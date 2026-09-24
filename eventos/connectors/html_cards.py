"""Strategy declarativa para carteleras compuestas por tarjetas HTML."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..config import SourceConfig
from ..models import Event
from ..text import (
    chile_now,
    clean,
    clean_list,
    is_upcoming,
    normal_date,
    now_iso,
    parse_spanish_datetime,
)
from .base import Connector
from .registry import register_connector


ABBREVIATED_SPANISH_MONTHS = {
    "ene": "enero",
    "feb": "febrero",
    "mar": "marzo",
    "abr": "abril",
    "may": "mayo",
    "jun": "junio",
    "jul": "julio",
    "ago": "agosto",
    "sep": "septiembre",
    "oct": "octubre",
    "nov": "noviembre",
    "dic": "diciembre",
}


def selector_value(card: Tag, rule: str | dict[str, Any] | None) -> str:
    if not rule:
        return ""
    if isinstance(rule, str):
        selector, attribute = rule, "text"
    else:
        selector = clean(rule.get("selector"))
        attribute = clean(rule.get("attribute")) or "text"
    node = card.select_one(selector) if selector else None
    if not node:
        return ""
    if attribute == "text":
        return clean(node.get_text(" "))
    return clean(node.get(attribute, ""))


def card_categories(source: SourceConfig, card_text: str, explicit: object) -> list[str]:
    categories = clean_list(explicit) or clean_list(source.get("default_categories", []))
    rules = source.get("category_rules", {})
    if not isinstance(rules, dict):
        return categories
    for pattern, additions in rules.items():
        try:
            matches = re.search(str(pattern), card_text, re.I)
        except re.error:
            matches = None
        if matches:
            values = additions if isinstance(additions, list) else [additions]
            categories = clean_list(categories + values)
    return categories


def spanish_same_month_range(value: str) -> tuple[str, str]:
    """Interpreta rangos públicos como ``Del 11 al 20 de septiembre``."""
    match = re.search(
        r"(?:del\s+)?(\d{1,2})\s*(?:al|a|y)\s*(\d{1,2})\s+de\s+([a-záéíóúñ]+)",
        clean(value),
        re.I,
    )
    if not match:
        return normal_date(value), ""
    year = str(chile_now().year)
    start = parse_spanish_datetime(match.group(1), match.group(3), year)
    end = parse_spanish_datetime(match.group(2), match.group(3), year)
    return start, end


def spanish_flexible_range(value: str, current=None) -> tuple[str, str]:
    """Interpreta fechas simples y rangos chilenos con uno o dos meses explícitos."""
    text = clean(value)
    if not text:
        return "", ""
    # Un año aislado no identifica una función. Dateutil completaría mes y día
    # con la fecha actual, generando un evento falso.
    if re.fullmatch(r"(?:19|20)\d{2}", text):
        return "", ""
    month_pattern = "|".join(
        sorted(
            (
                "enero", "febrero", "marzo", "abril", "mayo", "junio",
                "julio", "agosto", "septiembre", "setiembre", "octubre",
                "noviembre", "diciembre",
            ),
            key=len,
            reverse=True,
        )
    )
    full_dates = list(
        re.finditer(
            rf"\b(\d{{1,2}})(?:\s+de)?\s+({month_pattern})\b",
            text,
            re.I,
        )
    )
    same_month = re.search(
        rf"\b(\d{{1,2}})\s*(?:al|a|y)\s*(\d{{1,2}})\s+de\s+({month_pattern})\b",
        text,
        re.I,
    )
    if not full_dates and not same_month:
        return normal_date(text), ""

    today = current or chile_now()
    clocks = re.findall(r"(?<!\d)(\d{1,2})[.:](\d{2})(?!\d)", text)
    start_clock = f"{int(clocks[0][0]):02d}:{clocks[0][1]}" if clocks else "00:00"
    end_clock = f"{int(clocks[-1][0]):02d}:{clocks[-1][1]}" if clocks else start_clock

    if same_month:
        start_day, end_day, month_name = same_month.groups()
        month_alias = month_name[:3].lower().replace("set", "sep")
        start_month = list(ABBREVIATED_SPANISH_MONTHS).index(month_alias) + 1
        start_year = today.year + int(today.month - start_month >= 6)
        return (
            parse_spanish_datetime(start_day, month_name, str(start_year), start_clock),
            parse_spanish_datetime(end_day, month_name, str(start_year), end_clock),
        )

    first = full_dates[0]
    start_day, start_month_name = first.groups()
    start_alias = start_month_name[:3].lower().replace("set", "sep")
    start_month = list(ABBREVIATED_SPANISH_MONTHS).index(start_alias) + 1
    start_year = today.year + int(today.month - start_month >= 6)
    start = parse_spanish_datetime(start_day, start_month_name, str(start_year), start_clock)
    if len(full_dates) == 1:
        return start, ""

    last = full_dates[-1]
    end_day, end_month_name = last.groups()
    end_alias = end_month_name[:3].lower().replace("set", "sep")
    end_month = list(ABBREVIATED_SPANISH_MONTHS).index(end_alias) + 1
    end_year = start_year + int(end_month < start_month)
    end = parse_spanish_datetime(end_day, end_month_name, str(end_year), end_clock)
    return start, end


def spanish_abbreviated_range(value: str, current=None) -> tuple[str, str]:
    """Interpreta ``04 SEP — 16 OCT`` y ``26 SEP · 19.00 H.``."""
    text = clean(value)
    matches = re.findall(
        r"\b(\d{1,2})\s+(ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)\b",
        text,
        re.I,
    )
    if not matches:
        return normal_date(value), ""

    today = current or chile_now()
    start_day, start_alias = matches[0]
    start_month_name = ABBREVIATED_SPANISH_MONTHS[start_alias.lower()]
    start_month = list(ABBREVIATED_SPANISH_MONTHS).index(start_alias.lower()) + 1
    start_year = today.year + int(today.month - start_month >= 6)
    clock_match = re.search(r"\b(\d{1,2})[.:](\d{2})\s*H\b", text, re.I)
    clock = f"{int(clock_match.group(1)):02d}:{clock_match.group(2)}" if clock_match else "00:00"
    start = parse_spanish_datetime(start_day, start_month_name, str(start_year), clock)

    if len(matches) == 1:
        return start, ""
    end_day, end_alias = matches[1]
    end_month_name = ABBREVIATED_SPANISH_MONTHS[end_alias.lower()]
    end_month = list(ABBREVIATED_SPANISH_MONTHS).index(end_alias.lower()) + 1
    end_year = start_year + int(end_month < start_month)
    end = parse_spanish_datetime(end_day, end_month_name, str(end_year), clock)
    return start, end


@register_connector("html_cards")
class HtmlCardsConnector(Connector):
    """Convierte tarjetas usando selectores definidos únicamente en el JSON."""

    def collect(self, source: SourceConfig) -> list[Event]:
        configured_headers = source.get("request_headers", {})
        request_headers = (
            {str(key): str(value) for key, value in configured_headers.items()}
            if isinstance(configured_headers, dict)
            else None
        )
        html = self.http.get_html(
            source.url,
            verify=bool(source.get("verify_ssl", True)),
            request_headers=request_headers,
        )
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        card_selector = clean(source.get("card_selector"))
        selectors = source.get("selectors", {})
        if not card_selector or not isinstance(selectors, dict) or not selectors.get("title"):
            raise ValueError(f"{source.name} requiere card_selector y selectors.title")
        limit = int(source.get("max_results", 0) or 0)
        results: list[Event] = []
        cards = soup.select(card_selector)
        if not cards and not source.get("allow_empty_listing", False):
            raise ValueError(
                f"{source.name}: el selector de tarjetas no produjo resultados; revisar estructura"
            )
        for card in cards:
            values = {field: selector_value(card, rule) for field, rule in selectors.items()}
            title_remove_pattern = clean(source.get("title_remove_pattern"))
            if title_remove_pattern and values.get("title"):
                try:
                    values["title"] = clean(
                        re.sub(title_remove_pattern, "", values["title"], flags=re.I)
                    )
                except re.error as exc:
                    raise ValueError(
                        f"{source.name}: title_remove_pattern no válido"
                    ) from exc
            start_date = values.get("start_date", "")
            end_date = values.get("end_date", "")
            date_range = values.get("date_range", "")
            if date_range:
                if source.get("date_range_parser") == "spanish_same_month":
                    start_date, end_date = spanish_same_month_range(date_range)
                elif source.get("date_range_parser") == "spanish_abbreviated":
                    start_date, end_date = spanish_abbreviated_range(date_range)
                elif source.get("date_range_parser") == "spanish_flexible":
                    start_date, end_date = spanish_flexible_range(date_range)
                else:
                    separator = str(source.get("date_range_separator", " - ")) or " - "
                    parts = date_range.split(separator, maxsplit=1)
                    start_date = parts[0]
                    end_date = parts[1] if len(parts) > 1 else ""
            if values.get("start_time"):
                start_date = f"{start_date} {values['start_time']}"
            if values.get("end_time"):
                end_date = f"{end_date or start_date} {values['end_time']}"
            source_url = urljoin(source.url, values.get("source_url", "")) or source.url
            card_text = clean(card.get_text(" "))
            free = bool(re.search(r"gratis|gratuit|entrada liberada", card_text, re.I))
            event = Event(
                title=values.get("title", ""),
                start_date=normal_date(start_date),
                end_date=normal_date(end_date),
                venue=values.get("venue", "") or clean(source.get("venue")),
                address=values.get("address", "") or clean(source.get("address")),
                commune=values.get("commune", "") or source.commune,
                city=values.get("city", "") or source.city or source.commune,
                region=values.get("region", "") or source.region,
                postal_code=values.get("postal_code", ""),
                latitude=values.get("latitude", ""),
                longitude=values.get("longitude", ""),
                organizer=values.get("organizer", "") or source.organizer,
                categories=card_categories(
                    source,
                    card_text,
                    values.get("categories") or values.get("category"),
                ),
                audience=values.get("audience", ""),
                is_free="true" if free else "",
                description=values.get("description", ""),
                image_url=(
                    urljoin(source.url, values["image_url"]) if values.get("image_url") else ""
                ),
                price="Gratis" if free else values.get("price", ""),
                source_name=source.name,
                source_url=source_url,
                official_url=source_url if source.official else "",
                extracted_at=now_iso(),
                extraction_method="html-cards-css",
            )
            has_required_date = not source.get("require_start_date", False) or bool(
                event.start_date
            )
            if event.title and has_required_date and is_upcoming(event):
                results.append(event)
            if limit > 0 and len(results) >= limit:
                break
        logging.info("  %s: %s fichas por tarjetas HTML", source.name, len(results))
        return results
