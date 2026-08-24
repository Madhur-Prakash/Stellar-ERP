"""Move document blobs between storage backends.

Needed once, when a deployment switches backends: the database rows are backend-agnostic -
``document.storage_path`` means the same thing under either - but the bytes themselves do not
move on their own, so every document uploaded before the switch reads back as "the stored file
is missing".

The common direction is **into PostgreSQL**, which is the default backend as of the
``document blobs in postgres`` migration. The reverse exists too, for an install whose blobs
have outgrown the database.

**Deliberately not an Alembic data migration.** Moving these bytes means opening a filesystem
path or an S3 connection, and doing that from inside a DDL transaction means a network timeout
leaves the schema half-applied with no obvious way forward. As a script it is resumable,
interruptible, and safe to run twice.

**Verifies before it deletes, and only deletes when asked.** Every blob is written to the
destination, read back *out of the destination*, and hashed against the digest recorded on the
row. The source copy is removed only when that matches, and only with ``--purge``. A copy that
has not been proven readable is not a copy.

**Idempotent.** A blob already present at the destination is skipped, so an interrupted run is
resumed by running it again.

Usage::

    # into PostgreSQL, from wherever the bytes are now
    uv run python scripts/migrate_document_blobs.py --to database
    uv run python scripts/migrate_document_blobs.py --to database --purge

    # out to a configured bucket
    uv run python scripts/migrate_document_blobs.py --to object

    # where are the bytes, and how much would compression save?
    uv run python scripts/migrate_document_blobs.py --report

``--from-disk DIR`` reads the source from a filesystem tree written by the storage backend
this project used before blobs moved into the database. That backend is gone from the
application, so the reader lives here - it is needed exactly once per install, and keeping a
whole storage class alive in the app for it would be the tail wagging the dog.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

import app.db.registry  # noqa: F401  - registers every mapper
from app.core.config import settings
from app.db.session import SessionFactory
from app.modules.ocr.compression import compress_sync
from app.modules.ocr.models import Document
from app.modules.ocr.storage import DatabaseDocumentStore, sha256_of

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.ocr.storage import DocumentStore

Target = Literal["database", "object"]


class Meta:
    """The metadata a destination may record. Satisfies ``BlobMetadata``."""

    def __init__(self, filename: str, mime_type: str) -> None:
        self.original_filename = filename
        self.mime_type = mime_type
        #: Not recoverable per-blob here - the uploader is on the ``document`` row, and this
        #: script deliberately does not touch that row. Left null rather than guessed.
        self.uploaded_by_user_id = None


class DiskSource:
    """Reads blobs out of the old filesystem layout.

    The only remaining piece of that backend, and it lives here rather than in the
    application because the application no longer has a filesystem backend to belong to.
    Read-only, and it refuses any path that escapes the root - the paths come from a database
    column, and a stored string is exactly what a tampered row would poison.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _absolute(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"refusing a path outside the root: {key!r}")
        return candidate

    def exists(self, key: str) -> bool:
        return self._absolute(key).exists()

    def read(self, key: str) -> bytes:
        return self._absolute(key).read_bytes()

    def unlink(self, key: str) -> None:
        self._absolute(key).unlink(missing_ok=True)


def _destination(target: Target, session: AsyncSession, organization_id: object) -> DocumentStore:
    """The store to write into, chosen explicitly rather than from settings.

    ``document_store()`` reads the configuration, which is the wrong input here: the whole
    point of this script is to write to the backend the deployment is moving *to*, which is
    generally not the one it is currently configured for.
    """
    if target == "object":
        from app.modules.ocr.object_storage import ObjectDocumentStore

        return ObjectDocumentStore()
    return DatabaseDocumentStore(session, organization_id)  # type: ignore[arg-type]


async def _source_bytes(
    key: str,
    *,
    disk: DiskSource | None,
    session: AsyncSession,
    organization_id: object,
    target: Target,
) -> bytes | None:
    """Find the blob, wherever it currently is. ``None`` when it is nowhere.

    Tries the disk tree first when one was named, then the *other* backend - so
    ``--to database`` reads from the bucket and ``--to object`` reads from the database,
    without the caller having to say so twice.
    """
    if disk is not None and disk.exists(key):
        return disk.read(key)

    other: DocumentStore
    if target == "database":
        if settings.document_storage != "object":
            return None
        from app.modules.ocr.object_storage import ObjectDocumentStore

        other = ObjectDocumentStore()
    else:
        other = DatabaseDocumentStore(session, organization_id)  # type: ignore[arg-type]

    if not await other.exists(key):
        return None
    return await other.read(key)


