"""Fail-closed Basic authentication helpers for local web tools.

Framework imports are deferred into the factory functions: vendored copies run
in per-tool venvs that install only Flask or only FastAPI, so importing both at
module level would break whichever tool lacks the other framework.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from collections.abc import Callable


def _credentials(authorization: str) -> tuple[str, str]:
    """Decode a Basic header, returning empty credentials when it is invalid."""
    if not authorization.startswith("Basic "):
        return "", ""
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
        return username, password
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return "", ""


def _matches_expected(authorization: str) -> bool:
    expected_password = os.getenv("BASIC_AUTH_PASSWORD")
    if not expected_password:
        return False

    expected_username = os.getenv("BASIC_AUTH_USER") or "admin"
    provided_username, provided_password = _credentials(authorization)
    username_matches = secrets.compare_digest(
        provided_username.encode("utf-8"), expected_username.encode("utf-8")
    )
    password_matches = secrets.compare_digest(
        provided_password.encode("utf-8"), expected_password.encode("utf-8")
    )
    return username_matches and password_matches


def create_basic_auth_middleware(realm: str):
    """Return FastAPI middleware protecting every path except ``/healthz``."""
    from fastapi.responses import Response as FastAPIResponse

    async def basic_auth_middleware(request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)

        # Credential parsing is completed before the handler is called. Handler
        # exceptions must remain their original 4xx/5xx responses.
        if not _matches_expected(request.headers.get("Authorization", "")):
            return FastAPIResponse(
                status_code=401,
                headers={"WWW-Authenticate": f'Basic realm="{realm}"'},
                content="Unauthorized",
            )
        return await call_next(request)

    return basic_auth_middleware


def create_basic_auth_before_request(realm: str) -> Callable[[], "FlaskResponse | None"]:
    """Return a Flask ``before_request`` protecting all paths but ``/healthz``."""
    from flask import Response as FlaskResponse
    from flask import request

    def basic_auth_before_request() -> FlaskResponse | None:
        if request.path == "/healthz":
            return None
        if _matches_expected(request.headers.get("Authorization", "")):
            return None
        return FlaskResponse(
            "Unauthorized",
            status=401,
            headers={"WWW-Authenticate": f'Basic realm="{realm}"'},
        )

    return basic_auth_before_request
