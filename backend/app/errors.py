"""Application errors with stable machine-readable codes."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An error that is safe to show to the client."""

    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def to_body(self) -> dict:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def bad_request(message: str, code: str = "bad_request", details: Any = None) -> ApiError:
    return ApiError(400, code, message, details)


def conflict(message: str, code: str = "conflict", details: Any = None) -> ApiError:
    return ApiError(409, code, message, details)
