from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research_vault import paths
from research_vault.agents import Codex
from research_vault.storage import VaultError
from research_vault.zotero import Zotero


@pytest.mark.parametrize("name", ["paper.pdf", "spaces and café α.pdf", "literal%20.pdf"])
def test_linux_file_urls_round_trip(tmp_path, monkeypatch, name):
    monkeypatch.setattr(paths, "is_wsl", lambda: False)
    path = tmp_path / name
    path.write_bytes(b"synthetic")
    assert paths.local_path(path.as_uri()) == path
    paths.accessible(path, path.as_uri())
    assert paths.local_path("file://localhost" + path.as_uri()[7:]) == path


@pytest.mark.parametrize(
    "value,expected",
    [
        ("file:///D:/Research%20Files/caf%C3%A9%20%CE%B1.pdf", "D:/Research Files/café α.pdf"),
        (r"F:\Research Files\paper.pdf", r"F:\Research Files\paper.pdf"),
        ("file://server/share/paper.pdf", "//server/share/paper.pdf"),
    ],
)
def test_native_windows_resolution_without_translation(monkeypatch, value, expected):
    monkeypatch.setattr(paths, "is_wsl", lambda: False)
    monkeypatch.setattr(paths, "native_windows", lambda: True)
    monkeypatch.setattr(
        paths.subprocess, "run", lambda *a, **k: pytest.fail("no translation on Windows")
    )
    assert str(paths.local_path(value)).replace("\\", "/") == expected.replace("\\", "/")


def test_wsl_translation_uses_decoded_argument_and_custom_mount(tmp_path, monkeypatch, vault):
    translated = tmp_path / "custom-mount" / "OneDrive - University" / "café α.pdf"
    translated.parent.mkdir(parents=True)
    translated.write_bytes(b"synthetic")
    seen = []
    translated_raw = "/custom-mount/OneDrive - University/café α.pdf"
    monkeypatch.setattr(
        paths, "Path", lambda raw: translated if raw == translated_raw else Path(raw)
    )
    monkeypatch.setattr(paths, "is_wsl", lambda: True)

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, translated_raw + "\n", "")

    monkeypatch.setattr(paths.subprocess, "run", run)
    raw = "file:///R:/OneDrive%20-%20University/caf%C3%A9%20%CE%B1.pdf"
    client = Zotero(vault[-1].config)
    monkeypatch.setattr(client, "request", lambda route: (raw, {}))
    assert client.attachment_path("PDF00001") == translated
    assert seen[0][0] == ["wslpath", "-u", "R:/OneDrive - University/café α.pdf"]
    assert not seen[0][1].get("shell")
    assert client.attachment_diagnostics[-1]["original_url"] == raw
    assert client.attachment_diagnostics[-1]["resolved_path"] == str(translated)


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("wslpath"),
        subprocess.TimeoutExpired("wslpath", 15),
        subprocess.CalledProcessError(1, "wslpath"),
    ],
)
def test_wsl_translation_errors_are_not_missing_files(monkeypatch, failure):
    monkeypatch.setattr(paths, "is_wsl", lambda: True)

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(paths.subprocess, "run", run)
    with pytest.raises(paths.PathError, match="translation failed"):
        paths.local_path("file:///Q:/paper.pdf")


def test_missing_versus_cloud_accessibility(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    with pytest.raises(paths.PathError, match="Missing local") as error:
        paths.accessible(path, path.as_uri())
    assert error.value.resolved == str(path)
    path.write_bytes(b"placeholder")
    original = Path.open

    def offline(self, *args, **kwargs):
        if self == path:
            raise PermissionError("cloud provider unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", offline)
    with pytest.raises(paths.PathError, match="accessibility error.*cloud provider"):
        paths.accessible(path, path.as_uri())


def test_manuscript_roots_and_paths_share_wsl_translation(vault, monkeypatch):
    _, source, _, _, pipeline = vault
    monkeypatch.setattr(paths, "is_wsl", lambda: True)
    monkeypatch.setattr(
        paths, "Path", lambda raw: source if raw == "/test-project" else source / "main.tex"
    )

    def run(command, **kwargs):
        name = "" if command[-1].endswith("Project") else "/main.tex"
        return subprocess.CompletedProcess(command, 0, "/test-project" + name + "\n", "")

    monkeypatch.setattr(paths.subprocess, "run", run)
    pipeline.config.data["source_roots"] = [r"Q:\Project"]
    assert pipeline.config.authorized("file:///Q:/Project/main.tex") == source / "main.tex"


def test_wsl_detection_and_codex_environment(vault, monkeypatch):
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")
    monkeypatch.setattr(paths.platform, "release", lambda: "6.0-microsoft-standard-WSL2")
    assert paths.is_wsl()
    codex = Codex(vault[-1].config)
    monkeypatch.setattr("research_vault.agents.is_wsl", lambda: True)
    monkeypatch.setattr(codex, "available", lambda: "/mnt/c/npm/codex.exe")
    with pytest.raises(VaultError, match="Linux Codex"):
        codex.command_prefix()
