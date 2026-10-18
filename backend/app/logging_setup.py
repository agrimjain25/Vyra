"""Structured (JSON-lines) logging that masks secrets."""
from __future__ import annotations

import json
import logging
import re
import sys
import time

_KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{12,}"),
    re.compile(r"(?i)(bearer\s+)[0-9A-Za-z_\-\.]{12,}"),
    re.compile(r"(?i)(x-goog-api-key|x-api-key|x-apikey|authorization)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"),
]


class Redactor(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s]

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        text = _KEY_PATTERNS[0].sub("***", text)
        text = _KEY_PATTERNS[1].sub("***", text)
        text = _KEY_PATTERNS[2].sub(r"\1***", text)
        text = _KEY_PATTERNS[3].sub(r"\1\2***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self.redact(record.getMessage())
            record.args = ()
        except Exception:  # never break logging
            pass
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(Redactor(secrets))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # httpx logs full request URLs at INFO (Gemma API keys are sent in a header, but be safe)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **ctx) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
