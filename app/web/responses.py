"""One response shape for the whole Mini App API.

Success: ``{"ok": true, "data": {...}}``
Failure: ``{"ok": false, "error": "...", "code": "..."}``
"""

from __future__ import annotations

from typing import Any

from aiohttp import web


class ApiError(Exception):
    """An error that must reach the client as the standard envelope."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "error",
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.data = data

    def response(self) -> web.Response:
        return fail(self.message, status=self.status, code=self.code, data=self.data)


def ok(data: Any = None, status: int = 200) -> web.Response:
    """A successful response."""
    payload: dict[str, Any] = {"ok": True}
    if data is not None:
        payload["data"] = data
    return web.json_response(
        payload, status=status, dumps=lambda o: _dumps(o)
    )


def fail(
    message: str,
    status: int = 400,
    code: str = "error",
    data: Any = None,
) -> web.Response:
    """A failed response."""
    payload: dict[str, Any] = {"ok": False, "error": message, "code": code}
    if data is not None:
        payload["data"] = data
    return web.json_response(
        payload, status=status, dumps=lambda o: _dumps(o)
    )


def unauthorized(message: str = "دسترسی نامعتبر است.") -> web.Response:
    return fail(message, status=401, code="unauthorized")


def forbidden(message: str = "دسترسی ندارید.") -> web.Response:
    return fail(message, status=403, code="forbidden")


def not_found(message: str = "پیدا نشد.") -> web.Response:
    return fail(message, status=404, code="not_found")


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "too_many_requests",
    500: "server_error",
    503: "unavailable",
}


def error_response(exc: web.HTTPException) -> web.Response:
    """Turn an HTTP exception into the standard envelope."""
    status = int(exc.status)
    message = exc.reason or "خطای نامشخص."
    return fail(message, status=status, code=_STATUS_CODES.get(status, "error"))


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
