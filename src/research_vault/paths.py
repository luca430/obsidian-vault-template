"""Local paths shared by attachments and authorized manuscripts; never run a shell."""

from __future__ import annotations

import os
import platform
import re
import stat
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .storage import VaultError


class PathError(VaultError):
    def __init__(self, reason: str, original: str, resolved: str | None = None):
        self.reason, self.original, self.resolved = reason, original, resolved
        super().__init__(f"{reason}; original={original!r}; resolved={resolved!r}")


def is_wsl() -> bool:
    return platform.system() == "Linux" and (
        "microsoft" in platform.release().lower()
        or bool(os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"))
    )


def environment() -> str:
    return "WSL" if is_wsl() else platform.system()


def native_windows() -> bool:
    return os.name == "nt"


def local_path(raw: str) -> Path:
    original = raw
    if raw.lower().startswith("file:"):
        try:
            url = urlsplit(raw.strip())
            decoded = unquote(url.path, encoding="utf-8", errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise PathError(f"invalid local file URL: {exc}", original) from exc
        if url.scheme.lower() != "file" or url.query or url.fragment:
            raise PathError("invalid local file URL", original)
        raw = decoded
        if url.netloc and url.netloc.lower() != "localhost":
            # UNC paths are usable on Windows and can be translated if mounted in WSL.
            raw = "//" + unquote(url.netloc) + raw
        elif re.match(r"^/[A-Za-z]:[/\\]", raw):
            raw = raw[1:]
    elif "://" in raw:
        raise PathError("not a local file URL", original)
    windows = bool(re.match(r"^[A-Za-z]:[/\\]", raw) or raw.startswith(("\\\\", "//")))
    if windows and is_wsl():
        try:
            result = subprocess.run(
                ["wslpath", "-u", raw],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PathError(f"Windows-to-WSL path translation failed: {exc}", original) from exc
        translated = result.stdout.rstrip("\r\n")
        if not translated.startswith("/") or "\n" in translated or not translated:
            raise PathError("Windows-to-WSL path translation returned an invalid path", original)
        return Path(translated)
    if windows and not native_windows():
        raise PathError("Windows path requires native Windows or WSL", original)
    return Path(raw).expanduser()


def accessible(path: Path, original: str) -> None:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise PathError("not a regular file", original, str(path))
        # Opening detects permissions, offline cloud placeholders and other I/O errors.
        with path.open("rb") as stream:
            stream.read(1)
    except FileNotFoundError as exc:
        raise PathError("Missing local attachment/source", original, str(path)) from exc
    except OSError as exc:
        raise PathError(f"file accessibility error: {exc}", original, str(path)) from exc
