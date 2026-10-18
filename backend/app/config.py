"""Environment-based configuration.

All secrets come from environment variables (or a local ``backend/.env`` file that is never
committed). Nothing in here has a real key as a default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

PROVIDER_NAMES = ("amd", "gemma", "evolus")


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env reader (KEY=VALUE per line). Real environment variables always win."""
    path = path or (BACKEND_DIR / ".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _opt(name: str) -> str | None:
    v = os.environ.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _float(name: str) -> float | None:
    v = _opt(name)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        raise ConfigError(f"{name} must be a number (got {v!r}).") from None


def _int(name: str, default: int) -> int:
    v = _opt(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number (got {v!r}).") from None


class ConfigError(ValueError):
    pass


class Settings(BaseModel):
    # --- app -------------------------------------------------------------
    env: str = "development"
    database_path: Path = BACKEND_DIR / "data" / "vyra.db"
    media_dir: Path = BACKEND_DIR / "media"
    frontend_dir: Path = PROJECT_DIR
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    api_token: str | None = None  # optional shared token; when set, /api/* needs the X-Vyra-Token header
    max_body_bytes: int = 7 * 1024 * 1024
    max_json_bytes: int = 1024 * 1024
    max_upload_bytes: int = 5 * 1024 * 1024
    rate_limit_per_minute: int = 20
    log_level: str = "INFO"

    # --- LLM routing -----------------------------------------------------
    primary_llm: str = "amd"
    fallback_llm: str = "none"
    review_llm: str = ""  # empty = same chain as primary/fallback
    require_ai_review: bool = False
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    llm_concurrency: int = 2
    llm_max_output_tokens: int = 4096
    llm_temperature: float = 0.8

    # --- AMD-hosted OpenAI-compatible endpoint -----------------------------
    amd_base_url: str | None = None
    amd_api_key: str | None = None
    amd_model: str | None = None
    amd_endpoint_kind: Literal["token_factory", "developer_cloud_vllm", "other"] = "token_factory"
    amd_json_mode: Literal["json_object", "none"] = "json_object"
    amd_price_in_per_mtok: float | None = None
    amd_price_out_per_mtok: float | None = None

    # --- Gemma (Google DeepMind open model) --------------------------------
    # Default route: Gemma served by the Google AI Studio / Gemini API (same key as any AI Studio key, model id gemma-*).
    # Optional route: set GEMMA_OPENAI_BASE_URL to serve Gemma yourself (for example vLLM on AMD Developer Cloud).
    gemma_api_key: str | None = None
    gemma_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    gemma_model: str = "gemma-4-31b-it"
    gemma_price_in_per_mtok: float | None = None
    gemma_price_out_per_mtok: float | None = None
    gemma_openai_base_url: str | None = None
    gemma_openai_api_key: str | None = None

    # --- Images: Cloudflare (default). Pollinations is the automatic backup ---
    image_provider: Literal["cloudflare", "pollinations"] = "cloudflare"
    cf_account_id: str | None = None
    cf_api_token: str | None = None
    cf_base_url: str = "https://api.cloudflare.com/client/v4"
    cf_image_model: str = "@cf/black-forest-labs/flux-1-schnell"
    cf_image_cost_usd: float | None = None
    pollinations_api_key: str | None = None
    pollinations_base_url: str = "https://gen.pollinations.ai"
    pollinations_model: str = "flux"
    pollinations_image_cost_usd: float | None = None

    # --- Evolus (optional) ---------------------------------------------------
    evolus_base_url: str = "https://api.evolus.ai/v1"
    evolus_api_key: str | None = None
    evolus_app_id: str | None = None
    evolus_agent_id: str | None = None
    evolus_translator_agent_id: str | None = None  # optional second agent used as the translation fallback
    # Optional Evolus REST building block (AI Tool "Sentiment"). OFF unless EVOLUS_SENTIMENT=true.
    evolus_rest_base_url: str = "https://api.evolus.ai/api/v1"
    evolus_sentiment_enabled: bool = False
    evolus_sentiment_tool: str = "sentiment"
    evolus_tool_input_field: str = "text"
    evolus_tool_extra: dict | None = None

    # --- images ----------------------------------------------------------
    image_timeout_s: float = 150.0
    image_concurrency: int = 2
    image_max_per_day: int = 60

    @field_validator("primary_llm", "fallback_llm", "review_llm")
    @classmethod
    def _provider_name(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v == "google":  # old .env files: the Google fallback is now Gemma
            v = "gemma"
        if v in ("", "none"):
            return v
        if v not in PROVIDER_NAMES:
            raise ValueError(f"unknown provider {v!r}; choose one of {', '.join(PROVIDER_NAMES)} or 'none'")
        return v

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = _opt
        data: dict = {}

        def put(key: str, value):
            if value is not None:
                data[key] = value

        put("env", e("VYRA_ENV"))
        if e("DATABASE_PATH"):
            data["database_path"] = _resolve(e("DATABASE_PATH"))
        if e("MEDIA_DIR"):
            data["media_dir"] = _resolve(e("MEDIA_DIR"))
        if e("FRONTEND_DIR"):
            data["frontend_dir"] = _resolve(e("FRONTEND_DIR"))
        if e("CORS_ORIGINS"):
            data["cors_origins"] = [o.strip() for o in e("CORS_ORIGINS").split(",") if o.strip()]
        put("api_token", e("VYRA_API_TOKEN"))
        data["rate_limit_per_minute"] = _int("RATE_LIMIT_PER_MINUTE", 20)
        put("log_level", e("LOG_LEVEL"))
        put("primary_llm", e("PRIMARY_LLM_PROVIDER"))
        put("fallback_llm", e("FALLBACK_LLM_PROVIDER"))
        put("review_llm", e("REVIEW_LLM_PROVIDER"))
        data["require_ai_review"] = (e("REQUIRE_AI_REVIEW") or "false").lower() in ("1", "true", "yes")
        data["llm_max_retries"] = _int("LLM_MAX_RETRIES", 2)
        data["llm_concurrency"] = max(1, _int("LLM_CONCURRENCY", 2))
        data["llm_max_output_tokens"] = _int("LLM_MAX_OUTPUT_TOKENS", 4096)
        for env_name, key in (("LLM_TIMEOUT_SECONDS", "llm_timeout_s"), ("LLM_TEMPERATURE", "llm_temperature"),
                              ("IMAGE_TIMEOUT_SECONDS", "image_timeout_s")):
            put(key, _float(env_name))

        put("amd_base_url", (e("AMD_BASE_URL") or "").rstrip("/") or None)
        put("amd_api_key", e("AMD_API_KEY"))
        put("amd_model", e("AMD_MODEL"))
        put("amd_endpoint_kind", e("AMD_ENDPOINT_KIND"))
        put("amd_json_mode", e("AMD_JSON_MODE"))
        put("amd_price_in_per_mtok", _float("AMD_PRICE_INPUT_PER_MTOK_USD"))
        put("amd_price_out_per_mtok", _float("AMD_PRICE_OUTPUT_PER_MTOK_USD"))

        put("gemma_api_key", e("GEMMA_API_KEY") or e("GOOGLE_API_KEY") or e("GEMINI_API_KEY"))
        put("gemma_api_base", (e("GEMMA_API_BASE") or "").rstrip("/") or None)
        put("gemma_model", e("GEMMA_MODEL"))
        put("gemma_price_in_per_mtok", _float("GEMMA_PRICE_INPUT_PER_MTOK_USD"))
        put("gemma_price_out_per_mtok", _float("GEMMA_PRICE_OUTPUT_PER_MTOK_USD"))
        put("gemma_openai_base_url", (e("GEMMA_OPENAI_BASE_URL") or "").rstrip("/") or None)
        put("gemma_openai_api_key", e("GEMMA_OPENAI_API_KEY"))

        put("image_provider", (e("IMAGE_PROVIDER") or "").lower() or None)
        put("cf_account_id", e("CF_ACCOUNT_ID") or e("CLOUDFLARE_ACCOUNT_ID"))
        put("cf_api_token", e("CF_API_TOKEN") or e("CLOUDFLARE_API_TOKEN"))
        put("cf_base_url", (e("CF_BASE_URL") or "").rstrip("/") or None)
        put("cf_image_model", e("CF_IMAGE_MODEL"))
        put("cf_image_cost_usd", _float("CF_IMAGE_COST_PER_IMAGE_USD"))
        put("pollinations_api_key", e("POLLINATIONS_API_KEY"))
        put("pollinations_base_url", (e("POLLINATIONS_BASE_URL") or "").rstrip("/") or None)
        put("pollinations_model", e("POLLINATIONS_MODEL"))
        put("pollinations_image_cost_usd", _float("POLLINATIONS_IMAGE_COST_PER_IMAGE_USD"))

        put("evolus_base_url", (e("EVOLUS_BASE_URL") or "").rstrip("/") or None)
        put("evolus_api_key", e("EVOLUS_API_KEY"))
        put("evolus_app_id", e("EVOLUS_APP_ID"))
        put("evolus_agent_id", e("EVOLUS_AGENT_ID"))
        put("evolus_translator_agent_id", e("EVOLUS_TRANSLATOR_AGENT_ID"))
        put("evolus_rest_base_url", (e("EVOLUS_REST_BASE_URL") or "").rstrip("/") or None)
        data["evolus_sentiment_enabled"] = (e("EVOLUS_SENTIMENT") or "false").lower() in ("1", "true", "yes")
        put("evolus_sentiment_tool", e("EVOLUS_SENTIMENT_TOOL"))
        put("evolus_tool_input_field", e("EVOLUS_TOOL_INPUT_FIELD"))
        if e("EVOLUS_TOOL_EXTRA_JSON"):
            try:
                extra = json.loads(e("EVOLUS_TOOL_EXTRA_JSON"))
            except ValueError:
                raise ConfigError("EVOLUS_TOOL_EXTRA_JSON must be a JSON object, for example {\"language\": \"en\"}.") from None
            if not isinstance(extra, dict):
                raise ConfigError("EVOLUS_TOOL_EXTRA_JSON must be a JSON object, for example {\"language\": \"en\"}.")
            data["evolus_tool_extra"] = extra

        data["image_concurrency"] = max(1, _int("IMAGE_CONCURRENCY", 2))
        data["image_max_per_day"] = _int("IMAGE_MAX_PER_DAY", 60)
        try:
            return cls(**data)
        except ValueError as exc:  # pydantic ValidationError is a ValueError
            raise ConfigError(f"Invalid configuration in backend/.env: {exc}") from exc

    def secret_values(self) -> list[str]:
        """Every configured secret, used by the log redaction filter."""
        vals = [self.amd_api_key, self.gemma_api_key, self.gemma_openai_api_key, self.cf_api_token, self.pollinations_api_key, self.evolus_api_key, self.api_token]
        return [v for v in vals if v and len(v) >= 6]


def _resolve(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()
