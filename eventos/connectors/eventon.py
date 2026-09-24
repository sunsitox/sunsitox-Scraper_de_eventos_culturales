"""Strategy para calendarios EventON publicados en WordPress."""

from __future__ import annotations

import logging
import re
import requests
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..models import Event
from ..state import DetailCache
from ..text import clean, clean_list, is_upcoming, normal_date, normalized, now_iso, text_from_html
from .base import Connector
from .registry import register_connector


def microdata_value(root, prop: str) -> str:
    node = root.select_one(f'[itemprop="{prop}"]') if root else None
    if not node:
        return ""
    return clean(node.get("content") or node.get("href") or node.get_text(" "))


def configured_commune(source: SourceConfig, *values: str) -> str:
    haystack = normalized(" ".join(clean(value) for value in values))
    rules = source.get("commune_by_location", {})
    if not isinstance(rules, dict):
        return source.commune
    for needle, commune in sorted(rules.items(), key=lambda item: len(item[0]), reverse=True):
        if normalized(needle) in haystack:
            return clean(commune)
    return source.commune


def configured_region(source: SourceConfig, commune: str, default: str) -> str:
    rules = source.get("region_by_commune", {})
    if not isinstance(rules, dict):
        return default
    commune_key = normalized(commune)
    for configured_commune_name, region in rules.items():
        if normalized(configured_commune_name) == commune_key:
            return clean(region)
    return default


def event_from_microdata(
    html: str,
    source: SourceConfig,
    page_url: str,
    *,
    title: str = "",
    categories: list[str] | None = None,
    region: str = "",
) -> Event | None:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(
        '[itemtype="http://schema.org/Event"], [itemtype="https://schema.org/Event"]'
    )
    if not root:
        return None
    description = microdata_value(root, "description")
    location = root.select_one('[itemprop="location"]')
    address_root = location.select_one('[itemprop="address"]') if location else None
    venue = microdata_value(location, "name")
    address = microdata_value(address_root, "streetAddress")
    commune = microdata_value(address_root, "addressLocality") or configured_commune(
        source, venue, address
    )
    event_region = region or microdata_value(address_root, "addressRegion") or source.region
    event_region = configured_region(source, commune, event_region)
    organizer_root = root.select_one('[itemprop="organizer"]')
    title_node = root.select_one('.evcal_event_title[itemprop="name"]')
    event_title = clean(title)
    if not event_title and title_node:
        event_title = clean(
            title_node.get("content") or title_node.get("href") or title_node.get_text(" ")
        )
    event_title = event_title or microdata_value(root, "name")
    external_links = [
        anchor.get("href", "")
        for anchor in root.select('[itemprop="description"] a[href]')
        if urlparse(anchor.get("href", "")).netloc
        and urlparse(anchor.get("href", "")).netloc != urlparse(source.url).netloc
    ]
    free = bool(
        re.search(r"entrada\s+(?:liberada|gratuita)|evento\s+gratuito|gratis", description, re.I)
    )
    return Event(
        title=event_title,
        start_date=normal_date(microdata_value(root, "startDate")),
        end_date=normal_date(microdata_value(root, "endDate")),
        venue=venue,
        address=address,
        commune=commune,
        city=commune or source.city or source.commune,
        region=event_region,
        organizer=microdata_value(organizer_root, "name") or source.organizer,
        categories=clean_list(categories)
        or clean_list(microdata_value(root, "eventType"))
        or clean_list(source.get("default_categories", [])),
        is_free="true" if free else "",
        description=description,
        image_url=microdata_value(root, "image"),
        price="Gratis" if free else "",
        source_name=source.name,
        source_url=page_url,
        official_url=external_links[0] if external_links else page_url,
        extracted_at=now_iso(),
        extraction_method="eventon-wordpress-microdata",
    )


