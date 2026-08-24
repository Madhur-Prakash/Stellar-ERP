"""S3-compatible object storage for uploaded documents - the **optional** backend.

**Not the default.** Documents live in PostgreSQL unless S3-compatible credentials are
configured; see :mod:`app.modules.ocr.storage` for why the database is the default and what
its limits are. This module exists for the deployment that has outgrown those limits - blobs
dominating dump time and WAL volume, somewhere past tens of gigabytes.

Selecting it takes two things, and forgetting either is a distinct, visible failure:

1. ``MINIO_ENDPOINT``, ``MINIO_ACCESS_KEY`` and ``MINIO_SECRET_KEY``, all three.
2. Something at that endpoint. The development stack has a MinIO behind the ``objectstore``
   compose profile - ``make up-objectstore`` - deliberately not started by a plain
   ``docker compose up``, because the container should not be running unless this backend was
   chosen.

The client library ships in the base dependencies, so it is present either way. That is
convenience, not selection: this module is still imported *lazily* from
:func:`app.modules.ocr.storage.document_store`, so the default path never loads it, and a
build that trims ``minio`` out of the base list gets a clear
``object_storage_unavailable`` error instead of an ``ImportError`` from inside a handler.

Same interface as :class:`~app.modules.ocr.storage.DocumentStore`, so nothing above it knows
which one it is talking to.

**S3-compatible rather than tied to one vendor.** The same code addresses MinIO on the
operator's own machine or real S3 - which is the point for a product whose premise is that
you host it yourself. Nothing here depends on a provider-specific feature.

**Objects are private, and the bytes come back exactly as they went in.** No transformation
pipeline, no re-encoding, no format parsing: a blob is addressed by the SHA-256 of its own
bytes and verified against that on read, so anything that rewrote a single byte would turn
every read into a corruption error. Object storage is the right shape for that; a media CDN
is not, which was worth learning the hard way.

**No compression here, unlike the database backend.** The blob goes to the bucket verbatim.
Object storage is billed and sized per byte stored, so compressing would help - but it would
also mean the object at a key is no longer the document at that key, which breaks every
external tool an operator might reasonably point at the bucket, including the one they use to
verify the backup. In the database that trade is worth it because nothing else reads the
column; in a bucket it is not.

**The client is synchronous, so every call runs in a worker thread.** A 15 MB upload on the
event loop stalls every concurrent request.

A note on durability: this backend needs storage the operator mounts and backs up themselves.
That is the cost of moving blobs out of the database - the single consistent ``pg_dump`` stops
covering them, and a restore reinstates rows pointing at objects from a different moment.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Final

import anyio.to_thread
from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.ocr.storage import (
    BlobCorruptedError,
    BlobMetadata,
    BlobMissingError,
    StorageError,
    sha256_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger(__name__)

#: S3 error codes that mean "the object is not there", as opposed to a real fault.
MISSING_CODES: Final[frozenset[str]] = frozenset({"NoSuchKey", "NoSuchObject", "NotFound"})


class ObjectDocumentStore:
    """Reads and writes document blobs in an S3-compatible bucket."""

    def __init__(self) -> None:
        self._client: Minio | None = None
        self._bucket_ready = False

    # -----------------------------------------------------------------------
    # Client
    # -----------------------------------------------------------------------
    def _connect(self) -> Minio:
        """The client, built once per store instance.

        Built lazily rather than in ``__init__`` so constructing the store - which happens
        on every request through the service - costs nothing until a blob is actually
        touched.
        """
        if self._client is None:
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=settings.minio_secure,
            )
        return self._client

    def _ensure_bucket(self, client: Minio) -> None:
        """Create the bucket if this deployment has not got one yet.

        MinIO starts empty, so a fresh stack has no bucket and the first upload would fail
        on something the operator has no reason to have done by hand. Checked once per store
        instance: the call is cheap, but not free, and it cannot change mid-request.

        The bucket is left with default (private) access. Making it public would expose
        every invoice to anyone who guessed a URL.
        """
        if self._bucket_ready:
            return
        try:
            if not client.bucket_exists(settings.minio_bucket):
                client.make_bucket(settings.minio_bucket)
                log.info("created the document bucket", extra={"bucket": settings.minio_bucket})
        except S3Error as exc:
            log.error(
                "could not reach the object store",
                extra={"bucket": settings.minio_bucket, "error": str(exc)},
            )
            raise StorageError from exc
        self._bucket_ready = True

    async def _in_thread[T](self, work: Callable[[Minio], T]) -> T:
        """Run one client call off the event loop, with the bucket guaranteed to exist."""

        def run() -> T:
            client = self._connect()
            self._ensure_bucket(client)
            return work(client)

        return await anyio.to_thread.run_sync(run)

    # -----------------------------------------------------------------------
    # Operations
    # -----------------------------------------------------------------------
    async def write(self, relative: str, data: bytes, meta: BlobMetadata) -> None:
        """Store bytes under ``relative``.

        Content-addressed, so an object already at this key holds these exact bytes and
        re-uploading would be pure cost. Checked first for that reason, not for safety -
        overwriting would be harmless, just wasteful.

        ``meta`` is accepted to satisfy the shared contract and mostly unused: a bucket has
        nowhere to put an uploader id, and the key already encodes the format. Only the MIME
        type is recorded, as object metadata, and only so a human browsing the bucket sees a
        PDF as a PDF - the application always trusts the type it sniffed at upload, which is
        on the document row.
        """

        def work(client: Minio) -> None:
            try:
                client.stat_object(settings.minio_bucket, relative)
                return
            except S3Error as exc:
                if exc.code not in MISSING_CODES:
                    log.error(
                        "object store lookup failed before write",
                        extra={"key": relative, "code": exc.code},
                    )
                    raise StorageError from exc

            try:
                client.put_object(
                    settings.minio_bucket,
                    relative,
                    io.BytesIO(data),
                    length=len(data),
                    content_type=meta.mime_type or _content_type(relative),
                )
            except S3Error as exc:
                log.error("object store write failed", extra={"key": relative, "code": exc.code})
                raise StorageError from exc

        await self._in_thread(work)

    async def read(self, relative: str, *, verify: str | None = None) -> bytes:
        """Fetch a blob, optionally checking it against its expected digest.

        Verification matters more here than on a local disk, not less: the bytes have
        crossed a network and been held by another process, so "are these the bytes we
        stored" stops being a question only about disk corruption.
        """

        def work(client: Minio) -> bytes:
            response = None
            try:
                response = client.get_object(settings.minio_bucket, relative)
                return bytes(response.read())
            except S3Error as exc:
                if exc.code in MISSING_CODES:
                    raise BlobMissingError from exc
                log.error("object store read failed", extra={"key": relative, "code": exc.code})
                raise StorageError from exc
            finally:
                # Both required by the SDK: the response holds a pooled connection that is
                # not returned until it is released, and leaking those exhausts the pool
                # after a few hundred reads.
                if response is not None:
                    response.close()
                    response.release_conn()

        data = await self._in_thread(work)

        if verify is not None and sha256_of(data) != verify:
            log.error("blob integrity check failed", extra={"key": relative})
            raise BlobCorruptedError

        return data

    async def exists(self, relative: str) -> bool:
        def work(client: Minio) -> bool:
            try:
                client.stat_object(settings.minio_bucket, relative)
            except S3Error as exc:
                if exc.code in MISSING_CODES:
                    return False
                log.error("object store lookup failed", extra={"key": relative, "code": exc.code})
                raise StorageError from exc
            return True

        return await self._in_thread(work)

    async def delete(self, relative: str) -> None:
        """Remove a blob.

        A delete of something already absent is success: the goal is that it is gone.
        """

        def work(client: Minio) -> None:
            try:
                client.remove_object(settings.minio_bucket, relative)
            except S3Error as exc:
                if exc.code in MISSING_CODES:
                    return
                log.error("object store delete failed", extra={"key": relative, "code": exc.code})
                raise StorageError from exc

        await self._in_thread(work)


def _content_type(relative: str) -> str:
    """The MIME type to record on the object, from the key's extension.

    Stored so anything browsing the bucket - the MinIO console, a sync tool - sees a PDF as
    a PDF. It is metadata only: the application always trusts the type it sniffed from the
    bytes at upload, which is recorded on the document row.
    """
    _, _, extension = relative.rpartition(".")
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "tif": "image/tiff",
        "tiff": "image/tiff",
        "webp": "image/webp",
    }.get(extension.lower(), "application/octet-stream")


__all__ = ["ObjectDocumentStore"]
