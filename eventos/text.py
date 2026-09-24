"""Limpieza de texto, fechas y nombres geográficos."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .models import Event

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
SPANISH_TO_ENGLISH_MONTH = {
    "enero": "January",
    "febrero": "February",
    "marzo": "March",
    "abril": "April",
    "mayo": "May",
    "junio": "June",
    "julio": "July",
    "agosto": "August",
    "septiembre": "September",
    "setiembre": "September",
    "octubre": "October",
    "noviembre": "November",
    "diciembre": "December",
}

# WordPress conserva a veces la maqueta de WPBakery/Visual Composer en los
# campos REST aunque el sitio la renderice visualmente. Las etiquetas pueden
# contener comillas tipográficas y reglas CSS completas.
PAGE_BUILDER_SHORTCODE = re.compile(
    r"\[/?(?:vc|wpb|et_pb|fusion|elementor|mkdf|ux|av)_[^\]]*\]",
    re.IGNORECASE,
)
PAGE_BUILDER_TOKEN = re.compile(
    r"\b(?:vc_custom_\d+|wpb-content-wrapper|vc_(?:row|column|section|custom))\b",
    re.IGNORECASE,
)
CSS_RULE = re.compile(
    r"(?:[.#][a-z_][\w-]*(?:\s+[.#]?[\w-]+)*)?\s*"
    r"\{(?:[^{}]|\{[^{}]*\}){0,4000}\}",
    re.IGNORECASE,
)
CSS_AT_RULE = re.compile(r"@(?:media|supports|keyframes)[^{]*\{.*?\}", re.I | re.S)


def clean(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(clean(item) for item in value if item)
    if isinstance(value, dict):
        return clean(value.get("name") or value.get("@id") or "")
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def clean_list(value: Any) -> list[str]:
    """Normaliza una categoría o una colección y elimina duplicados."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        category = clean(item)
        key = normalized(category)
        if category and key not in seen:
            seen.add(key)
            result.append(category)
    return result


def text_from_html(value: Any) -> str:
    decoded = html.unescape(str(value or ""))
    soup = BeautifulSoup(decoded, "html.parser")
    for hidden in soup.select(
        "script, style, noscript, template, svg, canvas, form, iframe"
    ):
        hidden.decompose()
    visible_text = soup.get_text(" ")
    visible_text = PAGE_BUILDER_SHORTCODE.sub(" ", visible_text)
    visible_text = CSS_AT_RULE.sub(" ", visible_text)
    visible_text = CSS_RULE.sub(" ", visible_text)
    visible_text = PAGE_BUILDER_TOKEN.sub(" ", visible_text)
    visible_text = re.sub(r"!important\b|\bcss\s*=\s*[\"'][^\"']*[\"']", " ", visible_text, flags=re.I)
    return clean(visible_text)


def normal_date(value: Any) -> str:
    value = clean(value)
    if not value:
        return ""
    # Algunas APIs WordPress almacenan fechas ACF como YYYYMMDD. Dateutil las
    # interpreta como YYYYDDMM, por lo que se normalizan antes de delegar.
    compact_ymd = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value)
    if compact_ymd:
        value = "-".join(compact_ymd.groups())
    for spanish, english in SPANISH_TO_ENGLISH_MONTH.items():
        value = re.sub(rf"\b{spanish}\b", english, value, flags=re.I)
    try:
        year_first = bool(re.match(r"^\d{4}-\d{1,2}-\d{1,2}(?:T|\s|$)", value))
        return date_parser.parse(
            value,
            dayfirst=not year_first,
            yearfirst=year_first,
            fuzzy=True,
        ).isoformat()
    except (ValueError, TypeError, OverflowError):
        return value


def event_timestamp(value: Any) -> str:
    """Normaliza una fecha de evento y asigna la zona horaria chilena si falta."""
    parsed = normal_date(value)
    if not parsed:
        return ""
    try:
        moment = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if moment.tzinfo is None:
        try:
            moment = moment.replace(tzinfo=ZoneInfo("America/Santiago"))
        except ZoneInfoNotFoundError:
            moment = moment.replace(tzinfo=chile_now().tzinfo)
    return moment.isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def chile_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("America/Santiago"))
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone()


def is_upcoming(event: Event, current: datetime | None = None) -> bool:
    """Conserva eventos futuros/en curso y respeta la hora final cuando existe."""
    value = event.end_date or event.start_date
    if not value:
        return False
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        now = current or chile_now()
        # Una fecha publicada sin hora se normaliza a medianoche: representa el día completo.
        if moment.time() == time.min:
            return moment.date() >= now.date()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=now.tzinfo)
        return moment >= now.astimezone(moment.tzinfo)
    except (ValueError, TypeError):
        return False


def normalized(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def slug(value: str, fallback: str = "sin-region") -> str:
    return normalized(value).replace(" ", "-") or fallback


def parse_spanish_datetime(day: str, month: str, year: str, clock: str = "00:00") -> str:
    month_number = SPANISH_MONTHS.get(month.lower())
    if not month_number:
        return ""
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(int(year), month_number, int(day), hour, minute).isoformat()
