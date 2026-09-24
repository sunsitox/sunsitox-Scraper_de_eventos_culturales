"""Saneamiento final común para eventos provenientes de cualquier conector."""

from __future__ import annotations

import os
from datetime import datetime, time

from .config import SourceConfig
from .models import Event
from .text import clean, clean_list, event_timestamp, is_upcoming, text_from_html


PLAIN_TEXT_FIELDS = (
    "title",
    "venue",
    "address",
    "commune",
    "city",
    "region",
    "country",
    "location",
    "postal_code",
    "organizer",
    "audience",
    "price",
    "source_name",
    "source_description",
    "ocr_text",
)
URL_FIELDS = ("image_url", "source_url", "official_url")


class EventSanitizer:
    """Normaliza en el mismo objeto y compacta listas para usar menos memoria."""

    def __init__(self, description_max_chars: int | None = None) -> None:
        configured = description_max_chars
        if configured is None:
            try:
                configured = int(os.getenv("EVENT_DESCRIPTION_MAX_CHARS", "8000"))
            except ValueError:
                configured = 8000
        self.description_max_chars = max(500, configured)

    def sanitize(self, event: Event, source: SourceConfig | None = None) -> Event:
        for field_name in PLAIN_TEXT_FIELDS:
            setattr(event, field_name, text_from_html(getattr(event, field_name)))
        for field_name in URL_FIELDS:
            setattr(event, field_name, clean(getattr(event, field_name)))

        event.start_date = event_timestamp(event.start_date)
        event.end_date = event_timestamp(event.end_date)
        if event.end_date:
            try:
                end_moment = datetime.fromisoformat(
                    event.end_date.replace("Z", "+00:00")
                )
                if end_moment.time() == time.min:
                    event.end_date = end_moment.replace(
                        hour=23, minute=59, second=59
                    ).isoformat()
            except ValueError:
                pass
        event.description = text_from_html(event.description)
        event.source_description = text_from_html(event.source_description) or event.description
        event.ocr_text = text_from_html(event.ocr_text)
        for field_name, lower, upper in (
            ("latitude", -90.0, 90.0),
            ("longitude", -180.0, 180.0),
        ):
            raw = clean(getattr(event, field_name)).replace(",", ".")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
            setattr(event, field_name, raw if value is not None and lower <= value <= upper else "")
        description_limit = self.description_max_chars
        if source is not None:
            try:
                description_limit = max(
                    500,
                    int(source.get("description_max_chars", description_limit)),
                )
            except (TypeError, ValueError):
                pass
        if len(event.description) > description_limit:
            event.description = event.description[:description_limit].rstrip() + "…"
        if len(event.source_description) > description_limit:
            event.source_description = event.source_description[:description_limit].rstrip() + "…"
        try:
            ocr_limit = max(500, int(os.getenv("EVENT_OCR_MAX_CHARS", "8000")))
        except ValueError:
            ocr_limit = 8000
        if len(event.ocr_text) > ocr_limit:
            event.ocr_text = event.ocr_text[:ocr_limit].rstrip() + "…"

        event.categories = clean_list(
            [text_from_html(category) for category in event.categories]
        )
        if not event.categories and source is not None:
            event.categories = clean_list(source.get("default_categories", []))
        return event

    def apply(self, events: list[Event], source: SourceConfig | None = None) -> list[Event]:
        """Sanea, elimina inválidos/vencidos y reutiliza la lista recibida."""
        write_index = 0
        for event in events:
            self.sanitize(event, source)
            if not event.title or not event.start_date or not is_upcoming(event):
                continue
            events[write_index] = event
            write_index += 1
        del events[write_index:]
        return events
