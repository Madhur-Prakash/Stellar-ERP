"""Blob storage for uploaded documents: the contract, and the default backend.

**PostgreSQL is the default and the only backend a deployment needs.** Document bytes are
compressed and written to :class:`~app.modules.ocr.models.DocumentBlob` as ``BYTEA``, in the
same transaction as the row that describes them. S3-compatible object storage remains
available for an install that has outgrown that, and is used only when its credentials are
configured - see :func:`document_store`.

Storing blobs in the database is the unfashionable choice, so the reasoning is worth
recording rather than leaving to be re-litigated:

* **One backup, and it is consistent.** ``pg_dump`` now captures the books *and* their
  supporting evidence at one point in time. Under a split store, a restore reinstates rows
  pointing at blobs from a different moment - and the failure appears months later, as an
  invoice that cannot be produced for the entry that cites it.
* **One transaction.** A blob written by a request that then rolls back leaves no orphan,
  because it rolls back too. Under a filesystem or a bucket the write is not in the
  transaction, so every failed upload leaked bytes nobody would ever look for again.
* **No second service to run, secure, and back up.** The premise of this product is that one
  person can operate it on a small VPS. MinIO is another daemon, another credential, another
  volume to remember to mount - and forgetting the volume, which the old development compose
  file did, silently made document storage ephemeral.
* **It is genuinely bounded.** Scanned invoices for a small business are megabytes a month.
  The read path never touches the bytes unless a download asks for them (see
  :class:`~app.modules.ocr.models.DocumentBlob` on why they live in their own table), so the
  cost is disk, not query latency.

The honest limit: this is the wrong choice past roughly tens of gigabytes, where the blobs
start dominating dump time and WAL volume. That is what the object backend is still there
for, and :mod:`scripts.migrate_document_blobs` moves between the two in either direction.

**Content-addressed.** A blob's key is derived from the SHA-256 of its own bytes and nothing
else. Three things follow for free: the key is never attacker-controlled (a filename joined
onto a path is how ``../../../etc/authorized_keys`` becomes a write target, and no amount of
sanitising beats not doing it); identical uploads cannot occupy two blobs; and the key is a
checksum, so corruption is detectable on read.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.modules.ocr.compression import compress, decompress
from app.modules.ocr.engines import DocumentFormat, DocumentTooLargeError

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

#: Read size when streaming an upload. 64 KiB is a good balance: large enough that
#: syscall overhead disappears, small enough that the limit check below rejects an
#: oversized body long before it is all in memory.
CHUNK_SIZE: Final = 64 * 1024


class StorageError(AppError):
    """The blob could not be written or read.

    A 500, not a 4xx: the client did nothing wrong, and a database or object store that
    cannot hold a document is an operator problem that should page someone rather than look
    like a bad request.
    """

    code = "storage_error"
    message = "The document could not be stored."


class BlobMissingError(StorageError):
    """The row referencing this blob exists, but the bytes do not.

    410, not 404: the document is real and the client's reference to it is correct - the
    content is gone. That distinction is what tells a user "this was removed from storage"
    rather than "you asked for the wrong thing".
    """

    code = "blob_missing"
    status_code = 410
    message = "The stored file is missing. It may have been removed from storage."


class BlobCorruptedError(StorageError):
    """A blob's bytes no longer hash to its address."""

    code = "blob_corrupted"
    message = "The stored document failed its integrity check."


def sha256_of(data: bytes) -> str:
    """Lowercase hex SHA-256."""
    return hashlib.sha256(data).hexdigest()


def relative_path_for(organization_id: object, digest: str, fmt: DocumentFormat) -> str:
    """The storage key for a blob.

    Named ``relative_path_for`` because that is what it was under the filesystem backend and
    every stored ``storage_path`` was produced by it; the shape has to stay stable or old
    rows stop resolving.

    Sharded on the first two hex characters. Pointless for a database backend, where the key
    is just an indexed string - but the same key has to address an object in a bucket, and
    there a flat prefix of a hundred thousand objects makes ``ls`` unusable for the operator
    who has to look. Organization-first so a tenant's documents can be exported, audited, or
    removed with one prefix.
    """
    return f"{organization_id}/{digest[:2]}/{digest}.{fmt.extension}"


