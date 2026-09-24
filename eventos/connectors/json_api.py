"""Conector declarativo para APIs JSON paginadas."""

from __future__ import annotations

import logging

from ..config import SourceConfig
from ..mapping import event_from_values, get_path, map_fields
from ..models import Event
from ..text import clean, is_upcoming
from .base import Connector
from .registry import register_connector


@register_connector("json_api")
class JsonApiConnector(Connector):
    """Agrega una API común configurando rutas JSON, sin escribir Python nuevo."""

    def collect(self, source: SourceConfig) -> list[Event]:
        field_map = source.get("field_map", {})
        if not isinstance(field_map, dict) or not field_map.get("title"):
            raise ValueError(f"{source.name} requiere field_map.title")

        api_url = clean(source.get("api_url")) or source.url
        results_path = clean(source.get("results_path"))
        total_pages_path = clean(source.get("total_pages_path"))
        page_param = clean(source.get("page_param", "page"))
        page_size_param = clean(source.get("page_size_param", "page_size"))
        page_size = max(1, min(500, int(source.get("page_size", 100))))
        page = max(0, int(source.get("page_start", 1)))
        max_pages = max(0, int(source.get("max_pages", 0) or 0))
        limit = max(0, int(source.get("max_results", 0) or 0))
        configured_params = source.get("request_params", {})
        if not isinstance(configured_params, dict):
            raise ValueError(f"{source.name} requiere request_params como objeto JSON")

        results: list[Event] = []
        pages_read = 0
        while True:
            params = dict(configured_params)
            if page_param:
                params[page_param] = page
            if page_size_param:
                params[page_size_param] = page_size
            payload = self.http.get_json(
                api_url,
                params=params,
                verify=bool(source.get("verify_ssl", True)),
            )
            rows = get_path(payload, results_path)
            if not isinstance(rows, list):
                raise ValueError(
                    f"{source.name}: results_path='{results_path}' no apunta a una lista"
                )
            if not rows:
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                values = map_fields(row, field_map)
                event = event_from_values(
                    values,
                    source,
                    fallback_url=clean(values.get("source_url")) or source.url,
                    extraction_method="json-api-mapped",
                )
                if event.title and is_upcoming(event):
                    results.append(event)
                if limit and len(results) >= limit:
                    break

            pages_read += 1
            total_pages = get_path(payload, total_pages_path) if total_pages_path else ""
            try:
                reached_last_page = bool(total_pages) and page >= int(total_pages)
            except (TypeError, ValueError):
                reached_last_page = False
            if (
                reached_last_page
                or (max_pages and pages_read >= max_pages)
                or (limit and len(results) >= limit)
                or (not total_pages_path and len(rows) < page_size)
                or not page_param
            ):
                break
            page += 1

        logging.info("  %s: %s fichas por API JSON declarativa", source.name, len(results))
        return results
