"""Strategy Playwright para la aplicación municipal Maipú en Común."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..models import Event
from ..paths import PLAYWRIGHT_DIR
from ..text import (
    clean,
    clean_list,
    is_upcoming,
    normal_date,
    normalized,
    now_iso,
    parse_spanish_datetime,
    text_from_html,
)
from .base import Connector
from .registry import register_connector


def event_from_maipu_html(
    html: str, source: SourceConfig, page_url: str
) -> Event | None:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one(".card_title")
    if not title_node:
        return None
    body_text = clean(soup.get_text(" "))
    date_match = re.search(
        r"Desde el (\d{1,2}) de ([a-záéíóúñ]+) de (\d{4})"
        r"(?: de (\d{1,2}:\d{2}) a (\d{1,2}:\d{2}))?",
        body_text,
        re.I,
    )
    start_date = end_date = ""
    if date_match:
        start_date = parse_spanish_datetime(
            *date_match.group(1, 2, 3), date_match.group(4) or "00:00"
        )
        end_date = parse_spanish_datetime(
            *date_match.group(1, 2, 3), date_match.group(5) or date_match.group(4) or "23:59"
        )
    location_text = ""
    for node in soup.select(".q-item__section.card_subtitle"):
        candidate = "\n".join(clean(part) for part in node.stripped_strings if clean(part))
        if "Presencial" in candidate or "Online" in candidate:
            location_text = candidate
            break
    location_text = re.sub(r"^(Presencial|Online)\s*", "", location_text, flags=re.I)
    location_parts = [clean(part) for part in location_text.splitlines() if clean(part)]
    descriptions = [clean(node.get_text(" ")) for node in soup.select(".card_subtitle.text-textSecondary")]
    audience_match = re.search(r"Personas de .+?(?=Lugar y horario:|$)", body_text, re.I)
    categories = [clean(node.get_text(" ")).title() for node in soup.select(".q-card__section button")]
    free = bool(re.search(r"(?:público|publico) y gratuito|actividad gratuita|evento gratuito", body_text, re.I))
    image_url = ""
    for node in soup.select('[style*="background-image"]'):
        match = re.search(r"url\([\"']?([^\"')]+)", node.get("style", ""))
        if match and "logo" not in match.group(1).lower():
            image_url = match.group(1)
            break
    return Event(
        title=clean(title_node.get_text(" ")),
        start_date=start_date,
        end_date=end_date,
        venue=location_parts[0] if location_parts else "",
        address=clean(" ".join(location_parts[1:])),
        commune=source.commune or "Maipú",
        city=source.city or "Santiago",
        region=source.region,
        organizer=source.organizer or "Municipalidad de Maipú",
        categories=clean_list(categories) or clean_list(source.get("default_categories", [])),
        audience=clean(audience_match.group(0)) if audience_match else "",
        is_free="true" if free else "",
        description=max(descriptions, key=len, default=""),
        image_url=image_url,
        price="Gratis" if free else "",
        source_name=source.name,
        source_url=page_url,
        official_url=page_url,
        extracted_at=now_iso(),
        extraction_method="maipu-playwright",
    )


def _description_location(description: str) -> str:
    """Recupera un lugar explícito cuando el backend lo dejó dentro del texto."""
    match = re.search(
        r"(?:lugar|direcci[oó]n|punto de encuentro)\s*:\s*(.+?)"
        r"(?=\s+(?:fecha|d[ií]a|hora|horario|cu[aá]ndo)\s*:|$)",
        description,
        re.I,
    )
    return clean(match.group(1)) if match else ""


def event_from_maipu_record(record: dict[str, Any], source: SourceConfig) -> Event | None:
    """Convierte el registro que ya usa la aplicación, sin reabrir su ficha visual."""
    title = clean(record.get("name"))
    if not title:
        return None
    category = record.get("category") if isinstance(record.get("category"), dict) else {}
    place = record.get("place") if isinstance(record.get("place"), dict) else {}
    description = text_from_html(record.get("description"))
    venue = clean(place.get("name"))
    address = clean(place.get("address"))
    if "direccion en la descripcion" in normalized(venue):
        venue = _description_location(description)
        address = ""
    start_date = clean(record.get("start_date"))
    end_date = clean(record.get("end_date")) or start_date
    start_time = clean(record.get("start_time")) or "00:00"
    end_time = clean(record.get("end_time")) or "23:59"
    record_type = normalized(clean(record.get("type")))
    route = {"activity": "actividad", "workshop": "taller", "service": "servicio"}.get(
        record_type,
        "actividad",
    )
    page_url = urljoin(source.url, f"{route}/{record.get('id')}")
    free = bool(re.search(r"gratis|gratuit|sin costo|entrada liberada", description, re.I))
    event = Event(
        title=title,
        start_date=normal_date(f"{start_date}T{start_time}") if start_date else "",
        end_date=normal_date(f"{end_date}T{end_time}") if end_date else "",
        venue=venue,
        address=address,
        commune=source.commune or "Maipú",
        city=source.city or "Santiago",
        region=source.region,
        organizer=source.organizer or "Municipalidad de Maipú",
        categories=clean_list(category.get("display_name") or category.get("name"))
        or clean_list(source.get("default_categories", [])),
        is_free="true" if free else "",
        description=description,
        image_url=clean(record.get("thumbnail_image")),
        price="Gratis" if free else "",
        source_name=source.name,
        source_url=page_url,
        official_url=page_url,
        extracted_at=now_iso(),
        extraction_method="maipu-browser-api",
    )
    return event if is_upcoming(event) else None


@register_connector("maipu_browser")
class MaipuBrowserConnector(Connector):
    def collect(self, source: SourceConfig) -> list[Event]:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_DIR))
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            logging.warning("  Maipú omitido: instala Playwright con las instrucciones del README")
            return []
        max_pages = int(source.get("max_pages", 0) or 0)
        category_filter = clean(source.get("category_filter", "Cultura")).lower()
        request_interval = max(0.8, float(source.get("browser_request_interval", 1.2)))
        events: list[Event] = []
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=bool(source.get("headless", False)))
                context = browser.new_context(ignore_https_errors=True, locale="es-CL")
                page = context.new_page()
                page.set_default_timeout(30_000)
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_(),
                )
                with page.expect_response(
                    lambda response: "filter_events/1" in response.url,
                    timeout=30_000,
                ) as first_response:
                    page.goto(source.url, wait_until="domcontentloaded")
                first_payload = first_response.value.json()
                first_page = first_payload.get("data", {})
                available_pages = int(first_page.get("last_page", 1) or 1)
                pages_to_read = (
                    available_pages if max_pages <= 0 else min(max_pages, available_pages)
                )
                logging.info("  Maipú en Común: recorriendo %s páginas", pages_to_read)
                for page_number in range(1, pages_to_read + 1):
                    if page_number == 1:
                        payload = first_payload
                    else:
                        started = time.monotonic()
                        payload = page.evaluate(
                            """
                            async ({url}) => {
                              const response = await fetch(url, {
                                method: "POST",
                                headers: {"Content-Type": "application/json"},
                                body: "{}"
                              });
                              if (!response.ok) throw new Error(`HTTP ${response.status}`);
                              return await response.json();
                            }
                            """,
                            {
                                "url": (
                                    "https://maipu-mec-backend.tchile.com/"
                                    f"api/v2/workshops/filter_events/{page_number}"
                                )
                            },
                        )
                        elapsed = time.monotonic() - started
                        if elapsed < request_interval:
                            page.wait_for_timeout(int((request_interval - elapsed) * 1000))
                    page_data = payload.get("data", {}) if isinstance(payload, dict) else {}
                    records = page_data.get("data", []) if isinstance(page_data, dict) else []
                    for record in records:
                        if not isinstance(record, dict):
                            continue
                        category = record.get("category") or {}
                        category_name = clean(
                            category.get("display_name") if isinstance(category, dict) else ""
                        ).lower()
                        if category_filter and category_filter not in category_name:
                            continue
                        event = event_from_maipu_record(record, source)
                        if event:
                            events.append(event)
                    logging.info(
                        "  Maipú en Común: página %s de %s completada (%s eventos acumulados)",
                        page_number,
                        pages_to_read,
                        len(events),
                    )
                browser.close()
        except PlaywrightTimeoutError as exc:
            logging.warning("  Maipú no terminó de cargar: %s", exc)
        except Exception as exc:
            logging.warning("  Maipú omitido por error del navegador: %s", exc)
        logging.info("  Maipú en Común: %s fichas", len(events))
        return events
