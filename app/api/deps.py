"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, status

from ..runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runtime not ready",
        )
    return runtime


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Enforce the optional shared API key on every /v1 endpoint.

    ``BGV_API_KEY`` empty means auth is disabled (the default). When set, the
    caller must send ``X-API-Key: <key>``. Comparison is constant-time.
    """
    expected = (getattr(request.app.state.settings, "api_key", "") or "").strip()
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
