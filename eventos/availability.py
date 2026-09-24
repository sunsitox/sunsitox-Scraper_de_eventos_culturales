"""Validación conservadora de fichas públicas para fuentes que la requieren."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import SourceConfig
from .consolidation import original_event_url
from .http import HttpClient
from .models import Event
from .text import clean, normalized


SOFT_404_MARKERS = (
    "404 not found",
    "error 404",
    "page not found",
    "pagina no encontrada",
    "pagina no existe",
    "contenido no encontrado",
    "evento no encontrado",
)


def is_soft_404(html: str) -> bool:
    """Reconoce mensajes inequívocos de inexistencia aunque el servidor responda 200."""
    soup = BeautifulSoup(html, "html.parser")
    headings = " ".join(
        clean(node.get_text(" "))
        for node in soup.select("title, h1")[:4]
    )
    value = normalized(headings)
    return any(marker in value for marker in SOFT_404_MARKERS)


class EventAvailabilityValidator:
    """Retira solo URLs confirmadas como 404/410 o como una página 404 simulada."""

    def __init__(self, http: HttpClient | None = None):
        self.http = http

    def apply(self, events: list[Event], sources: list[SourceConfig]) -> int:
        source_by_name = {
            source.name: source
            for source in sources
            if source.enabled and bool(source.get("validate_event_urls", False))
        }
        if not source_by_name or not events:
            return 0

        # Una URL compartida normalmente corresponde a una cartelera, no a una ficha.
        url_counts: dict[str, int] = {}
        for event in events:
            url = original_event_url(event)
            if event.source_name in source_by_name and url:
                url_counts[url] = url_counts.get(url, 0) + 1

        http = self.http or HttpClient()
        unavailable: set[str] = set()
        checked: set[str] = set()
        try:
            for event in events:
                source = source_by_name.get(event.source_name)
                url = original_event_url(event)
                if source is None or not url or url_counts.get(url) != 1 or url in checked:
                    continue
                checked.add(url)
                if urlparse(url).scheme not in {"http", "https"}:
                    unavailable.add(url)
                    continue
                try:
                    response = http.get_response(
                        url,
                        verify=bool(source.get("verify_ssl", True)),
                    )
                except requests.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in {404, 410}:
                        unavailable.add(url)
                        logging.info(
                            "  %s: ficha retirada por HTTP %s: %s",
                            source.name,
                            status,
                            url,
                        )
                    else:
                        logging.warning(
                            "No se pudo confirmar la ficha %s (HTTP %s); se conserva.",
                            url,
                            status or "desconocido",
                        )
                except requests.RequestException as exc:
                    # Timeout, bloqueo o caída no demuestran que el evento haya desaparecido.
                    logging.warning(
                        "No se pudo confirmar la ficha %s; se conserva: %s",
                        url,
                        exc,
                    )
                else:
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type and is_soft_404(response.text):
                        unavailable.add(url)
                        logging.info("  %s: ficha retirada por página 404 simulada: %s", source.name, url)
        finally:
            if self.http is None:
                http.close()

        if unavailable:
            events[:] = [event for event in events if original_event_url(event) not in unavailable]
        logging.info(
            "Validación de fichas: %s URL revisadas, %s eventos no disponibles retirados.",
            len(checked),
            len(unavailable),
        )
        return len(unavailable)
