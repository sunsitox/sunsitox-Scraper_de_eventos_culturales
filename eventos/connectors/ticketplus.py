"""Strategy nacional para las carteleras regionales públicas de Ticketplus."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..locations import canonical_region
from ..text import clean
from .base import Connector
from .generic import GenericConnector
from .registry import register_connector


def ticketplus_regional_sources(source: SourceConfig, html: str) -> list[SourceConfig]:
    """Construye fuentes regionales desde el selector público de Ticketplus."""
    soup = BeautifulSoup(html, "html.parser")
    result: list[SourceConfig] = []
    seen: set[str] = set()
    for option in soup.select("#select-region option[value]"):
        slug = clean(option.get("value"))
        if not slug or slug in seen:
            continue
        # Ejemplo: "VIII Región del Biobío".
        label = re.sub(r"^\s*[IVXLCDM]+\s+", "", clean(option.get_text(" ")))
        region = canonical_region(label)
        if not region:
            logging.warning("Ticketplus: región no reconocida en el selector: %s", label)
            continue
        seen.add(slug)
        payload = dict(source)
        payload.update(
            url=urljoin(source.url, f"/states/{slug}"),
            region=region,
            # Una región sin eventos es válida y no debe abortar el catálogo nacional.
            allow_empty_listing=True,
        )
        result.append(SourceConfig.from_dict(payload))
    return result


@register_connector("ticketplus_chile")
class TicketplusChileConnector(Connector):
    """Recorre Ticketplus por región, secuencialmente, y reutiliza sus fichas."""

    def collect(self, source: SourceConfig):
        self.collection_complete = True
        verify = bool(source.get("verify_ssl", True))
        home_html = self.http.get_html(source.url, verify=verify)
        if not home_html:
            self.collection_complete = False
            return []
        regional_sources = ticketplus_regional_sources(source, home_html)
        if not regional_sources:
            raise ValueError(f"{source.name}: selector regional vacío o incompatible")
        events = []
        logging.info("  %s: %s carteleras regionales detectadas", source.name, len(regional_sources))
        for position, regional_source in enumerate(regional_sources, start=1):
            collector = GenericConnector(self.http)
            try:
                regional_events = collector.collect(regional_source)
            except Exception:
                # Cada cartelera regional es una unidad independiente. Una caída o
                # cambio de HTML no debe impedir que las demás regiones se publiquen.
                self.collection_complete = False
                logging.exception(
                    "  %s: falló la cartelera de %s; se continúa con las demás regiones",
                    source.name,
                    regional_source.region,
                )
                continue
            events.extend(regional_events)
            if not getattr(collector, "collection_complete", True):
                self.collection_complete = False
            logging.info(
                "  %s: %s/%s %s (%s eventos)",
                source.name, position, len(regional_sources), regional_source.region, len(regional_events),
            )
        return events