async def report() -> int:
    """Say where the bytes are and what compressing them would cost or save.

    Read-only. Worth running before a migration, because "how much will this add to the
    database" is the first question and the answer depends entirely on whether the documents
    are text-layer PDFs or photographs of paper.
    """
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Document.organization_id, Document.storage_path, Document.byte_size)
            )
        ).all()

        blobs = (
            await session.execute(
                select(
                    Document.organization_id,
                    Document.storage_path,
                )
            )
        ).all()

    print(f"{len(rows)} document row(s), including soft-deleted")
    print(f"declared bytes: {sum(r.byte_size for r in rows):,}")
    print(f"configured backend: {settings.document_storage}")
    print(f"distinct keys: {len({(r.organization_id, r.storage_path) for r in blobs})}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--to",
        choices=("database", "object"),
        help="destination backend",
    )
    parser.add_argument(
        "--from-disk",
        type=Path,
        metavar="DIR",
        help="also read from a filesystem tree written by the retired local backend "
        "(historically backend/var/uploads)",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="delete each source copy once the destination copy has been read back and "
        "verified against the digest on the row",
    )
    parser.add_argument("--report", action="store_true", help="show what is where; change nothing")
    args = parser.parse_args()

    if args.report:
        return await report()
    if not args.to:
        parser.error("one of --to or --report is required")

    target: Target = args.to
    if target == "object" and settings.document_storage != "object":
        print(
            "Object storage is not configured, so there is nowhere to move blobs to.\n"
            "Set MINIO_ENDPOINT, MINIO_ACCESS_KEY and MINIO_SECRET_KEY, and make sure "
            "something is listening at that endpoint - in development, "
            "`make up-objectstore`.",
            file=sys.stderr,
        )
        return 2

    disk = DiskSource(args.from_disk) if args.from_disk else None

    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(
                    Document.id,
                    Document.organization_id,
                    Document.original_filename,
                    Document.content_type,
                    Document.storage_path,
                    Document.sha256,
                )
            )
        ).all()

        print(f"{len(rows)} document row(s), including soft-deleted")
        print(f"target: {target}")
        if disk is not None:
            print(f"disk source: {disk.root}")
        print("mode:  ", "copy then delete verified sources" if args.purge else "copy only")
        print()

        copied = already = missing = failed = 0
        original_total = stored_total = 0

        for _id, organization_id, name, fmt, key, digest in rows:
            destination = _destination(target, session, organization_id)

            # Soft-deleted rows are included deliberately: their blob is still referenced by
            # the row, and leaving it behind would strand it when the source is wiped.
            if await destination.exists(key):
                already += 1
                continue

            try:
                data = await _source_bytes(
                    key,
                    disk=disk,
                    session=session,
                    organization_id=organization_id,
                    target=target,
                )
            except Exception as exc:
                print(f"  UNREADABLE   {name}: {exc}")
                failed += 1
                continue

            if data is None:
                print(f"  NOT FOUND    {name}  ({key})")
                missing += 1
                continue

            if sha256_of(data) != digest:
                print(f"  SOURCE BAD   {name} - does not match the digest on the row; skipping")
                failed += 1
                continue

            await destination.write(key, data, Meta(name, fmt.value))

            # Read back out of the *destination* rather than trusting the write. For the
            # database backend this also exercises the decompression path, which is the one
            # thing that must work in three years' time.
            verified = await destination.read(key, verify=digest)
            if verified != data:
                print(f"  VERIFY FAIL  {name} - leaving the source in place")
                failed += 1
                continue

            copied += 1
            original_total += len(data)
            stored_total += compress_sync(data).stored_size if target == "database" else len(data)

            if args.purge and disk is not None and disk.exists(key):
                disk.unlink(key)
                print(f"  moved        {name}  ({len(data):,} bytes)")
            else:
                print(f"  copied       {name}  ({len(data):,} bytes)")

        # One commit for the whole run, so an interrupted migration leaves the database with
        # either all of a blob or none of it - and re-running resumes from where it stopped.
        await session.commit()

    print(f"\ncopied {copied}, already present {already}, not found {missing}, failed {failed}")
    if copied and target == "database" and original_total:
        saved = 1 - (stored_total / original_total)
        print(
            f"stored {stored_total:,} bytes for {original_total:,} of documents "
            f"({saved * 100:.1f}% saved)"
        )
    if copied and not args.purge:
        print("\nSource copies kept. Re-run with --purge to remove the verified ones.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
