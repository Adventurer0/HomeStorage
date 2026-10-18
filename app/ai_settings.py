"""User-managed provider settings, kept outside inventory and AI tools.

Environment values are a migration default only. The first explicit save copies
the complete configuration into the inventory database so clearing a credential
cannot unexpectedly revive an older Compose credential after a restart.
"""

from contextlib import closing
from dataclasses import dataclass, field, replace
import os
import unicodedata
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .db import transaction


FIELDS = (
    "api_base_url", "api_key", "model", "transcription_model",
    "transcription_base_url", "transcription_api_key",
)
SECRETS = frozenset({"api_key", "transcription_api_key"})


def validated_base(value):
    """Allow OpenAI-compatible HTTPS providers and local HTTP development."""
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        return None
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or not valid_port or "\\" in value
        or any(character.isspace() or unicodedata.category(character) == "Cc" for character in value)
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
    ):
        return None
    return value


@dataclass(frozen=True)
class AIConfiguration:
    api_base_url: str
    api_key: str = field(repr=False)
    model: str
    transcription_model: str
    transcription_base_url: str
    transcription_api_key: str = field(repr=False)
    source: str = "environment"

    @property
    def enabled(self):
        return bool(self.api_base_url and self.api_key and self.model)

    @property
    def voice_base_url(self):
        # An invalid explicit legacy URL must disable voice rather than silently
        # redirecting recordings to the chat provider during migration.
        if self.transcription_base_url:
            return validated_base(self.transcription_base_url) or ""
        return self.api_base_url

    @property
    def voice_api_key(self):
        return self.transcription_api_key or self.api_key

    @property
    def voice_enabled(self):
        return bool(self.enabled and self.transcription_model and self.voice_base_url and self.voice_api_key)

    def public(self):
        return {
            "api_base_url": self.api_base_url,
            "model": self.model,
            "transcription_model": self.transcription_model,
            # An empty voice URL/key means inherit the current chat provider.
            "transcription_base_url": validated_base(self.transcription_base_url) or "",
            "api_key_set": bool(self.api_key),
            "transcription_api_key_set": bool(self.transcription_api_key),
            "enabled": self.enabled,
            "voice_enabled": self.voice_enabled,
            "source": self.source,
        }


class AISettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    api_base_url: StrictStr | None = Field(default=None, max_length=2048)
    model: StrictStr | None = Field(default=None, max_length=256)
    transcription_model: StrictStr | None = Field(default=None, max_length=256)
    transcription_base_url: StrictStr | None = Field(default=None, max_length=2048)
    api_key: StrictStr | None = Field(default=None, max_length=4096)
    transcription_api_key: StrictStr | None = Field(default=None, max_length=4096)

    @field_validator(*FIELDS, mode="before")
    @classmethod
    def bounded_plain_text(cls, value, info):
        if value is None:
            if info.field_name not in SECRETS:
                raise ValueError("配置文字不能设为空值")
            return None
        if not isinstance(value, str):
            raise ValueError("配置必须是文字")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("配置不能包含控制字符")
        return value.strip()

    @field_validator("api_base_url", "transcription_base_url")
    @classmethod
    def provider_url(cls, value, info):
        if value == "" and info.field_name == "transcription_base_url":
            return value
        normalized = validated_base(value)
        if normalized is None:
            raise ValueError("API 地址必须使用 HTTPS，本机服务可使用 HTTP")
        return normalized


class AISettingsStore:
    def __init__(self, connect):
        self.connect = connect
        # Capture at application creation, matching the existing Compose API
        # behavior and keeping unrelated process environment edits irrelevant.
        self.defaults = AIConfiguration(
            api_base_url=validated_base(os.environ.get("AI_API_BASE_URL", "https://api.openai.com/v1").strip()) or "",
            api_key=os.environ.get("AI_API_KEY", "").strip(),
            model=os.environ.get("AI_MODEL", "gpt-4o-mini").strip(),
            transcription_model=os.environ.get("AI_TRANSCRIPTION_MODEL", "").strip(),
            transcription_base_url=os.environ.get("AI_TRANSCRIPTION_BASE_URL", "").strip(),
            transcription_api_key=os.environ.get("AI_TRANSCRIPTION_API_KEY", "").strip(),
        )

    def initialize(self):
        with closing(self.connect()) as db:
            with transaction(db):
                db.execute("""CREATE TABLE IF NOT EXISTS ai_configuration (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    api_base_url TEXT NOT NULL, api_key TEXT NOT NULL,
                    model TEXT NOT NULL, transcription_model TEXT NOT NULL,
                    transcription_base_url TEXT NOT NULL,
                    transcription_api_key TEXT NOT NULL
                )""")

    def _read(self, db):
        row = db.execute("SELECT " + ",".join(FIELDS) + " FROM ai_configuration WHERE id=1").fetchone()
        return AIConfiguration(**dict(row), source="web") if row is not None else self.defaults

    def snapshot(self):
        with closing(self.connect()) as db:
            return self._read(db)

    def save(self, body):
        changes = body.model_dump(exclude_unset=True)
        with closing(self.connect()) as db:
            with transaction(db):
                current = self._read(db)
                for name in SECRETS:
                    if name in changes:
                        if changes[name] is None:
                            changes[name] = ""
                        elif changes[name] == "":
                            # Empty password inputs never erase a stored key.
                            del changes[name]
                updated = replace(current, **changes, source="web")
                db.execute(
                    "INSERT INTO ai_configuration(id," + ",".join(FIELDS) + ") VALUES(1,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + ",".join(name + "=excluded." + name for name in FIELDS),
                    tuple(getattr(updated, name) for name in FIELDS),
                )
        return updated


def build_ai_settings_router(store, *, password_required):
    router = APIRouter(prefix="/api/settings/ai", tags=["settings"])

    def require_password():
        if not (password_required() if callable(password_required) else password_required):
            raise HTTPException(403, "请先设置 HomeStorage 登录密码，再管理 AI 配置")

    @router.get("")
    def settings():
        require_password()
        return store.snapshot().public()

    @router.patch("")
    def save_settings(body: AISettingsInput):
        require_password()
        return store.save(body).public()

    return router
