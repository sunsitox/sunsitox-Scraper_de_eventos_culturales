"""Normalización, deduplicación y filtro geográfico del conjunto final."""

import hashlib
from urllib.parse import urldefrag

from .config import Settings
from .models import Event
from .text import clean, normalized


def fingerprint(event: Event) -> str:
    location_key = "|".join(
        value for value in (normalized(event.venue), normalized(event.commune)) if value
    )
    # Cuando la fuente no publica ubicación, el origen evita fusionar por accidente
    # dos actividades homónimas celebradas a la misma hora en lugares desconocidos.
    origin_key = "" if location_key else "|".join(
        value
        for value in (normalized(event.source_name), original_event_url(event))
        if value
    )
    raw = "|".join(
        [
            normalized(event.title),
            event.start_date[:10],
            location_key,
            origin_key,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def original_event_url(event: Event) -> str:
    """URL canónica publicada para comparar el mismo evento entre ejecuciones."""
    value = clean(event.source_url) or clean(event.official_url)
    return urldefrag(value)[0].rstrip("/").casefold()


def deduplicate(events: list[Event]) -> list[Event]:
    unique: dict[str, Event] = {}
    urls: dict[tuple[str, str, str], str] = {}
    semantic: dict[tuple[str, str, str, str], str] = {}
    for event in events:
        if not event.title:
            continue
        event.event_id = fingerprint(event)
        identity = event.event_id
        # Una misma URL puede ser una cartelera o tener varias funciones. Solo se
        # une por URL cuando también coinciden título y fecha/hora publicados.
        url_key = (original_event_url(event), normalized(event.title), event.start_date)
        place = (normalized(event.venue), normalized(event.commune))
        semantic_scope = "" if any(place) else "|".join(
            value
            for value in (normalized(event.source_name), original_event_url(event))
            if value
        )
        semantic_key = (
            normalized(event.title),
            event.start_date,
            *place,
            semantic_scope,
        )
        identity = semantic.get(semantic_key, identity)
        if url_key[0] and event.start_date:
            identity = urls.get(url_key, identity)
            urls[url_key] = identity
        semantic[semantic_key] = identity
        event.event_id = identity
        previous = unique.get(identity)
        if previous is None or (event.official_url and not previous.official_url):
            unique[identity] = event
    return sorted(unique.values(), key=lambda item: (item.start_date, item.title))


def deduplicate_resolved(events: list[Event]) -> list[Event]:
    """Colapsa IDs remotos coincidentes sin recalcular la identidad ya resuelta."""

    def quality(event: Event) -> tuple[int, int, int, int]:
        clock = event.start_date[11:19] if len(event.start_date) >= 19 else ""
        precise_time = bool(clock and clock != "00:00:00")
        populated = sum(
            bool(clean(getattr(event, field)))
            for field in (
                "description", "source_description", "ocr_text", "venue", "address",
                "commune", "region", "organizer", "image_url", "official_url",
            )
        )
        return (
            int(event.is_rewritten),
            int(precise_time),
            populated,
            len(clean(event.description)),
        )

    unique: dict[str, Event] = {}
    for event in events:
        identity = clean(event.event_id) or fingerprint(event)
        event.event_id = identity
        previous = unique.get(identity)
        if previous is None or quality(event) > quality(previous):
            unique[identity] = event
    return sorted(unique.values(), key=lambda item: (item.start_date, item.title))


def filter_regions(events: list[Event], settings: Settings) -> list[Event]:
    targets = {normalized(region) for region in settings.target_regions}
    if not targets:
        return events
    return [
        event
        for event in events
        if normalized(event.region) in targets
        or (settings.include_unknown_region and not clean(event.region))
    ]
