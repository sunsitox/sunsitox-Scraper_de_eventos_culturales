"""Índice JSON de PuntoTicket y detalles públicos incrementales, sin LLM de extracción."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..extractors import extract_jsonld
from ..models import Event
from ..state import DetailCache, digest
from ..text import clean, clean_list, event_timestamp, is_upcoming, now_iso, text_from_html
from .base import Connector
from .html_cards import ABBREVIATED_SPANISH_MONTHS, spanish_flexible_range
from .registry import register_connector


def puntoticket_dates(value) -> tuple[str, str]:
    """Solo fechas explícitas: nunca interpreta una lista de ciudades como fecha."""
    text = clean(value)
    numeric = re.findall(r"\b(?:20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/](?:20\d{2}|\d{2}))\b", text)
    clocks = re.findall(r"\b(\d{1,2}):([0-5]\d)\b", text)
    if numeric:
        dates = []
        for raw in (numeric[0], numeric[-1]) if len(numeric) > 1 else (numeric[0],):
            try:
                if raw[:4].isdigit() and raw[4:5] == "-":
                    moment = datetime.strptime(raw, "%Y-%m-%d")
                else:
                    pieces = re.split(r"[-/]", raw)
                    year = int(pieces[2]) + (2000 if len(pieces[2]) == 2 else 0)
                    moment = datetime(year, int(pieces[1]), int(pieces[0]))
                dates.append(moment.isoformat())
            except ValueError:
                return "", ""
        start, end = dates[0], dates[-1] if len(dates) > 1 else ""
    else:
        for short, full in ABBREVIATED_SPANISH_MONTHS.items():
            text = re.sub(rf"\b{short}\.?\b", full, text, flags=re.I)
        months = "|".join(ABBREVIATED_SPANISH_MONTHS.values())
        if not re.search(rf"\b\d{{1,2}}(?:\s+de)?\s+(?:{months})\b", text, re.I) or not re.search(r"\b20\d{2}\b", text):
            return "", ""
        try:
            start, end = spanish_flexible_range(text)
        except (ValueError, IndexError):
            return "", ""
    if not event_timestamp(start) or (end and not event_timestamp(end)):
        return "", ""
    if clocks:
        if any(int(hour) > 23 for hour, _ in clocks):
            return "", ""
        start = start[:11] + f"{int(clocks[0][0]):02d}:{clocks[0][1]}:00"
        if end:
            end = end[:11] + (f"{int(clocks[-1][0]):02d}:{clocks[-1][1]}:00" if len(clocks) > 1 else "00:00:00")
    if end and end[:10] < start[:10]:
        return "", ""
    return start, end


def listing_categories(html, source):
    result = {}
    for card in BeautifulSoup(html or "", "html.parser").select(".event-item"):
        anchor, category = card.select_one("a[href]"), card.select_one(".descripcion")
        if anchor and category:
            text = clean(category.get_text(" "))
            result[urljoin(source.url, anchor["href"]).rstrip("/")] = text.rsplit("/", 1)[-1].strip() if "/" in text else ""
    return result


@register_connector("puntoticket_json")
class PuntoTicketConnector(Connector):
    def collect(self, source):
        self.collection_complete = True
        rows = self.http.get_json(source["api_url"], verify=bool(source.get("verify_ssl", True)))
        if not isinstance(rows, list) or not rows:
            raise ValueError("PuntoTicket: catálogo JSON vacío o incompatible")
        categories = {}
        try:
            categories = listing_categories(self.http.get_html(source.url), source)
        except requests.RequestException:
            logging.warning("%s: sin etiquetas de portada; se conserva el catálogo JSON", source.name)
        cache = DetailCache(self.state, source) if self.state else None
        events, seen, undated = [], set(), 0
        for position, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                self.collection_complete = False
                continue
            permalink = clean(row.get("permalink"))
            if not permalink:
                self.collection_complete = False
                continue
            url = urljoin(source.url, permalink)
            target = urlparse(url)
            if target.scheme != "https" or target.hostname not in {"www.puntoticket.com", "puntoticket.com"} or url in seen:
                continue
            seen.add(url)
            start, end = puntoticket_dates(row.get("date"))
            if not start:
                # Giras sin funciones fechadas, abonos y páginas agrupadoras no son ocurrencias.
                undated += 1
                continue
            location = text_from_html(row.get("location"))
            place = location.rsplit(" - ", 1)
            event = Event(title=text_from_html(row.get("title")), start_date=start, end_date=end,
                venue=place[0] if location != "-" else "", commune=place[1] if len(place) == 2 else "",
                image_url=clean(row.get("image")), categories=clean_list([categories.get(url.rstrip("/"))]),
                source_name=source.name, source_url=url, official_url="", country="Chile",
                extracted_at=now_iso(), extraction_method="puntoticket-json+jsonld")
            if not event.title or not is_upcoming(event):
                continue
            revision = digest(row)
            cached = cache.load(url, revision) if cache else None
            if cached is not None:
                events.extend(item for item in cached if is_upcoming(item))
                continue
            try:
                html = self.http.get_html(url, verify=bool(source.get("verify_ssl", True)))
                details = extract_jsonld(html or "", source, url)
                if len(details) == 1:
                    detail = details[0]
                    event.categories = clean_list(event.categories + detail.categories)
                    for field in ("venue", "address", "commune", "region", "postal_code", "latitude", "longitude", "organizer", "description", "price", "currency"):
                        if clean(getattr(detail, field)):
                            setattr(event, field, getattr(detail, field))
                soup = BeautifulSoup(html or "", "html.parser")
                selector = source.get("detail_description_selector")
                blocks = soup.select(selector) if selector else []
                if len(blocks) == 1:
                    event.description = text_from_html(str(blocks[0])) or event.description
                event.source_description = event.description
                if not html:
                    self.collection_complete = False
                elif cache:
                    cache.save(url, [event], revision)
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in {404, 410}:
                    if cache:
                        cache.save(url, [], revision)
                    continue
                self.collection_complete = False
                if status in {403, 429}:
                    logging.warning("%s: HTTP %s; se suspende este dominio y se conserva el avance", source.name, status)
                    break
                logging.warning("%s: detalle temporalmente no disponible; se conserva la ficha JSON %s", source.name, url)
            events.append(event)
            if position % 50 == 0:
                logging.info("  %s: %s/%s entradas de catálogo revisadas", source.name, position, len(rows))
        logging.info("  %s: %s eventos vigentes; %s entradas sin fecha verificable omitidas", source.name, len(events), undated)
        return events
