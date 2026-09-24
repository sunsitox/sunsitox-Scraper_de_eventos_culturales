"""Asistente interactivo para crear configuraciones de fuentes genéricas."""

from __future__ import annotations

import json

from .config import Settings
from .paths import SOURCES_DIR
from .text import clean, slug


CONNECTORS = {
    "generic": "Página común: JSON-LD y respaldo NVIDIA",
    "wordpress_rest": "API REST de WordPress con mapeo de campos",
    "json_api": "API JSON paginada configurable",
    "html_cards": "Cartelera HTML con selectores CSS",
    "tribe_events_api": "WordPress con The Events Calendar",
}


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def run_wizard() -> None:
    regions = Settings.load().target_regions
    print("\nRegiones configuradas:")
    for index, region in enumerate(regions, start=1):
        print(f"  {index}. {region}")
    choice = ask("Número de región", "1")
    try:
        region = regions[int(choice) - 1]
    except (ValueError, IndexError):
        raise SystemExit("La región seleccionada no es válida.")

    name = ask("Nombre de la fuente")
    url = ask("URL de la agenda o cartelera")
    if not name or not url.startswith(("http://", "https://")):
        raise SystemExit("Debes indicar un nombre y una URL HTTP/HTTPS válida.")
    category_text = ask("Categorías predeterminadas, separadas por ;", "Cultura")
    commune = ask("Comuna, si corresponde")
    city = ask("Ciudad, si es distinta de la comuna", commune)
    default_venue = ask("Recinto asumido para toda la fuente, si corresponde")
    default_address = ask("Dirección asumida para toda la fuente, si corresponde")
    default_postal_code = ask("Código postal común, si corresponde")
    default_latitude = ask("Latitud común comprobada, si corresponde")
    default_longitude = ask("Longitud común comprobada, si corresponde")
    print("\nTipos de conector disponibles:")
    for connector_name, description in CONNECTORS.items():
        print(f"  {connector_name}: {description}")
    connector = ask("Tipo de conector", "generic")
    if connector not in CONNECTORS:
        raise SystemExit(f"Conector no reconocido: {connector}")
    source = {
        "name": name,
        "url": url,
        "connector": connector,
        "region": region,
        "commune": commune,
        "city": city,
        "organizer": ask("Organizador predeterminado, si corresponde"),
        "default_categories": [
            clean(category) for category in category_text.split(";") if clean(category)
        ],
        "official": ask("¿Es una fuente oficial? (s/n)", "s").lower().startswith("s"),
        "max_pages": 30 if connector == "generic" else 0,
        "max_results": 0,
        "enabled": True,
    }
    if any((default_venue, default_address, default_postal_code, default_latitude, default_longitude)):
        source["location_defaults"] = {
            key: value
            for key, value in {
                "venue": default_venue,
                "address": default_address,
                "postal_code": default_postal_code,
                "latitude": default_latitude,
                "longitude": default_longitude,
            }.items()
            if value
        }
    if connector in {"wordpress_rest", "json_api", "tribe_events_api"}:
        source["api_url"] = ask("URL del endpoint API", url)
    if connector == "wordpress_rest":
        source["field_map"] = {
            "title": ask("Ruta JSON del título", "title.rendered"),
            "start_date": ask("Ruta JSON de fecha inicial", "start_date"),
            "end_date": ask("Ruta JSON de fecha final", "end_date"),
            "description": ask("Ruta JSON de descripción limpia", "excerpt.rendered"),
            "source_url": ask("Ruta JSON de URL oficial", "link"),
        }
    elif connector == "json_api":
        source["results_path"] = ask("Ruta JSON de la lista de resultados", "results")
        source["total_pages_path"] = ask("Ruta del total de páginas, si existe")
        source["page_param"] = ask("Parámetro de número de página", "page")
        source["page_size_param"] = ask("Parámetro de tamaño de página", "page_size")
        source["page_size"] = 100
        source["request_params"] = {}
        source["field_map"] = {
            "title": ask("Ruta JSON del título", "title"),
            "start_date": ask("Ruta JSON de fecha inicial", "start_date"),
            "end_date": ask("Ruta JSON de fecha final", "end_date"),
            "description": ask("Ruta JSON de descripción", "description"),
            "source_url": ask("Ruta JSON de URL oficial", "url"),
        }
    elif connector == "html_cards":
        source["card_selector"] = ask("Selector CSS de cada tarjeta", "article.evento")
        source["selectors"] = {
            "title": ask("Selector CSS del título", "h2"),
            "start_date": ask("Selector CSS de fecha inicial", ".fecha"),
            "description": ask("Selector CSS de descripción", ".descripcion"),
            "source_url": {
                "selector": ask("Selector CSS del enlace", "a"),
                "attribute": "href",
            },
        }
    SOURCES_DIR.mkdir(exist_ok=True)
    path = SOURCES_DIR / f"{slug(name, 'nueva-fuente')}.json"
    if path.exists():
        raise SystemExit(f"Ya existe {path.name}; cambia el nombre o edita ese archivo.")
    path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nFuente creada: {path}")
    print("Ejecuta python scrape_events.py para incorporarla.")
