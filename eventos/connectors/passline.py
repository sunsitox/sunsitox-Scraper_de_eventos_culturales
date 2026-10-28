"""Catálogo público de Passline Chile: búsquedas JSON de solo lectura y paginadas."""
from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlparse

import requests

from ..models import Event
from ..state import digest
from ..text import clean, clean_list, is_upcoming, now_iso, text_from_html
from .base import Connector
from .registry import register_connector


def passline_timestamp(day, clock) -> str:
    """Nunca completa una fecha incompleta con el día/año actual."""
    day = clean(day)
    if len(day) != 10:
        return ""
    try:
        return datetime.fromisoformat(day + "T" + (clean(clock) or "00:00:00")).isoformat()
    except ValueError:
        return ""


def passline_event(row, source) -> Event | None:
    url = clean(row.get("url"))
    target = urlparse(url)
    if target.scheme != "https" or target.hostname not in {"passline.com", "www.passline.com"}:
        return None
    start = passline_timestamp(row.get("fecha_inicio"), row.get("hora_inicio"))
    end_day = clean(row.get("fecha_termino"))
    end = passline_timestamp(end_day, row.get("hora_termino")) if end_day else ""
    if not start or (end_day and not end) or (end and end < start):
        return None
    title = text_from_html(row.get("nombre"))
    if not title:
        return None
    price = clean(row.get("precio_min"))
    try:
        is_free = "true" if float(price) == 0 else "false"
    except (ValueError, TypeError):
        is_free = ""
    categories = clean_list([row.get("category_name"), row.get("upper_category_name")])
    description = text_from_html(row.get("descripcion") or row.get("description") or "")
    venue = "Online" if str(row.get("streaming")) == "1" else text_from_html(row.get("lugar"))
    return Event(
        title=title, start_date=start, end_date=end, venue=venue,
        commune=text_from_html(row.get("nombre_communa")),
        region=source.get("region_by_code", {}).get(clean(row.get("region")), ""),
        organizer=text_from_html(row.get("nombre_productor") or row.get("organizer_name") or ""),
        categories=categories, description=description, source_description=description,
        image_url=clean(row.get("miniatura") or row.get("recorte")),
        price=price, is_free=is_free,
        currency="CLP", country="Chile", source_name=source.name,
        source_url=url, official_url="", extracted_at=now_iso(), extraction_method="passline-public-json",
    )


@register_connector("passline_json")
class PasslineConnector(Connector):
    def collect(self, source):
        self.collection_complete = True
        page_size = max(1, min(100, int(source.get("page_size", 100))))
        max_pages = max(0, int(source.get("max_pages", 0)))
        results, seen, page_signatures = [], set(), set()
        offset, pages = 0, 0
        invalid = 0
        while True:
            body = dict(source.get("request_body", {}))
            if body.get("country") != "chile":
                raise ValueError("Passline requiere seleccionar explícitamente Chile")
            body.update(limit=f"{offset},{page_size}", offset="1")
            try:
                rows = self.http.post_json(source["api_url"], json_body=body,
                    verify=bool(source.get("verify_ssl", True)))
            except (requests.RequestException, ValueError):
                if not pages:
                    raise
                self.collection_complete = False
                logging.warning("%s: paginación interrumpida en offset %s; se conservan %s eventos", source.name, offset, len(results))
                break
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                if not pages:
                    raise ValueError("Passline cambió el formato del catálogo JSON")
                self.collection_complete = False
                logging.warning("%s: formato JSON incompatible en offset %s; se conserva el avance", source.name, offset)
                break
            if not rows:
                if not pages and not source.get("allow_empty_catalogue", False):
                    raise ValueError("Passline: catálogo nacional inesperadamente vacío")
                break
            signature = digest([row.get("id") or row.get("url") for row in rows])
            if signature in page_signatures or all((row.get("id") or row.get("url")) in seen for row in rows):
                self.collection_complete = False
                logging.warning("%s: página repetida; se detiene sin autorizar conciliación", source.name)
                break
            page_signatures.add(signature)
            for row in rows:
                identity = row.get("id") or row.get("url")
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    event = passline_event(row, source)
                except (ValueError, TypeError):
                    event = None
                if event is None:
                    invalid += 1
                    self.collection_complete = False
                    continue
                if is_upcoming(event):
                    results.append(event)
            pages += 1
            logging.info("  %s: página %s, %s eventos vigentes acumulados", source.name, pages, len(results))
            if len(rows) < page_size:
                break
            if max_pages and pages >= max_pages:
                self.collection_complete = False
                break
            offset += page_size
        logging.info("  %s: %s vigentes, %s páginas JSON, %s registros inválidos", source.name, len(results), pages, invalid)
        return results
