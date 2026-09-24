"""Mapeo reutilizable desde diccionarios externos al modelo Event."""

from __future__ import annotations

from typing import Any

from .config import SourceConfig
from .models import Event
from .text import clean, clean_list, normal_date, now_iso, text_from_html


def get_path(payload: Any, path: str) -> Any:
    """Obtiene rutas como title.rendered o _embedded.wp:term.1.0.name."""
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return ""
        elif isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    return current


def map_fields(payload: Any, field_map: dict[str, Any]) -> dict[str, Any]:
    """Mapea cada campo; una lista de rutas funciona como cadena de alternativas."""
    values: dict[str, Any] = {}
    for field, configured_path in field_map.items():
        paths = configured_path if isinstance(configured_path, list) else [configured_path]
        value: Any = ""
        for path in paths:
            if not isinstance(path, str):
                continue
            value = get_path(payload, path)
            if value not in (None, "", [], {}):
                break
        values[field] = value
    return values


def event_from_values(
    values: dict[str, Any],
    source: SourceConfig,
    *,
    fallback_url: str = "",
    extraction_method: str,
) -> Event:
    """Construye el modelo canónico para conectores REST declarativos."""
    source_url = clean(values.get("source_url")) or clean(fallback_url) or source.url
    official_url = clean(values.get("official_url"))
    if not official_url and source.official:
        official_url = source_url
    return Event(
        title=text_from_html(values.get("title")),
        start_date=normal_date(values.get("start_date")),
        end_date=normal_date(values.get("end_date")),
        venue=text_from_html(values.get("venue")),
        address=text_from_html(values.get("address")),
        commune=text_from_html(values.get("commune")) or source.commune,
        city=text_from_html(values.get("city")) or source.city or source.commune,
        region=text_from_html(values.get("region")) or source.region,
        country=text_from_html(values.get("country")) or "Chile",
        postal_code=text_from_html(values.get("postal_code")),
        latitude=clean(values.get("latitude")),
        longitude=clean(values.get("longitude")),
        organizer=text_from_html(values.get("organizer")) or source.organizer,
        categories=clean_list(values.get("categories") or values.get("category"))
        or clean_list(source.get("default_categories", [])),
        audience=text_from_html(values.get("audience")),
        is_free=clean(values.get("is_free")),
        description=text_from_html(values.get("description")),
        image_url=clean(values.get("image_url")),
        price=text_from_html(values.get("price")),
        currency=clean(values.get("currency")) or "CLP",
        source_name=source.name,
        source_url=source_url,
        official_url=official_url,
        extracted_at=now_iso(),
        extraction_method=extraction_method,
    )
