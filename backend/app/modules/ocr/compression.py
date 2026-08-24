"""Compression for stored document blobs.

**Lossless and byte-exact, always.** ``decompress(*compress(data)) == data`` for every
input, with no exceptions and no configuration that weakens it. That property is the whole
design, and it is worth stating why it is not negotiable here:

* A document in this system is *accounting evidence*. It supports a posted ledger entry
  that is immutable and correctable only by a reversal. The file an auditor downloads in
  three years has to be the file the supplier sent, not a re-rendered equivalent of it.
* :attr:`~app.modules.ocr.models.Document.sha256` is the digest of the **original upload**,
  and it does three jobs at once: the duplicate key, the storage address, and the integrity
  check applied on every download. Anything that rewrote a single byte would turn every
  read into :class:`~app.modules.ocr.storage.BlobCorruptedError`.

So this module does **not** rewrite PDFs. PDF-aware optimisation - re-encoding content
streams, downsampling embedded images, stripping metadata - produces a file that renders
identically and hashes differently. That is a reasonable trade for a media pipeline and the
wrong one for a books-of-account attachment, so the only transform applied is a general
lossless codec over the whole file.

**What that actually buys.** Measured on this project's own fixtures, so the numbers are
reproducible rather than folklore - but read the caveat under the table before quoting them:

=============================  ===========  ==========  =======  =======
Input                            Original      Stored    Saved    Codec
=============================  ===========  ==========  =======  =======
Text-layer PDF, ~10 pages           28,974       1,489    94.9%     zlib
Text-layer PDF, ~500 pages       1,430,578      54,501    96.2%     zlib
PNG render of an invoice            86,141      43,723    49.2%     zlib
JPEG scan of an invoice            234,288     218,043     6.9%     zlib
Entropy-coded bytes                500,004     500,004     0.0%     none
=============================  ===========  ==========  =======  =======

**The PDF figures flatter the codec and should not be read as typical.** The fixtures are
generated invoice lines with no image data and heavy repetition, which is the best case
zlib will ever see. A real supplier invoice is usually either (a) produced by a tool that
already Flate-compressed its own content streams, leaving 10-30%, or (b) a photograph of
paper, where the image is DCTDecode and there is essentially nothing left - the 6.9% line is
the honest expectation for most of what a small business actually uploads.

So the useful claim is not "documents compress" but "documents that compress are stored
compressed, and documents that do not are not made worse". :func:`compress` **stores
whichever representation is smaller** and records which one it chose. A blob under
:attr:`Codec.NONE` is not a failure - it is the correct answer, and it costs nothing on read.

**Compression runs in a worker thread.** The 1.4 MB case above takes ~9 ms and the 500 KB
entropy case ~22 ms (higher, because incompressible input is where zlib works hardest for
nothing), which puts a 15 MB upload in the low hundreds of milliseconds. On the event loop
that is the same hundreds of milliseconds of every other request being blocked. zlib releases
the GIL while it works, so a thread genuinely parallelises here rather than just moving the
stall somewhere less visible.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import anyio.to_thread

from app.core.exceptions import AppError
from app.core.logging import get_logger

log = get_logger(__name__)


class Codec(StrEnum):
    """How a stored blob's bytes were encoded.

    Recorded per blob rather than assumed globally, so changing the default codec - or
    adding one - never invalidates a byte already in the database. A row written under
    ``ZLIB`` keeps decoding as zlib forever, whatever this module later prefers.
    """

    #: Stored verbatim. Chosen when compression did not pay for itself.
    NONE = "none"
    #: zlib (RFC 1950). In the standard library, so no dependency, and its decoder is
    #: the most widely exercised piece of decompression code in existence.
    ZLIB = "zlib"


#: zlib level. 6 is the library default and the right point on the curve here.
#:
#: Levels 7-9 buy low single-digit percentages on this kind of input while costing roughly
#: twice the CPU - and on a partly-incompressible PDF, most of that extra work is spent
#: confirming there is nothing to find. Measured on the fixtures in
#: ``tests/test_document_storage.py``, 9 beat 6 by under 1%.
LEVEL: Final = 6

#: Don't bother compressing below this. A blob this small is one TOAST-free row either way,
#: and the codec's own header would eat most of the saving.
MIN_WORTH_TRYING: Final = 512

#: Keep the compressed form only if it saves at least this fraction.
#:
#: Not zero. Shaving 0.5% off a 12 MB scan still costs a full decompression pass on every
#: single download of it, forever - so a saving has to be worth the read-side price, not
#: merely positive.
MIN_SAVING_RATIO: Final = 0.05


class DecompressionError(AppError):
    """Stored bytes could not be decoded.

    A 500, and a serious one: it means the database holds a blob that cannot be turned back
    into the document it claims to be. The client did nothing wrong, and no retry will help.
    """

    code = "decompression_failed"
    message = "The stored document could not be decoded."


@dataclass(frozen=True, slots=True)
class Compressed:
    """The result of compressing one blob."""

    codec: Codec
    payload: bytes
    #: Length of the original input, so a caller can record it without holding the input.
    original_size: int

    @property
    def stored_size(self) -> int:
        return len(self.payload)

    @property
    def saving_ratio(self) -> float:
        """Fraction of the original size saved. ``0.0`` when stored verbatim."""
        if self.original_size == 0:
            return 0.0
        return 1 - (self.stored_size / self.original_size)


def compress_sync(data: bytes) -> Compressed:
    """Compress ``data``, or decide not to. Never raises.

    Synchronous, and exported for the migration script and for tests that want to assert on
    ratios without an event loop. Request paths should use :func:`compress`.
    """
    if len(data) < MIN_WORTH_TRYING:
        return Compressed(codec=Codec.NONE, payload=data, original_size=len(data))

    try:
        payload = zlib.compress(data, level=LEVEL)
    except zlib.error as exc:
        # Compressing cannot fail on valid input, so this is a memory or library problem.
        # Storing the original is strictly better than refusing the upload over it.
        log.error("compression failed - storing verbatim", extra={"error": str(exc)})
        return Compressed(codec=Codec.NONE, payload=data, original_size=len(data))

    if len(payload) > len(data) * (1 - MIN_SAVING_RATIO):
        return Compressed(codec=Codec.NONE, payload=data, original_size=len(data))

    return Compressed(codec=Codec.ZLIB, payload=payload, original_size=len(data))


async def compress(data: bytes) -> Compressed:
    """Compress ``data`` off the event loop. See the module docstring."""
    return await anyio.to_thread.run_sync(compress_sync, data)


def decompress_sync(codec: Codec | str, payload: bytes) -> bytes:
    """Restore the original bytes. Raises :class:`DecompressionError` on bad input.

    Accepts the codec as a plain string as well as the enum, because it arrives from a
    database column and a value written by a future version of this module should produce a
    clear "unknown codec" error rather than a ``ValueError`` from enum coercion.
    """
    if isinstance(codec, str) and not isinstance(codec, Codec):
        try:
            codec = Codec(codec)
        except ValueError as exc:
            raise DecompressionError(
                f"Unknown compression codec {codec!r} - written by a newer version?"
            ) from exc

    if codec is Codec.NONE:
        return payload

    try:
        return zlib.decompress(payload)
    except zlib.error as exc:
        log.error("decompression failed", extra={"codec": str(codec), "bytes": len(payload)})
        raise DecompressionError from exc


async def decompress(codec: Codec | str, payload: bytes) -> bytes:
    """Restore the original bytes, off the event loop."""
    return await anyio.to_thread.run_sync(decompress_sync, codec, payload)


__all__ = [
    "LEVEL",
    "MIN_SAVING_RATIO",
    "MIN_WORTH_TRYING",
    "Codec",
    "Compressed",
    "DecompressionError",
    "compress",
    "compress_sync",
    "decompress",
    "decompress_sync",
]
