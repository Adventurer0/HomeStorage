"""Pure validation for updater-only proxy settings, shared by the two APIs."""

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator


PROXY_FIELDS = frozenset({"proxy_url", "proxy_username", "proxy_password"})
PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


def normalize_proxy_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("代理地址格式无效")
    if not value:
        return ""
    if any(character.isspace() or unicodedata.category(character) == "Cc" or 0xD800 <= ord(character) <= 0xDFFF for character in value) or "\\" in value:
        raise ValueError("代理地址格式无效")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in PROXY_SCHEMES or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or "?" in value or "#" in value
            or parsed.port is not None and not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        authority_pattern = r"\[[^\]]+\](?::[0-9]{1,5})?" if parsed.netloc.startswith("[") else r"[^:]+(?::[0-9]{1,5})?"
        if not re.fullmatch(authority_pattern, parsed.netloc):
            raise ValueError
        hostname = parsed.hostname
        if ":" in hostname:
            if "%" in hostname:
                raise ValueError
            hostname = "[" + ipaddress.IPv6Address(hostname).compressed + "]"
        else:
            hostname = hostname.encode("idna").decode("ascii").lower()
            if len(hostname) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.rstrip(".").split(".")):
                raise ValueError
        port = ":" + str(parsed.port) if parsed.port is not None else ""
        return parsed.scheme + "://" + hostname + port
    except (ValueError, UnicodeError):
        raise ValueError("代理地址格式无效") from None


def _credential(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or any(unicodedata.category(character) == "Cc" or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("代理认证信息格式无效")
    return value


class ProxySettingsInput(BaseModel):
    # Password spaces are significant. Never inherit global string trimming.
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)
    auto_update: StrictBool | None = None
    proxy_url: StrictStr | None = Field(default=None, max_length=2048)
    proxy_username: StrictStr | None = Field(default=None, max_length=255)
    proxy_password: StrictStr | None = Field(default=None, max_length=2048)

    @field_validator("proxy_url")
    @classmethod
    def validate_url(cls, value):
        return normalize_proxy_url(value) if value is not None else value

    @field_validator("proxy_username", "proxy_password")
    @classmethod
    def validate_credential(cls, value, info):
        return _credential(value, 255 if info.field_name == "proxy_username" else 2048) if value is not None else value

    @model_validator(mode="after")
    def require_settings(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set - {"proxy_password"}):
            raise ValueError("请提供有效的更新设置")
        return self


def proxy_configuration(snapshot):
    if "proxy_configuration" not in snapshot:
        return None
    config = snapshot["proxy_configuration"]
    if not isinstance(config, dict) or set(config) != {"url", "username", "password"}:
        raise ValueError("保存的代理设置无效")
    normalized = {
        "url": normalize_proxy_url(config["url"]),
        "username": _credential(config["username"], 255),
        "password": _credential(config["password"], 2048),
    }
    if not normalized["url"] and (normalized["username"] or normalized["password"]):
        raise ValueError("保存的代理设置无效")
    return normalized


def merge_proxy_configuration(snapshot, updates):
    previous = proxy_configuration(snapshot) or {"url": "", "username": "", "password": ""}
    config = {**previous}
    if "proxy_url" in updates:
        config["url"] = normalize_proxy_url(updates["proxy_url"])
    if "proxy_username" in updates:
        config["username"] = _credential(updates["proxy_username"], 255)
    changed_identity = (config["url"], config["username"]) != (previous["url"], previous["username"])
    if changed_identity:
        config["password"] = ""
    if "proxy_password" in updates:
        password = updates["proxy_password"]
        if password is None:
            config["password"] = ""
        elif _credential(password, 2048) != "":
            config["password"] = password
    if not config["url"]:
        config["username"] = config["password"] = ""
    return config


def public_proxy_status(snapshot):
    config = proxy_configuration(snapshot)
    return {
        "proxy_url": config["url"] if config else "",
        "proxy_username": config["username"] if config else "",
        "proxy_password_set": bool(config and config["password"]),
        "proxy_source": "web" if config is not None else "environment",
    }
