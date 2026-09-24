"""Strategy para la API nacional Chile Cultura."""

import logging

from ..config import SourceConfig
from ..models import Event
from ..text import clean, clean_list, is_upcoming, normal_date, now_iso, text_from_html
from .base import Connector
from .registry import register_connector

DEFAULT_API = "https://chilecultura.gob.cl/api/v1.0/eventos/search"


@register_connector("chilecultura_api")
class ChileCulturaConnector(Connector):
    def collect(self, source: SourceConfig) -> list[Event]:
        limit = int(source.get("max_results", 0) or 0)
        page, results = 1, []
        while True:
            page_size = 100 if limit <= 0 else min(100, limit - len(results))
            payload = self.http.get_json(
                source.get("api_url", DEFAULT_API),
                params={"page": page, "page_size": page_size, "status": "approved"},
            )
            rows = payload.get("results", [])
            if not rows:
                break
            for row in rows:
                free = row.get("free")
                event = Event(
                    title=clean(row.get("name")),
                    start_date=normal_date(row.get("start_date")),
                    end_date=normal_date(row.get("end_date")),
                    venue=clean(row.get("venue_name")),
                    commune=clean(row.get("commune")),
                    city=clean(row.get("commune")),
                    region=clean(row.get("region")),
                    organizer=clean(row.get("institution")),
                    categories=clean_list(row.get("main_discipline"))
                    or clean_list(source.get("default_categories", [])),
                    audience=clean(row.get("suggested_audience")),
                    is_free="true" if free is True else "false" if free is False else "",
                    description=text_from_html(row.get("description")),
                    image_url=clean(row.get("image")),
                    price="Gratis" if free is True else "",
                    source_name=source.name,
                    source_url=clean(row.get("url"))
                    or f"https://chilecultura.gob.cl/events/{row.get('id')}/",
                    official_url=clean(row.get("permalink")),
                    extracted_at=now_iso(),
                    extraction_method="chilecultura-api",
                )
                if is_upcoming(event):
                    results.append(event)
                if limit > 0 and len(results) >= limit:
                    break
            if (limit > 0 and len(results) >= limit) or page >= int(
                payload.get("page_count", page)
            ):
                break
            page += 1
        logging.info("  Chile Cultura: %s fichas", len(results))
        return results if limit <= 0 else results[:limit]
