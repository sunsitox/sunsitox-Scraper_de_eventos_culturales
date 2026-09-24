"""Strategy declarativa para tipos de contenido WordPress estructurados."""

from __future__ import annotations

import logging
from ..config import SourceConfig
from ..mapping import event_from_values, get_path, map_fields
from ..models import Event
from ..text import is_upcoming
from .base import Connector
from .registry import register_connector


@register_connector("wordpress_rest")
class WordPressRestConnector(Connector):
    def collect(self, source: SourceConfig) -> list[Event]:
        field_map = source.get("field_map", {})
        if not isinstance(field_map, dict) or not field_map.get("title"):
            raise ValueError(f"{source.name} requiere field_map.title")
        limit = int(source.get("max_results", 0) or 0)
        page, results = 1, []
        while True:
            per_page = 100 if limit <= 0 else min(100, limit - len(results))
            response = self.http.get_response(
                source.get("api_url", source.url),
                params={"page": page, "per_page": per_page, "status": "publish", "_embed": 1},
                verify=bool(source.get("verify_ssl", True)),
            )
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                values = map_fields(row, field_map)
                event = event_from_values(
                    values,
                    source,
                    fallback_url=str(row.get("link") or ""),
                    extraction_method="wordpress-rest-mapped",
                )
                if is_upcoming(event):
                    results.append(event)
                if limit > 0 and len(results) >= limit:
                    break
            total_pages = int(response.headers.get("X-WP-TotalPages", page))
            if page >= total_pages or (limit > 0 and len(results) >= limit):
                break
            page += 1
        logging.info("  %s: %s fichas por WordPress REST", source.name, len(results))
        return results if limit <= 0 else results[:limit]
