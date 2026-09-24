"""Extracción de eventos Schema.org en JSON-LD."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..models import Event
from ..text import clean, clean_list, normal_date, now_iso


def flatten_jsonld(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from flatten_jsonld(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from flatten_jsonld(node["@graph"])
        else:
            yield node


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def event_from_jsonld(node: dict[str, Any], source: SourceConfig, page_url: str) -> Event:
    location = _first(node.get("location") or {}) or {}
    address = location.get("address", {}) if isinstance(location, dict) else {}
    if isinstance(address, str):
        address_text, commune, address = address, source.commune, {}
    else:
        address_text = clean(address.get("streetAddress"))
        commune = clean(address.get("addressLocality")) or source.commune
    offers = _first(node.get("offers") or {}) or {}
    geo = location.get("geo", {}) if isinstance(location, dict) else {}
    organizer = _first(node.get("organizer") or {}) or {}
    organizer_name = (
        clean(organizer.get("name"))
        if isinstance(organizer, dict)
        else clean(organizer)
    )
    image = _first(node.get("image") or "")
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl") or ""
    return Event(
        title=clean(node.get("name")),
        start_date=normal_date(node.get("startDate")),
        end_date=normal_date(node.get("endDate")),
        venue=clean(location.get("name") if isinstance(location, dict) else ""),
        address=address_text,
        commune=commune,
        city=clean(address.get("addressLocality")) or source.city or source.commune,
        region=clean(address.get("addressRegion")) or source.region,
        country=clean(address.get("addressCountry")) or "Chile",
        postal_code=clean(address.get("postalCode")),
        latitude=clean(geo.get("latitude")) if isinstance(geo, dict) else "",
        longitude=clean(geo.get("longitude")) if isinstance(geo, dict) else "",
        organizer=organizer_name or source.organizer,
        categories=clean_list(node.get("eventType") or node.get("genre"))
        or clean_list(source.get("default_categories", [])),
        description=clean(node.get("description")),
        image_url=clean(image),
        price=clean(offers.get("price")) if isinstance(offers, dict) else "",
        currency=clean(offers.get("priceCurrency")) if isinstance(offers, dict) else "CLP",
        source_name=source.name,
        source_url=page_url,
        official_url=clean(node.get("url")) if source.official else "",
        extracted_at=now_iso(),
        extraction_method="json-ld",
    )


def extract_jsonld(html: str, source: SourceConfig, page_url: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[Event] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in flatten_jsonld(data):
            kind = node.get("@type", "")
            is_event = "Event" in kind if isinstance(kind, list) else "Event" in str(kind)
            if is_event:
                results.append(event_from_jsonld(node, source, page_url))
    return results
