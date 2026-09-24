"""Cliente HTTP común con espera, cabeceras y manejo uniforme de errores."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from .cache import ConditionalHttpCache
from .paths import REQUEST_DELAY_SECONDS, TIMEOUT_SECONDS, USER_AGENT
from .text import normalized

TRUE_VALUES = {"1", "true", "yes", "si", "sí", "on", "enabled", "enable"}


class HttpClient:
    def __init__(
        self,
        delay: float = REQUEST_DELAY_SECONDS,
        timeout: int = TIMEOUT_SECONDS,
        cache: ConditionalHttpCache | None = None,
    ):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        cache_enabled = normalized(os.getenv("HTTP_CACHE_ENABLED", "true")) in TRUE_VALUES
        self.cache = cache if cache is not None else ConditionalHttpCache() if cache_enabled else None
        self.request_count = 0
        self.cache_hits = 0

    def get_response(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        verify: bool = True,
        request_headers: dict[str, str] | None = None,
    ) -> requests.Response:
        cached = self.cache.load(url, params) if self.cache else None
        headers = self.cache.conditional_headers(cached) if self.cache else {}
        if request_headers:
            headers.update(request_headers)
        self.request_count += 1
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.timeout,
            verify=verify,
        )
        if response.status_code == 304 and cached and self.cache:
            response = self.cache.restore(cached, response)
            self.cache_hits += 1
        response.raise_for_status()
        if self.cache and response.status_code == 200:
            self.cache.store(url, params, response)
        if self.delay:
            time.sleep(self.delay)
        return response

    def close(self) -> None:
        self.session.close()

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        verify: bool = True,
        request_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.get_response(
            url,
            params=params,
            verify=verify,
            request_headers=request_headers,
        ).json()

    def get_html(
        self,
        url: str,
        *,
        verify: bool = True,
        request_headers: dict[str, str] | None = None,
    ) -> str | None:
        try:
            response = self.get_response(
                url,
                verify=verify,
                request_headers=request_headers,
            )
            if "text/html" not in response.headers.get("content-type", ""):
                return None
            try:
                return response.content.decode("utf-8")
            except UnicodeDecodeError:
                return response.text
        except requests.RequestException as exc:
            logging.warning("No se pudo obtener %s: %s", url, exc)
            raise