class BlobMetadata(Protocol):
    """What a backend may record alongside the bytes.

    A protocol rather than a concrete type so the object backend - which has nowhere to put
    most of it - can accept the argument and use only the MIME type, without either backend
    importing the other's notion of a document.
    """

    @property
    def original_filename(self) -> str: ...
    @property
    def mime_type(self) -> str: ...
    @property
    def uploaded_by_user_id(self) -> uuid.UUID | None: ...


class DocumentStore(Protocol):
    """The storage contract, implemented by the database and object backends.

    Explicit rather than implied, because there are two implementations and the interface is
    the only thing keeping them substitutable. Anything above this layer talks to the
    protocol and cannot tell which one it has - the choice is made once, in
    :func:`document_store`.
    """

    async def write(self, key: str, data: bytes, meta: BlobMetadata) -> None: ...
    async def read(self, key: str, *, verify: str | None = None) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...


class DatabaseDocumentStore:
    """Stores document blobs in PostgreSQL, compressed.

    Holds the request's :class:`~sqlalchemy.ext.asyncio.AsyncSession`, which is what puts the
    blob inside the caller's transaction. That is the point of this backend, not an
    implementation detail: a rolled-back upload leaves nothing behind, and a committed one
    cannot have a row without its bytes.

    Note what this means for :meth:`write` - it *flushes*, it does not commit. Whether the
    blob survives is the caller's decision, made by the same commit that decides whether the
    :class:`~app.modules.ocr.models.Document` row survives.
    """

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self._session = session
        self._organization_id = organization_id

    async def write(self, key: str, data: bytes, meta: BlobMetadata) -> None:
        """Compress and store ``data`` under ``key``.

        Idempotent, because the key is content-addressed: a row already at this key holds
        these exact bytes, so re-writing would be pure cost. Checked first for that reason
        rather than for safety.
        """
        from app.modules.ocr.models import DocumentBlob

        if await self.exists(key):
            return

        compressed = await compress(data)

        blob = DocumentBlob(
            organization_id=self._organization_id,
            storage_key=key,
            sha256=sha256_of(data),
            original_filename=meta.original_filename[:255] or "upload",
            mime_type=meta.mime_type,
            original_size=compressed.original_size,
            compressed_size=compressed.stored_size,
            compression=compressed.codec,
            data=compressed.payload,
            uploaded_by_user_id=meta.uploaded_by_user_id,
        )
        self._session.add(blob)

        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Two concurrent uploads of identical bytes. The document service takes a
            # transaction-scoped advisory lock on `(organization, digest)` before it gets
            # here, so this is unreachable through the API - but a script or a future caller
            # without that lock would land here, and a failed flush poisons the session, so
            # it has to be reported rather than swallowed.
            log.warning("blob insert conflicted", extra={"key": key, "error": str(exc.orig)})
            raise StorageError from exc

        log.info(
            "document blob stored",
            extra={
                "key": key,
                "codec": str(compressed.codec),
                "original_bytes": compressed.original_size,
                "stored_bytes": compressed.stored_size,
                "saved_pct": round(compressed.saving_ratio * 100, 1),
            },
        )

    async def read(self, key: str, *, verify: str | None = None) -> bytes:
        """Fetch and decompress a blob, optionally checking it against a digest.

        Verification is opt-in rather than automatic: it costs a full hash pass, worth paying
        when the bytes are about to be shown to a user as the evidence behind a ledger entry
        and not worth paying on the internal re-read that feeds recognition.
        """
        from app.modules.ocr.models import DocumentBlob

        query = select(DocumentBlob.compression, DocumentBlob.data).where(
            DocumentBlob.organization_id == self._organization_id,
            DocumentBlob.storage_key == key,
        )
        row = (await self._session.execute(query)).one_or_none()
        if row is None:
            raise BlobMissingError

        codec, payload = row
        data = await decompress(codec, payload)

        if verify is not None and sha256_of(data) != verify:
            log.error("blob integrity check failed", extra={"key": key})
            raise BlobCorruptedError

        return data

    async def exists(self, key: str) -> bool:
        """Whether a blob is stored under ``key``.

        Selects the primary key, not the row: ``SELECT data`` here would pull megabytes
        across the wire to answer a yes/no question.
        """
        from app.modules.ocr.models import DocumentBlob

        query = select(DocumentBlob.id).where(
            DocumentBlob.organization_id == self._organization_id,
            DocumentBlob.storage_key == key,
        )
        return (await self._session.execute(query)).scalar_one_or_none() is not None

    async def delete(self, key: str) -> None:
        """Remove a blob.

        Only for a hard purge. Soft-deleting a :class:`~app.modules.ocr.models.Document`
        deliberately leaves its blob alone - a document that turned into a posted bill is the
        evidence for a ledger entry, and destroying it because someone tidied the review
        queue would leave the books unsupportable.
        """
        from app.modules.ocr.models import DocumentBlob

        blob = (
            await self._session.execute(
                select(DocumentBlob).where(
                    DocumentBlob.organization_id == self._organization_id,
                    DocumentBlob.storage_key == key,
                )
            )
        ).scalar_one_or_none()
        if blob is not None:
            await self._session.delete(blob)
            await self._session.flush()


