"""Conector de bajo costo para la cartelera oficial de Teatro Biobio.

La cartelera publica las fichas como entradas de WordPress dentro de la
categoria ``cartelera``. Sus fechas viven en el contenido visible y no en
JSON-LD, por lo que este conector las interpreta de forma determinista en vez
de delegarlas a NVIDIA.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config import SourceConfig
from ..models import Event
from ..text import clean, clean_list, chile_now, is_upcoming, normal_date, now_iso, text_from_html
from .base import Connector
from .registry import register_connector


DATE_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})\b")
DATE_TEXT = re.compile(
    r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)?\s*"
    r"(\d{1,2})\s+de\s+"
    r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
    r"(?:\s+de\s+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
TIME = re.compile(r"\b(\d{1,2})[.:](\d{2})\s*(?:h(?:rs?)?\.?)?", re.IGNORECASE)


def event_datetime(content: str) -> str:
    """Obtiene la fecha del evento sin adivinar fechas de publicacion."""
    text = text_from_html(content)
    numeric = DATE_DMY.search(text)
    if numeric:
        day, month, year = numeric.groups()
        value = f"{year}-{int(month):02d}-{int(day):02d}"
    else:
        written = DATE_TEXT.search(text)
        if not written:
            return ""
        day, month, year = written.groups()
        # La programacion actual suele omitir el ano en la linea "Fecha y
        # hora". Se usa el ano vigente y se corrige la transicion diciembre/
        # enero; no se intenta inferir una fecha si tampoco hay mes y dia.
        current = chile_now()
        year_value = int(year or current.year)
        month_number = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
            "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
            "septiembre": 9, "setiembre": 9, "octubre": 10,
            "noviembre": 11, "diciembre": 12,
        }[month.lower()]
        if not year and month_number < current.month - 6:
            year_value += 1
        value = f"{year_value}-{month_number:02d}-{int(day):02d}"
    clock = TIME.search(text)
    if clock:
        value += f" {int(clock.group(1)):02d}:{clock.group(2)}"
    return normal_date(value)


def post_categories(post: dict[str, Any], defaults: list[str]) -> list[str]:
    """Recoge taxonomias expuestas por WordPress sin incluir etiquetas vacias."""
    names: list[str] = []
    embedded = post.get("_embedded", {})
    if isinstance(embedded, dict):
        for group in embedded.get("wp:term", []):
            if not isinstance(group, list):
                continue
            for term in group:
                if isinstance(term, dict):
                    name = clean(term.get("name"))
                    if name and name.lower() not in {"cartelera", "sin categorizar"}:
                        names.append(name)
    return clean_list(names) or clean_list(defaults)


@register_connector("teatro_biobio")
class TeatroBiobioConnector(Connector):
    """Lee la categoria de cartelera desde la API publica de WordPress."""

    def collect(self, source: SourceConfig) -> list[Event]:
        categories_url = source.get(
            "categories_api_url", "https://teatrobiobio.cl/wp-json/wp/v2/categories"
        )
        category_slug = clean(source.get("category_slug", "cartelera"))
        categories = self.http.get_json(categories_url, params={"slug": category_slug})
        if not isinstance(categories, list) or not categories or not categories[0].get("id"):
            raise ValueError(f"{source.name}: no se encontro la categoria WordPress {category_slug!r}")

        category_id = categories[0]["id"]
        posts_url = source.get(
            "api_url", "https://teatrobiobio.cl/wp-json/wp/v2/posts"
        )
        max_pages = max(1, int(source.get("max_pages", 5) or 5))
        max_results = int(source.get("max_results", 0) or 0)
        page = 1
        results: list[Event] = []
        while page <= max_pages:
            response = self.http.get_response(
                posts_url,
                params={
                    "page": page,
                    "per_page": 100,
                    "categories": category_id,
                    "status": "publish",
                    "_embed": 1,
                },
                verify=bool(source.get("verify_ssl", True)),
            )
            posts = response.json()
            if not isinstance(posts, list) or not posts:
                break
            for post in posts:
                if not isinstance(post, dict):
                    continue
                content = str(post.get("content", {}).get("rendered", ""))
                start_date = event_datetime(content)
                event = Event(
                    title=text_from_html(post.get("title", {}).get("rendered", "")),
                    start_date=start_date,
                    venue=clean(source.get("venue")),
                    address=clean(source.get("address")),
                    commune=source.commune,
                    city=source.city or source.commune,
                    region=source.region,
                    organizer=source.organizer,
                    categories=post_categories(post, source.get("default_categories", [])),
                    description=text_from_html(content),
                    image_url=clean(
                        post.get("_embedded", {})
                        .get("wp:featuredmedia", [{}])[0]
                        .get("source_url", "")
                    ),
                    source_name=source.name,
                    source_url=clean(post.get("link")) or source.url,
                    official_url=clean(post.get("link")) if source.official else "",
                    extracted_at=now_iso(),
                    extraction_method="teatro-biobio-wordpress-rest",
                )
                if event.title and is_upcoming(event):
                    results.append(event)
                if max_results and len(results) >= max_results:
                    break
            total_pages = int(response.headers.get("X-WP-TotalPages", page))
            if page >= total_pages or (max_results and len(results) >= max_results):
                break
            page += 1
        logging.info("  %s: %s fichas por WordPress REST", source.name, len(results))
        return results
