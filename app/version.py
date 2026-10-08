"""Version metadata shared by the application and release builds."""

import os
import re


VERSION = "2.1.1"
REVISION = "local"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def runtime_version() -> str:
    value = os.environ.get("HOMESTORAGE_VERSION", "").strip()
    return value if len(value) <= 64 and VERSION_PATTERN.fullmatch(value) else VERSION


def runtime_revision() -> str:
    value = os.environ.get("HOMESTORAGE_REVISION", "").strip()
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", value) else REVISION
