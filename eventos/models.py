"""Modelo canónico que comparten todos los conectores."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class Event:
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    venue: str = ""
    address: str = ""
    commune: str = ""
    city: str = ""
    region: str = ""
    country: str = "Chile"
    location: str = ""
    location_precision: str = ""
    location_source: str = ""
    postal_code: str = ""
    latitude: str = ""
    longitude: str = ""
    organizer: str = ""
    categories: list[str] = field(default_factory=list)
    audience: str = ""
    is_free: str = ""
    description: str = ""
    source_description: str = ""
    ocr_text: str = ""
    is_rewritten: bool = False
    image_url: str = ""
    price: str = ""
    currency: str = "CLP"
    source_name: str = ""
    source_url: str = ""
    official_url: str = ""
    extracted_at: str = ""
    extraction_method: str = ""
    event_id: str = ""