async def read_within_limit(stream: object, *, limit: int | None = None) -> bytes:
    """Read an upload, aborting as soon as it exceeds the limit.

    **Chunked, not ``await file.read()``.** Reading the whole body first and checking its
    length afterwards means a 2 GB upload is a 2 GB allocation before the check runs - the
    size limit becomes a way to *report* the memory exhaustion it was supposed to prevent.
    Stopping at the first chunk that crosses the line caps the damage at one chunk.
    """
    ceiling = limit if limit is not None else settings.max_upload_bytes
    read = getattr(stream, "read", None)
    if read is None:  # pragma: no cover - guarded by the router's typing
        raise StorageError("Upload stream is not readable")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > ceiling:
            raise DocumentTooLargeError(
                f"That file is larger than the {ceiling // (1024 * 1024)} MB limit.",
                details={"max_bytes": ceiling},
            )
        chunks.append(chunk)

    return b"".join(chunks)


def document_store(session: AsyncSession, organization_id: uuid.UUID) -> DocumentStore:
    """The blob backend this deployment uses.

    Chosen here and nowhere else, so no caller has to know or care. **PostgreSQL unless
    S3-compatible credentials are configured** - see
    :attr:`app.core.config.Settings.document_storage` for why that is derived from the
    credentials rather than set by a separate switch, and the module docstring for why the
    database is the default.

    The object backend is imported lazily, and that is load-bearing rather than tidy: the
    default path must not pay for - or depend on - a client it never uses.

    The missing-dependency case is caught and named because the bare version of it is
    actively misleading. A build that trims ``minio`` out of the dependency list while three
    ``MINIO_*`` variables are still sitting in a ``.env`` would otherwise fail its first
    upload with a ``ModuleNotFoundError`` raised from inside a request handler - a message
    that names neither cause and neither of the two ways out.
    """
    if settings.document_storage == "object":
        try:
            from app.modules.ocr.object_storage import ObjectDocumentStore
        except ImportError as exc:
            log.error(
                "object storage is configured but its client is not installed",
                extra={"endpoint": settings.minio_endpoint, "bucket": settings.minio_bucket},
            )
            raise StorageError(
                "Object storage is configured (MINIO_ENDPOINT is set) but the client library "
                "is not installed. Either install it with `uv sync --extra objectstore`, or "
                "clear MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY to store "
                "documents in PostgreSQL, which is the default.",
                code="object_storage_unavailable",
            ) from exc

        return ObjectDocumentStore()
    return DatabaseDocumentStore(session, organization_id)


__all__ = [
    "CHUNK_SIZE",
    "BlobCorruptedError",
    "BlobMetadata",
    "BlobMissingError",
    "DatabaseDocumentStore",
    "DocumentStore",
    "StorageError",
    "document_store",
    "read_within_limit",
    "relative_path_for",
    "sha256_of",
]