@register_connector("santiago_wordpress", "eventon_wordpress")
class EventOnWordPressConnector(Connector):
    def _records(self, source, requested_fields, limit):
        page = 1
        count = 0
        while True:
            response = self.http.get_response(
                source.get("api_url", source.url),
                params={"per_page": 100, "page": page, "status": "publish", "_fields": ",".join(requested_fields)},
                verify=bool(source.get("verify_ssl", True)),
            )
            batch = response.json()
            if not isinstance(batch, list):
                raise ValueError(f"{source.name}: respuesta WordPress inesperada")
            for record in batch:
                yield record
                count += 1
                if limit > 0 and count >= limit:
                    return
            total_pages = int(response.headers.get("X-WP-TotalPages", page))
            if page >= total_pages:
                return
            page += 1

    def _taxonomy(self, source: SourceConfig, route: str) -> dict[int, dict[str, str]]:
        api_base = clean(source.get("wp_api_base"))
        if not api_base or not route:
            return {}
        response = self.http.get_response(
            f"{api_base.rstrip('/')}/{route}",
            params={"per_page": 100},
            verify=bool(source.get("verify_ssl", True)),
        )
        rows = response.json()
        return {
            int(row["id"]): {"name": clean(row.get("name")), "slug": clean(row.get("slug"))}
            for row in rows
            if isinstance(row, dict) and row.get("id")
        }

    def collect(self, source: SourceConfig) -> list[Event]:
        self.collection_complete = True
        limit = int(source.get("max_results", 0) or 0)
        category_field = clean(source.get("category_field"))
        area_field = clean(source.get("area_field"))
        category_terms = self._taxonomy(source, clean(source.get("category_taxonomy")))
        area_terms = self._taxonomy(source, clean(source.get("area_taxonomy")))
        requested_fields = ["id", "link", "title", "modified_gmt"]
        requested_fields.extend(field for field in (category_field, area_field) if field)
        records = self._records(source, requested_fields, limit)
        cache = DetailCache(self.state, source) if self.state and source.get("incremental") else None
        events: list[Event] = []
        default_categories = clean_list(source.get("default_categories", []))
        region_by_area = source.get("region_by_area", {})
        position = 0
        reused = 0
        for position, record in enumerate(records, start=1):
            page_url = clean(record.get("link"))
            revision = clean(record.get("modified_gmt"))
            cached = cache.load(page_url, revision) if cache else None
            if cached is not None:
                events.extend(event for event in cached if is_upcoming(event))
                reused += 1
                continue
            try:
                page_html = self.http.get_html(
                    page_url, verify=bool(source.get("verify_ssl", True))
                )
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in {404, 410}:
                    if cache:
                        cache.save(page_url, [], revision)
                else:
                    self.collection_complete = False
                logging.warning("Ficha omitida en %s: %s", source.name, page_url)
                continue
            dynamic_categories = [
                category_terms[term_id]["name"]
                for term_id in record.get(category_field, [])
                if term_id in category_terms
            ] if category_field else []
            area = next(
                (
                    area_terms[term_id]
                    for term_id in record.get(area_field, [])
                    if term_id in area_terms
                ),
                {},
            ) if area_field else {}
            area_region = ""
            if isinstance(region_by_area, dict) and area:
                area_region = clean(
                    region_by_area.get(area.get("slug")) or region_by_area.get(area.get("name"))
                )
            title = text_from_html((record.get("title") or {}).get("rendered"))
            event = event_from_microdata(
                page_html or "",
                source,
                page_url,
                title=title,
                categories=clean_list(default_categories + dynamic_categories),
                region=area_region,
            )
            if event is None:
                self.collection_complete = False
                logging.warning("%s: faltan microdatos en %s", source.name, page_url)
                continue
            if cache:
                cache.save(page_url, [event], revision)
            if event and is_upcoming(event):
                events.append(event)
            if position % 50 == 0:
                logging.info("  %s: revisadas %s fichas", source.name, position)
        logging.info("  %s: %s eventos vigentes de %s fichas (%s reutilizadas)", source.name, len(events), position, reused)
        return events
