# =============================================================================
# Stellar ERP - backend image
#
# Multi-stage, with `development` and `production` targets sharing one dependency
# layer. Compose selects the target; CI builds `production`.
# =============================================================================

# ---- Base: interpreter + uv, no application code ----------------------------
FROM python:3.13-slim-bookworm AS base

# uv is copied from its official distroless image rather than pip-installed:
# a pinned binary, no build toolchain, and no chance of it perturbing the
# application's own dependency resolution.
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Copy packages into the venv instead of hardlinking: the cache mount and
    # the venv are on different filesystems, where hardlinks fail.
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

# curl is for the container healthcheck; libpq5 is asyncpg's runtime library.
#
# `tesseract-ocr` is the binary `pytesseract` shells out to. The wheel is already a
# core dependency and installs from the lockfile below, but it is a subprocess wrapper
# and nothing more: without this package the image imports it happily and fails on the
# first scanned upload with `TesseractNotFoundError` - which is precisely the failure
# `app/modules/ocr/engines.py` probes for rather than assumes away.
#
# The language data is named explicitly. `tesseract-ocr` depends on `-eng`, so it
# arrives either way, but writing it down is what ties the image to `OCR_LANGUAGES`:
# a language named in that setting and missing from here fails recognition outright
# rather than degrading, so `eng+hin` means adding `tesseract-ocr-hin` on this line.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Where apt put the binary. Not strictly required - it is on `PATH`, which is what the
# blank default in `Settings.tesseract_cmd` means - but the same `.env` is read by a
# developer's Windows machine and by this container, and there the setting has to hold
# `C:/Program Files/Tesseract-OCR/tesseract.exe`. Handing that path to a Linux
# container is the "installed but unreachable" case the setting exists to fix, pointed
# the wrong way. Compose's `env_file` still beats an image `ENV`, so both compose files
# also pin this in the backend's `environment:` block, next to POSTGRES_HOST.
ENV TESSERACT_CMD=/usr/bin/tesseract

WORKDIR /app

# ---- Dependencies -----------------------------------------------------------
# Manifests only, before the source: dependencies change far less often than
# code, so this layer stays cached across almost every rebuild.
FROM base AS deps

COPY pyproject.toml uv.lock ./

# `--frozen` fails if uv.lock is out of date with pyproject.toml, so a
# forgotten lockfile update breaks the build instead of silently installing
# different versions than were tested.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---- Development ------------------------------------------------------------
FROM deps AS development

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY . .

EXPOSE 8000

# `--reload` watches the bind-mounted source.
#
# `--no-proxy-headers`, deliberately. The application resolves the client address
# itself, in `app.core.net.client_ip`, by counting `X-Forwarded-For` hops from the
# right - the end our own proxy appends to. uvicorn's handling takes the *left-most*
# entry whenever `--forwarded-allow-ips` includes the peer, and the left-most entry is
# the one the caller writes: `curl -H 'X-Forwarded-For: 1.2.3.4'` and every IP-keyed
# control in the system is looking at a fabricated address. Leaving `scope["client"]`
# as the real socket peer keeps one unforgeable fact available underneath the
# resolution rule.
#
# ---- Why `uv sync` runs here and not only at build time -----------------------
#
# This is the fix for a real failure, not belt-and-braces.
#
# `docker-compose.yml` mounts a named volume over `/app/.venv` so the bind-mounted
# source cannot shadow the image's virtualenv. But Docker seeds a named volume from
# the image **only when it first creates the volume** - never again. So the moment a
# dependency is added, the rebuilt image carries it and the container does not: the
# old volume is mounted straight over the new virtualenv. It surfaces as
# `ModuleNotFoundError` for a package that is plainly in `pyproject.toml` and in
# `uv.lock`, which sends you looking everywhere except at the volume.
#
# Syncing on boot reconciles the mounted virtualenv with the lockfile every time.
# `--frozen` keeps it honest - it installs what the lock says and never resolves
# something new - and when nothing has changed it is a no-op that costs a moment.
CMD ["sh", "-c", "uv sync --frozen --no-install-project && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app --no-proxy-headers"]

# ---- Production -------------------------------------------------------------
FROM base AS production

# Runs unprivileged. A container process that does not need root should not have
# it - this is the cheapest possible mitigation for a container escape.
RUN groupadd --gid 1001 stellarerp \
    && useradd --uid 1001 --gid stellarerp --create-home --shell /bin/bash stellarerp

COPY --from=deps --chown=stellarerp:stellarerp /app/.venv /app/.venv

COPY --chown=stellarerp:stellarerp app ./app
COPY --chown=stellarerp:stellarerp migrations ./migrations
COPY --chown=stellarerp:stellarerp alembic.ini pyproject.toml README.md ./

# The operational scripts, not only the application.
#
# `migrate_document_blobs.py` is the documented recovery for a document whose bytes
# predate the database backend - the `blob_missing` 410 - and `check_schema_drift.py`
# answers "does the deployed schema still match the models", which is a question about
# production and can only be asked from inside it. A recovery procedure that exists
# solely on a developer's laptop is not a recovery procedure.
COPY --chown=stellarerp:stellarerp scripts ./scripts

# logifyx writes here; it must be writable by the unprivileged user.
RUN mkdir -p /app/logs && chown -R stellarerp:stellarerp /app/logs

USER stellarerp

EXPOSE 8000

# Hits the liveness probe, which deliberately touches no dependencies - a
# database blip must not cause the orchestrator to kill healthy containers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

# No `--reload` and no `--workers`: horizontal scaling is by container replica,
# which keeps every instance stateless and independently restartable. Set
# WEB_CONCURRENCY only if running a single container on a large host.
#
# `--no-proxy-headers` for the reason given on the development target above: the client
# address is resolved by the application, from the trustworthy end of the header.
#
# `--no-server-header` and `--no-date-header` keep the response minimal - the first
# because naming the server and its version is a free CVE shortlist, the second because
# nothing depends on it and every byte is on every response.
#
# `--h11-max-incomplete-event-size` caps the request line and headers at 16 KiB. Without
# a bound, a slowloris-style client can hold a connection open dribbling header bytes,
# and the default is generous enough to be worth tightening for an API whose largest
# legitimate header set is a bearer token.
#
# `--timeout-graceful-shutdown` bounds what happens on SIGTERM, which is every deploy.
# uvicorn's default is to wait indefinitely for in-flight requests: with `order:
# start-first` replacing one replica at a time, a single stuck connection stalls the
# rollout until Docker's own grace period expires and SIGKILLs the process mid-request.
# 25s is under the 30s `stop_grace_period` the compose file sets, so the ordering is
# always "finish the work, then exit" rather than "get killed".
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", \
     "--no-server-header", "--no-date-header", \
     "--h11-max-incomplete-event-size", "16384", \
     "--timeout-keep-alive", "65", \
     "--timeout-graceful-shutdown", "25"]
