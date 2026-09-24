"""Normalización jerárquica de ubicaciones sin ocultar su nivel de precisión."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Protocol

from .config import SourceConfig
from .models import Event
from .text import clean, normalized


ONLINE_MARKERS = ("online", "en linea", "zoom", "streaming", "virtual")
LOCATION_FIELDS = (
    "venue", "address", "commune", "city", "region", "country", "postal_code",
    "latitude", "longitude",
)

# Las fuentes no usan un único catálogo de nombres: Schema.org suele devolver
# "Región Metropolitana", mientras que la configuración y Supabase usan el
# nombre oficial. Mantener esta equivalencia en un solo lugar evita que un
# evento correcto desaparezca por una diferencia meramente editorial.
REGION_ALIASES = {
    "region de arica y parinacota": "Región de Arica-Parinacota",
    "arica y parinacota": "Región de Arica-Parinacota",
    "region de tarapaca": "Región de Tarapacá", "tarapaca": "Región de Tarapacá",
    "region de antofagasta": "Región de Antofagasta", "antofagasta": "Región de Antofagasta",
    "region de atacama": "Región de Atacama", "atacama": "Región de Atacama",
    "region de coquimbo": "Región de Coquimbo", "coquimbo": "Región de Coquimbo",
    "region de valparaiso": "Región de Valparaíso", "valparaiso": "Región de Valparaíso",
    "region metropolitana de santiago": "Región Metropolitana de Santiago",
    "region metropolitana": "Región Metropolitana de Santiago",
    "metropolitana": "Región Metropolitana de Santiago",
    "region del libertador bernardo o higgins": "Región del Libertador Bernardo O'Higgins",
    "region del libertador general bernardo o higgins": "Región del Libertador Bernardo O'Higgins",
    "libertador bernardo o higgins": "Región del Libertador Bernardo O'Higgins",
    "o higgins": "Región del Libertador Bernardo O'Higgins",
    "region del maule": "Región del Maule", "maule": "Región del Maule",
    "region de nuble": "Región de Ñuble", "nuble": "Región de Ñuble",
    "region del biobio": "Región del Biobío", "biobio": "Región del Biobío",
    "region de la araucania": "Región de la Araucanía", "la araucania": "Región de la Araucanía",
    "araucania": "Región de la Araucanía",
    "region de los rios": "Región de Los Ríos", "los rios": "Región de Los Ríos",
    "region de los lagos": "Región de los Lagos", "los lagos": "Región de los Lagos",
    "region de aysen del general carlos ibanez del campo": "Región de Aysén del General Carlos Ibáñez del Campo",
    "aysen": "Región de Aysén del General Carlos Ibáñez del Campo",
    "region de aysen": "Región de Aysén del General Carlos Ibáñez del Campo",
    "region de magallanes y la antartica chilena": "Región de Magallanes y la Antártica Chilena",
    "magallanes": "Región de Magallanes y la Antártica Chilena",
    "region de magallanes y antartica": "Región de Magallanes y la Antártica Chilena",
}


def canonical_region(value: str) -> str:
    """Devuelve el nombre oficial de una región conocida o una cadena vacía."""
    return REGION_ALIASES.get(normalized(value), "")


def region_from_location_text(*values: str) -> str:
    """Reconoce una región escrita como parte de una comuna, dirección o recinto."""
    evidence = normalized(" ".join(clean(value) for value in values))
    for alias, region in sorted(REGION_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in evidence:
            return region
    return ""


class LocationEnricher(Protocol):
    def enrich(self, events: list[Event]) -> int: ...


def is_online_event(event: Event) -> bool:
    place_text = normalized(" ".join([event.venue, event.address]))
    if any(marker in place_text for marker in ONLINE_MARKERS):
        return True
    if clean(event.venue) or clean(event.address):
        return False
    content_text = normalized(" ".join([event.title, event.description]))
    return any(marker in content_text for marker in ONLINE_MARKERS)


def location_precision(event: Event) -> str:
    if is_online_event(event) and not clean(event.address):
        return "online"
    if clean(event.address):
        return "exact"
    if clean(event.latitude) and clean(event.longitude):
        return "coordinates"
    if clean(event.venue):
        return "venue"
    if clean(event.commune):
        return "commune"
    if clean(event.city):
        return "city"
    if clean(event.region):
        return "region"
    return "country"


def build_location_display(event: Event) -> str:
    """Construye una etiqueta legible, evitando repetir comuna o región dentro de la dirección."""
    if is_online_event(event) and not clean(event.address):
        return "Online"

    parts: list[str] = []
    for value in (event.venue, event.address, event.commune or event.city, event.region):
        text = clean(value)
        key = normalized(text)
        if not key:
            continue
        existing = normalized(" ".join(parts))
        if key in existing or (existing and existing in key):
            continue
        parts.append(text)
    return ", ".join(parts) or clean(event.country) or "Chile"


def needs_ai_location(event: Event) -> bool:
    lacks_specific_place = not clean(event.venue) and not clean(event.address)
    lacks_locality = not clean(event.commune) and not clean(event.city)
    return not is_online_event(event) and (lacks_specific_place or lacks_locality)


class LocationNormalizer:
    """Completa ubicación por evidencia, configuración, reglas, IA y fallback geográfico."""

    def __init__(self, enricher: LocationEnricher | None = None):
        if enricher is None:
            from .services.nvidia import NvidiaLocationEnricher

            enricher = NvidiaLocationEnricher()
        self.enricher = enricher

    @staticmethod
    def _source_map(sources: Iterable[SourceConfig]) -> dict[str, SourceConfig]:
        return {source.name: source for source in sources}

    @staticmethod
    def _commune_regions(sources: Iterable[SourceConfig]) -> dict[str, str]:
        result: dict[str, str] = {}
        for source in sources:
            if source.commune and source.region:
                result[normalized(source.commune)] = source.region
            rules = source.get("region_by_commune", {})
            if isinstance(rules, dict):
                for commune, region in rules.items():
                    if clean(commune) and clean(region):
                        result[normalized(commune)] = clean(region)
        return result

    @staticmethod
    def _configured_defaults(source: SourceConfig) -> dict[str, str]:
        configured = source.get("location_defaults", {})
        defaults = configured if isinstance(configured, dict) else {}
        return {
            "venue": clean(defaults.get("venue") or source.get("venue")),
            "address": clean(defaults.get("address") or source.get("address")),
            "commune": clean(defaults.get("commune") or source.commune),
            "city": clean(defaults.get("city") or source.city or source.commune),
            "region": clean(defaults.get("region") or source.region),
            "country": clean(defaults.get("country") or "Chile"),
            "postal_code": clean(defaults.get("postal_code")),
            "latitude": clean(defaults.get("latitude")),
            "longitude": clean(defaults.get("longitude")),
        }

    @staticmethod
    def _infer_commune(event: Event, source: SourceConfig) -> str:
        rules = source.get("commune_by_location", {})
        if not isinstance(rules, dict):
            return ""
        evidence = normalized(" ".join([event.venue, event.address, event.title]))
        for needle, commune in sorted(rules.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized(needle) and normalized(needle) in evidence:
                return clean(commune)
        return ""

    @staticmethod
    def _matched_location_metadata(event: Event, source: SourceConfig) -> dict[str, str]:
        """Busca un recinto conocido declarado en el JSON de la fuente."""
        rules = source.get("location_metadata", {})
        if not isinstance(rules, dict):
            return {}
        evidence = normalized(
            " ".join(
                [event.venue, event.address, event.title, event.source_description, event.ocr_text]
            )
        )
        for needle, values in sorted(rules.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized(needle) and normalized(needle) in evidence and isinstance(values, dict):
                return {
                    field: clean(values.get(field))
                    for field in LOCATION_FIELDS
                    if clean(values.get(field))
                }
        return {}

    def apply_source_defaults(
        self,
        events: list[Event],
        sources: Iterable[SourceConfig],
    ) -> None:
        source_list = list(sources)
        source_map = self._source_map(source_list)
        commune_regions = self._commune_regions(source_list)
        for event in events:
            event.country = clean(event.country) or "Chile"
            source = source_map.get(event.source_name)
            changed_by_default = False
            changed_by_rule = False
            original_region = clean(event.region)
            resolved_region = canonical_region(original_region)
            location_region = region_from_location_text(
                event.commune, event.city, event.address, event.venue
            )
            if resolved_region:
                if event.region != resolved_region:
                    event.region = resolved_region
                    changed_by_rule = True
            elif location_region:
                # FondasChile entrega "Todas" como región y escribe la región
                # real junto a la comuna: "Pucón, La Araucanía".
                event.region = location_region
                changed_by_rule = True
            if source:
                defaults = self._configured_defaults(source)
                matched_metadata = self._matched_location_metadata(event, source)
                inferred_commune = self._infer_commune(event, source)
                for field, value in matched_metadata.items():
                    if not clean(getattr(event, field)) and value:
                        setattr(event, field, value)
                        changed_by_rule = True
                if not clean(event.commune) and inferred_commune:
                    event.commune = inferred_commune
                    changed_by_rule = True
                for field in LOCATION_FIELDS:
                    if not clean(getattr(event, field)) and clean(defaults.get(field)):
                        setattr(event, field, defaults[field])
                        changed_by_default = True
                # Si la fuente es territorial y Schema.org devolvió una ciudad,
                # una etiqueta genérica o un valor no reconocido como región,
                # la configuración declarativa es la evidencia más confiable.
                configured_region = canonical_region(defaults["region"])
                if (
                    configured_region
                    and original_region
                    and not canonical_region(original_region)
                    and not location_region
                ):
                    event.region = configured_region
                    changed_by_rule = True

            if not clean(event.city) and clean(event.commune):
                event.city = clean(event.commune)
                changed_by_rule = True
            if not clean(event.region) and clean(event.commune):
                inferred_region = commune_regions.get(normalized(event.commune), "")
                if inferred_region:
                    event.region = inferred_region
                    changed_by_rule = True

            if is_online_event(event) and not clean(event.venue) and not clean(event.address):
                event.venue = "Online"
                changed_by_rule = True

            if changed_by_rule:
                event.location_source = "deterministic-inference"
            elif changed_by_default:
                event.location_source = "source-default"
            elif not clean(event.location_source):
                event.location_source = "source-data"

    def enrich(self, events: list[Event]) -> int:
        candidates = [event for event in events if needs_ai_location(event)]
        return self.enricher.enrich(candidates) if candidates else 0

    @staticmethod
    def finalize(events: list[Event]) -> Counter[str]:
        precisions: Counter[str] = Counter()
        for event in events:
            event.country = clean(event.country) or "Chile"
            event.location_precision = location_precision(event)
            event.location = build_location_display(event)
            if not clean(event.location_source):
                event.location_source = "geographic-fallback"
            if event.location_precision in {"commune", "city", "region", "country"} and (
                event.location_source == "source-data"
            ):
                event.location_source = "geographic-fallback"
            precisions[event.location_precision] += 1
        return precisions
