"""An httpx transport for the Stellar SDK's async Soroban server.

The SDK's own async client is built on ``aiohttp``, which it treats as an optional
extra. This backend already depends on ``httpx`` - it is the HTTP client every
other outbound call in the application uses - so pulling in a second async HTTP
stack for one subsystem would mean two connection pools, two timeout
configurations, two sets of proxy semantics, and two libraries to keep patched,
for no capability the first one lacks.

So this implements the SDK's :class:`~stellar_sdk.client.base_async_client.BaseAsyncClient`
against httpx instead. It is about sixty lines, and the alternative was a
transitive dependency tree.

``stream`` raises. It exists on the interface for Horizon's server-sent-event
endpoints, which this application never uses: the proof ledger polls for a
transaction result and reads contract state, both plain request/response. An
implementation nothing calls would be untested code sitting in the path of a
feature that matters, so it fails loudly rather than pretending.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx
from stellar_sdk.client.base_async_client import BaseAsyncClient
from stellar_sdk.client.response import Response
from stellar_sdk.exceptions import ConnectionError as StellarConnectionError

from app.core.logging import get_logger

log = get_logger(__name__)

#: Ceiling on a single RPC response.
#:
#: A Soroban RPC reply is a few kilobytes of base64 XDR. Anything approaching this
#: is a misrouted request answering with somebody's HTML error page, and buffering
#: it would be the only way this module could consume real memory.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class HttpxSorobanClient(BaseAsyncClient):
    """Minimal async client the SDK can drive, backed by httpx."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            # A modest pool: the seal worker makes a handful of calls per pass and
            # the verifier's reads go out from the browser, not from here.
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Content-Type": "application/json"},
            # Redirects are not followed. An RPC endpoint that redirects is an
            # endpoint that has moved or been intercepted, and silently following
            # it would mean signing transactions against a host nobody configured.
            follow_redirects=False,
        )

    async def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        max_content_size: int | None = None,
    ) -> Response:
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise StellarConnectionError(str(exc)) from exc
        self._guard_size(response, max_content_size)
        return _to_response(response)

    async def post(
        self,
        url: str,
        data: dict[str, str] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Response:
        try:
            response = await self._client.post(url, data=data, json=json_data)
        except httpx.HTTPError as exc:
            raise StellarConnectionError(str(exc)) from exc
        self._guard_size(response, None)
        return _to_response(response)

    def stream(
        self, url: str, params: dict[str, str] | None = None
    ) -> AsyncGenerator[dict[str, Any]]:
        raise NotImplementedError(
            "Server-sent event streaming is not implemented: the proof ledger only "
            "polls transaction results and reads contract state."
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _guard_size(response: httpx.Response, limit: int | None) -> None:
        ceiling = limit or MAX_RESPONSE_BYTES
        if len(response.content) > ceiling:
            raise StellarConnectionError(
                f"RPC response exceeded {ceiling} bytes, which no legitimate "
                "Soroban reply does - refusing to parse it"
            )


def _to_response(response: httpx.Response) -> Response:
    return Response(
        status_code=response.status_code,
        text=response.text,
        headers=dict(response.headers),
        url=str(response.url),
    )
