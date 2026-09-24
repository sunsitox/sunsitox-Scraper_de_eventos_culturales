"""Strategy determinista para el portafolio oficial de ferias FISA."""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..models import Event
from ..text import clean, clean_list, is_upcoming, normalized, now_iso
from .base import Connector
from .registry import register_connector


MONTHS = {
    "ene": 1, "enero": 1, "jan": 1, "january": 1,
    "feb": 2, "febrero": 2, "february": 2,
    "mar": 3, "marzo": 3, "march": 3,
    "abr": 4, "abril": 4, "apr": 4, "april": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6, "june": 6,
    "jul": 7, "julio": 7, "july": 7,
    "ago": 8, "agosto": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10,
    "nov": 11, "noviembre": 11, "november": 11,
    "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
}


def fisa_date_range(value: str) -> tuple[str, str]:
    """Interpreta rangos como '20 al 22 octubre 2026' y 'Sep 29 ... 01 Oct 2026'."""
    tokens = normalized(value).split()
    years = [int(token) for token in tokens if token.isdigit() and len(token) == 4]
    month_positions = [(index, MONTHS[token]) for index, token in enumerate(tokens) if token in MONTHS]
    days = [
        (index, int(token))
        for index, token in enumerate(tokens)
        if token.isdigit() and len(token) <= 2 and 1 <= int(token) <= 31
    ]
    if not years or not month_positions or not days:
        return "", ""
    year = years[-1]
    start_month = month_positions[0][1]
    end_month = month_positions[-1][1]
    start_day = days[0][1]
    end_day = days[-1][1]
    try:
        return date(year, start_month, start_day).isoformat(), date(
            year, end_month, end_day
        ).isoformat()
    except ValueError:
        return "", ""


def location_fields(value: str, default_region: str = "") -> tuple[str, str, str, str]:
    raw = clean(value)
    key = normalized(raw)
    venue = clean(raw.split(",", 1)[0])
    address = clean(raw.split(",", 1)[1]) if "," in raw else ""
    rules = (
        (r"los lagos", "", "Región de Los Lagos"),
        (r"puerto varas|la laja", "Puerto Varas", "Región de Los Lagos"),
        (r"punta arenas", "Punta Arenas", "Región de Magallanes y de la Antártica Chilena"),
        (r"valparaiso|terminal pasajeros", "Valparaíso", "Región de Valparaíso"),
        (r"huechuraba|espacio riesco", "Huechuraba", "Región Metropolitana de Santiago"),
        (r"vitacura|metropolitan santiago", "Vitacura", "Región Metropolitana de Santiago"),
        (r"estacion mapocho|santiago", "Santiago", "Región Metropolitana de Santiago"),
    )
    for pattern, commune, region in rules:
        if re.search(pattern, key):
            return venue, address, commune, region
    return venue, address, "", default_region


def expo_categories(title: str, defaults: object) -> list[str]:
    categories = clean_list(defaults)
    key = normalized(title)
    rules = (
        ("salud", "Salud"),
        ("naval", "Industria naval"),
        ("food", "Gastronomía"),
        ("aqua", "Acuicultura"),
        ("edifica", "Construcción"),
        ("expomin", "Minería"),
    )
    return clean_list(categories + [category for needle, category in rules if needle in key])


@register_connector("fisa_portfolio")
class FisaPortfolioConnector(Connector):
    def collect(self, source: SourceConfig) -> list[Event]:
        verify = bool(source.get("verify_ssl", True))
        index_html = self.http.get_html(source.url, verify=verify)
        if not index_html:
            return []
        soup = BeautifulSoup(index_html, "html.parser")
        links: list[str] = []
        for anchor in soup.select('a[href*="/ferias/"]'):
            url = urljoin(source.url, anchor.get("href", ""))
            if url not in links:
                links.append(url)

        limit = int(source.get("max_results", 0) or 0)
        results: list[Event] = []
        for page_url in links:
            html = self.http.get_html(page_url, verify=verify)
            if not html:
                continue
            detail = BeautifulSoup(html, "html.parser")
            heading = detail.select_one("h1")
            title = clean(heading.get_text(" ")) if heading else ""
            info: dict[str, str] = {}
            for box in detail.select(".portfolio-details-box"):
                label = box.select_one(".title")
                value = box.select_one(".info")
                if label and value:
                    info[normalized(label.get_text(" "))] = clean(value.get_text(" "))
            start_date, end_date = fisa_date_range(info.get("fecha", ""))
            venue, address, commune, region = location_fields(
                info.get("lugar", ""), source.region
            )
            description_node = detail.select_one(".portfolio-details__content p")
            description = clean(description_node.get_text(" ")) if description_node else ""
            image_node = detail.select_one('meta[property="og:image"]')
            image_url = clean(image_node.get("content")) if image_node else ""
            official_url = page_url
            for anchor in detail.select("a[href]"):
                href = urljoin(page_url, anchor.get("href", ""))
                host = urlparse(href).netloc.removeprefix("www.")
                label = normalized(anchor.get_text(" "))
                if "visitar" in label and host and host != "fisa.cl":
                    official_url = href
                    break
            event = Event(
                title=title,
                start_date=start_date,
                end_date=end_date,
                venue=venue,
                address=address,
                commune=commune,
                city=commune,
                region=region,
                organizer=source.organizer,
                categories=expo_categories(title, source.get("default_categories", [])),
                description=description,
                image_url=image_url,
                source_name=source.name,
                source_url=page_url,
                official_url=official_url,
                extracted_at=now_iso(),
                extraction_method="fisa-portfolio-html",
            )
            if event.title and is_upcoming(event):
                results.append(event)
            if limit > 0 and len(results) >= limit:
                break
        logging.info("  %s: %s ferias vigentes", source.name, len(results))
        return results
