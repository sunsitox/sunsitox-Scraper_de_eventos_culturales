"""Strategy para sitios con The Events Calendar REST API."""

from __future__ import annotations

import logging
import re

from ..config import SourceConfig
from ..models import Event
from ..text import clean, clean_list, is_upcoming, normal_date, now_iso, text_from_html
from .base import Connector
from .registry import register_connector


def _names(items: object) -> str:
    if not isinstance(items, list):
        items = [items] if items else []
    return ", ".join(clean(item.get("name")) for item in items if isinstance(item, dict))


@register_connector("tribe_events_api")
class TribeEventsConnector(Connector):
    def collect(self, source: SourceConfig) -> list[Event]:
        limit = int(source.get("max_results", 0) or 0)
        page, results = 1, []
        while True:
            per_page = 100 if limit <= 0 else min(100, limit - len(results))
            payload = self.http.get_json(
                source.get("api_url", source.url),
                params={"page": page, "per_page": per_page},
                verify=bool(source.get("verify_ssl", True)),
            )
            rows = payload.get("events", [])
            for row in rows:
                venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
                cost = clean(row.get("cost"))
                values = (row.get("cost_details") or {}).get("values", [])
                free = bool(re.search(r"gratis|gratuit", cost, re.I)) or values == ["0"]
                event = Event(
                    title=text_from_html(row.get("title")),
                    start_date=normal_date(row.get("start_date")),
                    end_date=normal_date(row.get("end_date")),
                    venue=clean(venue.get("venue")),
                    address=clean(venue.get("address")),
                    commune=clean(venue.get("city")) or source.commune,
                    city=clean(venue.get("city")) or source.city or source.commune,
                    region=source.region or clean(venue.get("province")),
                    postal_code=clean(venue.get("zip")),
                    latitude=clean(venue.get("lat")),
                    longitude=clean(venue.get("lng")),
                    organizer=_names(row.get("organizer")) or source.organizer,
                    categories=clean_list(
                        [
                            item.get("name")
                            for item in row.get("categories", [])
                            if isinstance(item, dict)
                        ]
                    )
                    or clean_list(source.get("default_categories", [])),
                    is_free="true" if free else "false" if cost else "",
                    description=text_from_html(row.get("description")),
                    image_url=clean((row.get("image") or {}).get("url")),
                    price="Gratis" if free else cost,
                    currency=clean((row.get("cost_details") or {}).get("currency_code")) or "CLP",
                    source_name=source.name,
                    source_url=clean(row.get("url")),
                    official_url=clean(row.get("website")) or clean(row.get("url")),
                    extracted_at=now_iso(),
                    extraction_method="tribe-events-api",
                )
                if is_upcoming(event):
                    results.append(event)
                if limit > 0 and len(results) >= limit:
                    break
            total_pages = int(payload.get("total_pages", page) or page)
            if not rows or page >= total_pages or (limit > 0 and len(results) >= limit):
                break
            page += 1
        logging.info("  %s: %s fichas por The Events Calendar", source.name, len(results))
        return results if limit <= 0 else results[:limit]
