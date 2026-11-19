"""GET-only Zotero local API adapter with complete, consistent snapshots."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .config import KEY, Config
from .storage import VaultError, digest, file_hash


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Zotero:
    def __init__(self, config: Config):
        self.config = config
        self.base = config.zotero.get("base_url", "http://localhost:23119/api/")
        self.prefix = config.zotero.get("library", "users/0")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.version: str | None = None
        self.server: str | None = None

    def request(self, route: str, params: dict | None = None) -> tuple[object, dict]:
        url = self.base + self.prefix + "/" + route
        if params:
            url += "?" + urlencode(params)
        headers = {"Zotero-API-Version": "3"}
        if self.server:
            headers["Zotero-Server-ID"] = self.server
        try:
            with self.opener.open(
                Request(url, headers=headers, method="GET"), timeout=30
            ) as response:
                h = dict(response.headers.items())
                # Normalize for fake services and HTTP implementations alike.
                h = {k.lower(): v for k, v in h.items()}
                version = h.get("last-modified-version")
                server = h.get("zotero-server-id")
                if self.server is not None and server is not None and server != self.server:
                    raise VaultError("Zotero instance changed during retrieval")
                if server is not None:
                    self.server = server
                body = response.read().decode("utf-8")
                data = body if route.endswith("/file/view/url") else json.loads(body)
                # Zotero uses OBJECT versions on single-item/collection responses;
                # only multi-object lists carry the comparable LIBRARY version.
                if isinstance(data, list) and version is not None:
                    if self.version is not None and version != self.version:
                        raise VaultError("Zotero library changed during retrieval; retry sync")
                    self.version = version
                return data, h
        except (URLError, HTTPError, OSError, ValueError) as exc:
            raise VaultError(
                f"Zotero GET {route} failed: {exc}. Keep Zotero open and enable its local API."
            ) from exc

    def listing(self, route: str) -> list[dict]:
        output = []
        start, expected = 0, None
        seen = set()
        while True:
            data, headers = self.request(route, {"format": "json", "limit": 100, "start": start})
            if not isinstance(data, list):
                raise VaultError(f"Incomplete Zotero list: {route}")
            try:
                total = int(headers["total-results"])
            except (KeyError, ValueError) as exc:
                raise VaultError(
                    f"Zotero omitted Total-Results for {route}; cannot prove completeness"
                ) from exc
            if expected is not None and total != expected:
                raise VaultError("Collection changed during pagination")
            expected = total
            for item in data:
                key = item.get("key") if isinstance(item, dict) else None
                if (
                    not isinstance(key, str)
                    or not KEY.fullmatch(key)
                    or key in seen
                    or not isinstance(item.get("data"), dict)
                ):
                    raise VaultError("Malformed or repeated Zotero item in paginated response")
                seen.add(key)
            output.extend(data)
            if len(output) == total:
                return output
            if not data or len(output) > total:
                raise VaultError(f"Truncated Zotero response for {route}")
            start += len(data)

    def item(self, key: str) -> dict:
        if not KEY.fullmatch(key):
            raise VaultError("Invalid Zotero parent key")
        item, _ = self.request(f"items/{key}")
        if (
            not isinstance(item, dict)
            or item.get("key") != key
            or not isinstance(item.get("data"), dict)
        ):
            raise VaultError(f"Malformed parent item {key}")
        return item

    def attachment_path(self, key: str) -> Path:
        value, _ = self.request(f"items/{key}/file/view/url")
        if not isinstance(value, str):
            raise VaultError(f"No local file URL for attachment {key}")
        url = urlparse(value.strip())
        if url.scheme != "file" or url.netloc not in {"", "localhost"}:
            raise VaultError(f"Attachment {key} is not a local file")
        raw = unquote(url.path)
        if re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
        path = Path(raw)
        if not path.is_file():
            raise VaultError(f"Missing local attachment {key}: download it in Zotero")
        return path

    def snapshot(self) -> dict[str, dict]:
        self.version = None
        collection = self.config.zotero["collection_key"]
        meta, _ = self.request(f"collections/{collection}")
        if not isinstance(meta, dict) or meta.get("key") != collection:
            raise VaultError("Zotero collection does not exist")
        keys = {collection}
        if self.config.zotero.get("include_subcollections", False):
            queue = [collection]
            while queue:
                parent = queue.pop()
                for child in self.listing(f"collections/{parent}/collections"):
                    if child["key"] not in keys:
                        keys.add(child["key"])
                        queue.append(child["key"])
        members: dict[str, dict] = {}
        memberships = {}
        for key in sorted(keys):
            items = self.listing(f"collections/{key}/items")
            memberships[key] = digest(items)
            for item in items:
                data = item["data"]
                if data.get("deleted"):
                    continue
                parent = data.get("parentItem")
                if parent:
                    item = self.item(parent)
                if item["data"].get("itemType") in {"attachment", "note", "annotation"}:
                    continue
                members[item["key"]] = item
        result = {}
        for key, item in sorted(members.items()):
            children = self.listing(f"items/{key}/children")
            attachments, annotations = [], []
            for child in children:
                data = child["data"]
                if data.get("itemType") != "attachment":
                    annotations.append(child)
                    continue
                classification = self.classify(child)
                attachment = {
                    "key": child["key"],
                    "classification": classification,
                    "metadata": child,
                }
                if classification != "ignore":
                    path = self.attachment_path(child["key"])
                    attachment.update(path=str(path), sha256=file_hash(path))
                    # Annotations are children of PDF attachments, not bibliographic items.
                    annotations.extend(self.listing(f"items/{child['key']}/children"))
                else:
                    attachment["reason"] = "configured ignore or unsupported non-PDF attachment"
                attachments.append(attachment)
            result[key] = {
                "metadata": item,
                "attachments": attachments,
                "annotations": annotations,
                "fingerprint": digest([item, attachments, annotations]),
            }
        # Double-check membership, even on servers omitting version headers.
        for key in sorted(keys):
            if digest(self.listing(f"collections/{key}/items")) != memberships[key]:
                raise VaultError("Collection membership changed during snapshot")
        return result

    def classify(self, attachment: dict) -> str:
        key, data = attachment["key"], attachment["data"]
        override = self.config.data.get("attachments", {}).get("overrides", {}).get(key)
        is_pdf = data.get("contentType") == "application/pdf" or data.get(
            "filename", ""
        ).lower().endswith(".pdf")
        if override == "ignore":
            return "ignore"
        if not is_pdf:
            if override:
                raise VaultError(f"Attachment {key} is configured as PDF but has unsupported type")
            return "ignore"
        if override:
            return override
        tags = {t.get("tag", "").lower() for t in data.get("tags", [])}
        if "research-vault:main" in tags:
            return "main"
        if "research-vault:supplementary" in tags:
            return "supplementary"
        title = data.get("title", "") + " " + data.get("filename", "")
        return (
            "supplementary" if re.search(r"supplement|supporting|appendix", title, re.I) else "main"
        )
