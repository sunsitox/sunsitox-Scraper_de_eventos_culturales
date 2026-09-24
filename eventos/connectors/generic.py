"""Strategy genérica: JSON-LD primero y NVIDIA NIM como respaldo."""

from __future__ import annotations

import logging
import re
import requests
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..extractors import extract_jsonld
from ..models import Event
from ..services import NvidiaExtractor
from ..text import clean, is_upcoming
from ..state import DetailCache
from .base import Connector
from .registry import register_connector


def candidate_links(
    html: str,
    source_url: str,
    limit: int = 30,
    path_prefixes: tuple[str, ...] = (),
) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    origin = urlparse(source_url).netloc.removeprefix("www.")
    pattern = re.compile(
        r"evento|event|agenda|programa|obra|concierto|funcion|actividad|ticket|cartelera|que-hacer",
        re.I,
    )
    candidates: list[str] = []
    for anchor in soup.select("a[href]"):
        url = urldefrag(urljoin(source_url, anchor["href"]))[0]
        # Los calendarios WordPress suelen publicar un enlace de suscripción
        # ``webcal://`` junto a las fichas normales. Requests sólo descarga
        # HTTP(S); además, ese archivo no es una ficha HTML de evento. No debe
        # impedir que el resto de la agenda se procese.
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        # The Events Calendar también ofrece iCalendar sobre HTTPS. El
        # contenido es un calendario, no HTML, aunque la URL diga "event".
        query = parse_qs(parsed.query)
        if parsed.path.lower().endswith(".ics") or any(
            value.lower() in {"1", "true"}
            for key in ("ical", "outlook-ical") for value in query.get(key, [])
        ):
            continue
        target_origin = urlparse(url).netloc.removeprefix("www.")
        target_path = urlparse(url).path
        if path_prefixes and not any(target_path.startswith(prefix) for prefix in path_prefixes):
            continue
        if target_origin == origin and pattern.search(clean(anchor.get_text(" ")) + url):
            if url != source_url and url not in candidates:
                candidates.append(url)
        if limit > 0 and len(candidates) >= limit:
            break
    return candidates


@register_connector("generic")
class GenericConnector(Connector):
    def __init__(self, http):
        super().__init__(http)
        self.nvidia = NvidiaExtractor()

    def collect(self, source: SourceConfig) -> list[Event]:
        self.collection_complete = True
        verify = bool(source.get("verify_ssl", True))
        home_html = self.http.get_html(source.url, verify=verify)
        if not home_html:
            return []
        max_pages = int(source.get("max_pages", source.get("max_results", 30)))
        configured_prefixes = source.get("link_path_prefixes", [])
        if isinstance(configured_prefixes, str):
            configured_prefixes = [configured_prefixes]
        path_prefixes = tuple(
            clean(prefix) for prefix in configured_prefixes if clean(prefix).startswith("/")
        )
        pages = [source.url] + candidate_links(
            home_html, source.url, max_pages, path_prefixes
        )
        if (
            source.get("incremental")
            and len(pages) == 1
            and not source.get("allow_empty_listing", False)
        ):
            raise ValueError(f"{source.name}: no se encontraron enlaces de fichas; revisar estructura")
        cache = DetailCache(self.state, source) if self.state and source.get("incremental") else None
        use_nvidia = bool(source.get("use_nvidia", True))
        events: list[Event] = []
        for position, page_url in enumerate(pages, start=1):
            cached = cache.load(page_url) if cache and page_url != source.url else None
            if cached is not None:
                events.extend(cached)
                continue
            try:
                page_html = home_html if page_url == source.url else self.http.get_html(
                    page_url, verify=verify
                )
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in {404, 410}:
                    if cache:
                        cache.save(page_url, [])
                else:
                    self.collection_complete = False
                logging.warning("Ficha omitida en %s: %s", source.name, page_url)
                continue
            if not page_html:
                self.collection_complete = False
                logging.warning("Ficha sin HTML omitida en %s: %s", source.name, page_url)
                continue
            structured = extract_jsonld(page_html, source, page_url)
            if structured:
                events.extend(structured)
            elif use_nvidia:
                events.extend(self.nvidia.extract(page_html, source, page_url))
            if cache and page_url != source.url:
                if not structured:
                    self.collection_complete = False
                    logging.warning("%s: ficha sin JSON-LD: %s", source.name, page_url)
                    continue
                cache.save(page_url, structured)
            if position % 20 == 0:
                logging.info("  %s: revisadas %s de %s páginas", source.name, position, len(pages))
        events = [event for event in events if is_upcoming(event)]
        limit = int(source.get("max_results", 0) or 0)
        logging.info("  %s: %s fichas genéricas", source.name, len(events))
        return events if limit <= 0 else events[:limit]
