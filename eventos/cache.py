"""Caché HTTP condicional para reutilizar respuestas que no han cambiado."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

import requests

from .paths import CACHE_DIR

LOGGER = logging.getLogger(__name__)


class ConditionalHttpCache:
    """Persiste ETag/Last-Modified y reconstruye una respuesta cuando llega 304."""

    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self.directory = directory
        self._lock = threading.Lock()

    @staticmethod
    def _key(url: str, params: dict[str, Any] | None) -> str:
        payload = json.dumps(
            {"url": url, "params": params or {}},
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        return self.directory / f"{self._key(url, params)}.json"

    def load(self, url: str, params: dict[str, Any] | None) -> dict[str, Any] | None:
        path = self._path(url, params)
        try:
            with self._lock:
                if not path.exists():
                    return None
                payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def conditional_headers(entry: dict[str, Any] | None) -> dict[str, str]:
        if not entry:
            return {}
        headers: dict[str, str] = {}
        if entry.get("etag"):
            headers["If-None-Match"] = str(entry["etag"])
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = str(entry["last_modified"])
        return headers

    def store(
        self,
        url: str,
        params: dict[str, Any] | None,
        response: requests.Response,
    ) -> None:
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        if not (etag or last_modified) or "no-store" in response.headers.get(
            "Cache-Control", ""
        ).lower():
            return
        payload = {
            "etag": etag,
            "last_modified": last_modified,
            "url": response.url,
            "encoding": response.encoding,
            "headers": dict(response.headers),
            "body": base64.b64encode(response.content).decode("ascii"),
        }
        path = self._path(url, params)
        temporary = path.with_suffix(".tmp")
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(payload), encoding="utf-8")
                temporary.replace(path)
        except OSError as exc:
            LOGGER.debug("No se pudo actualizar la caché HTTP: %s", exc)

    @staticmethod
    def restore(entry: dict[str, Any], response_304: requests.Response) -> requests.Response:
        restored = requests.Response()
        restored.status_code = 200
        restored._content = base64.b64decode(str(entry.get("body", "")))
        restored._content_consumed = True
        restored.url = str(entry.get("url") or response_304.url)
        restored.encoding = entry.get("encoding") or response_304.encoding
        restored.headers.update(entry.get("headers") or {})
        restored.headers.update(response_304.headers)
        restored.request = response_304.request
        return restored
