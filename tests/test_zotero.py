"""Mock HTTP transport rather than mock the complete API adapter."""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest
from conftest import write_pdf

from research_vault.storage import VaultError
from research_vault.zotero import Zotero


def item(key, kind="journalArticle", **fields):
    return {"key": key, "data": {"itemType": kind, **fields}}


class Response:
    def __init__(self, data, headers):
        self.body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.body


class Transport:
    def __init__(self, routes):
        self.routes, self.calls = routes, []
        self.version = "10"

    def open(self, request, timeout):
        from urllib.parse import parse_qs, urlparse

        self.calls.append(request)
        path = urlparse(request.full_url).path.split("/users/0/")[1]
        params = parse_qs(urlparse(request.full_url).query)
        if path not in self.routes:
            raise URLError("Synthetic missing endpoint")
        data = self.routes[path]
        headers = {"Last-Modified-Version": self.version, "Zotero-Server-ID": "synthetic"}
        if isinstance(data, list):
            headers["Total-Results"] = str(len(data))
            start = int(params.get("start", ["0"])[0])
            limit = int(params.get("limit", ["100"])[0])
            data = data[start : start + limit]
        return Response(data, headers)


def test_real_adapter_groups_child_members_and_subcollections(vault):
    _, source, *_ = vault
    config = vault[-1].config
    config.zotero["include_subcollections"] = True
    path = write_pdf(source / "main.pdf")
    parent = item("PAPER001")
    transport = Transport(
        {
            "collections/COLLECT1": {"key": "COLLECT1"},
            "collections/COLLECT1/collections": [item("SUBCOL01", "collection")],
            "collections/SUBCOL01/collections": [],
            "collections/COLLECT1/items": [
                parent,
                item("PDF00001", "attachment", parentItem="PAPER001"),
            ],
            "collections/SUBCOL01/items": [parent],
            "items/PAPER001": parent,
            "items/PAPER001/children": [
                item("PDF00001", "attachment", contentType="application/pdf", title="Main")
            ],
            "items/PDF00001/file/view/url": path.as_uri(),
            "items/PDF00001/children": [
                item("ANNOT001", "annotation", annotationText="An annotation")
            ],
        }
    )
    client = Zotero(config)
    client.opener = transport
    result = client.snapshot()
    assert set(result) == {"PAPER001"}
    assert len(result["PAPER001"]["attachments"]) == 1
    assert result["PAPER001"]["annotations"][0]["key"] == "ANNOT001"
    assert all(r.method == "GET" for r in transport.calls)
    assert client.server == "synthetic"


def test_pagination_collects_all_250_members(vault):
    client = Zotero(vault[-1].config)
    transport = Transport({"collections/COLLECT1/items": [item(f"{i:08d}") for i in range(250)]})
    client.opener = transport
    result = client.listing("collections/COLLECT1/items")
    assert len(result) == 250
    assert len(transport.calls) == 3


def test_incomplete_or_missing_total_refused(vault, monkeypatch):
    client = Zotero(vault[-1].config)
    monkeypatch.setattr(client, "request", lambda *args: ([], {"total-results": "2"}))
    with pytest.raises(VaultError, match="Truncated"):
        client.listing("collections/COLLECT1/items")
    monkeypatch.setattr(client, "request", lambda *args: ([], {}))
    with pytest.raises(VaultError, match="cannot prove completeness"):
        client.listing("collections/COLLECT1/items")


def test_library_version_change_aborts_snapshot(vault):
    client = Zotero(vault[-1].config)
    transport = Transport({"collections/COLLECT1/items": []})
    client.opener = transport
    client.listing("collections/COLLECT1/items")
    transport.version = "11"
    with pytest.raises(VaultError, match="changed during retrieval"):
        client.listing("collections/COLLECT1/items")


def test_attachment_classification_overrides_and_missing_files(vault):
    config = vault[-1].config
    client = Zotero(config)
    supplement = item(
        "PDF00001", "attachment", contentType="application/pdf", title="Supporting information"
    )
    assert client.classify(supplement) == "supplementary"
    config.data["attachments"] = {"overrides": {"PDF00001": "main"}}
    assert client.classify(supplement) == "main"
    assert client.classify(item("DATA0001", "attachment", contentType="text/csv")) == "ignore"
    client.opener = Transport(
        {"items/PDF00001/file/view/url": "file:///nonexistent-synthetic-document.pdf"}
    )
    with pytest.raises(VaultError, match="Missing local attachment"):
        client.attachment_path("PDF00001")


def test_single_object_versions_do_not_conflict_with_library_version(vault):
    client = Zotero(vault[-1].config)
    transport = Transport({"collections/COLLECT1/items": [], "items/PAPER001": item("PAPER001")})
    client.opener = transport
    client.listing("collections/COLLECT1/items")
    assert client.version == "10"
    transport.version = "3"  # A bibliographic item was last modified earlier.
    assert client.item("PAPER001")["key"] == "PAPER001"
    assert client.version == "10"
    transport.version = "10"
    assert client.listing("collections/COLLECT1/items") == []
