"""Clasificación común, explicable e idempotente de todas las fuentes.

No se deduce la disciplina del nombre del recinto, del organizador ni de la
fuente. Una etiqueta desconocida se conserva para auditoría, no se inventa
una categoría «Otros». Las fichas de directorio no son ocurrencias de eventos.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from .models import Event
from .text import clean_list, normalized, text_from_html

CLASSIFICATION_VERSION = "2026-10-27.1"
RULES_REVISION = "2026-10-28.2"
CANONICAL_CATEGORIES = (
    "Música", "Teatro", "Danza", "Cine y audiovisual", "Artes visuales",
    "Literatura", "Patrimonio", "Artesanía", "Gastronomía", "Cultura del vino",
    "Circo", "Humor", "Culturas populares", "Diseño", "Artes mediales",
    "Divulgación y pensamiento", "Artes escénicas", "Interdisciplina",
)

ALIASES = {
    "Música": ("musica", "musica popular", "musica clasica", "concierto", "conciertos", "opera",
               "pop", "pop latino", "pop rock", "rock", "rock latino", "hard rock", "jazz",
               "metal", "electronica", "techno", "reggaeton", "cumbia", "baladas", "boleros", "rancheras"),
    "Teatro": ("teatro", "titeres"),
    "Danza": ("danza", "danza performance", "danza - performance"),
    "Cine y audiovisual": ("cine", "audiovisual", "documental"),
    "Artes visuales": ("artes visuales", "fotografia", "artes visuales y mediales"),
    "Literatura": ("literatura", "libros", "lectura", "poesia"),
    "Patrimonio": ("patrimonio", "patrimonio cultural"),
    "Artesanía": ("artesania",),
    "Gastronomía": ("gastronomia", "festivales del vino y la gastronomia"),
    "Cultura del vino": ("vino", "enoturismo", "vendimia", "festivales del vino y la gastronomia"),
    "Circo": ("circo", "artes circenses"),
    "Humor": ("humor", "stand up comedy", "stand up", "comedia"),
    "Culturas populares": ("culturas populares", "folclor", "folklore", "carnavales y celebraciones populares"),
    "Diseño": ("diseno",),
    "Artes mediales": ("artes mediales", "artes visuales y mediales"),
    "Divulgación y pensamiento": ("humanidades", "ideas y pensamiento", "ciencia y sociedad", "ciencias"),
    "Artes escénicas": ("artes escenicas",),
    "Interdisciplina": ("interdisciplina",),
}

CONTENT_TERMS = {
    "Música": ("concierto", "conciertos", "musica", "musical", "jazz", "recital", "opera", "orquesta", "cantautor", "sinfonico", "dj set", "canto", "melodias"),
    "Teatro": ("obra de teatro", "obra teatral", "montaje teatral", "dramaturgia", "teatro experimental", "titeres"),
    "Danza": ("danza", "ballet", "milonga", "tango", "baile", "coreografia", "cueca"),
    "Cine y audiovisual": ("cine", "cinematografico", "documental", "cortometraje", "largometraje", "cineforo"),
    "Artes visuales": ("artes visuales", "fotografia", "fotografias", "pintura", "escultura", "grabado", "acuarela", "ilustracion", "collage", "autorretrato"),
    "Literatura": ("literatura", "poesia", "lectura", "cuentacuentos", "presentacion del libro", "lanzamiento del libro", "feria del libro", "feria de libros", "escritura creativa"),
    "Patrimonio": ("patrimonio", "patrimonial", "memoria historica", "historia local"),
    "Artesanía": ("artesania", "artesanal", "oficios tradicionales", "ceramica", "telar"),
    "Gastronomía": ("gastronomia", "gastronomica", "cocina tradicional", "cocina chilena", "cultura del cafe"),
    "Cultura del vino": ("vendimia", "enoturismo", "cata de vinos", "cultura del vino", "degustacion de vinos"),
    "Circo": ("circo", "circense", "circenses", "malabares"),
    "Humor": ("stand up", "standup", "stand-up", "comedia", "humor", "comediante"),
    "Culturas populares": ("fonda", "fondas", "ramada", "carnaval", "folclor", "folklore", "cosplay", "anime", "tradiciones populares"),
    "Diseño": ("diseno de autor", "diseno textil", "diseno grafico"),
    "Artes mediales": ("arte digital", "artes mediales", "videoarte", "arte sonoro", "instalacion sonora"),
    "Divulgación y pensamiento": ("divulgacion cientifica", "filosofia", "pensamiento critico", "astronomia", "humanidades"),
}

FORMAT_TERMS = {
    "Concierto": ("concierto", "recital"), "Taller": ("taller", "curso"),
    "Exposición": ("exposicion", "muestra"), "Festival": ("festival", "vendimia"),
    "Feria": ("feria",), "Charla": ("charla", "conferencia", "conversatorio"),
    "Proyección": ("proyeccion", "cineforo"), "Visita guiada": ("visita guiada",),
    "Función": ("funcion",), "Encuentro": ("encuentro",),
}
AUDIENCE_ALIASES = {
    "Familiar": ("familiar", "familias"), "Infancias": ("infantil", "infancias", "ninos y ninas"),
    "Personas mayores": ("personas mayores", "adultos mayores", "adulto mayor"),
    "Jóvenes": ("jovenes", "juvenil"),
}
PROGRAMS = {normalized(s) for s in ("PAOCC", "FTB", "ICA3", "Hecho en Biobío", "Festival LOFT", "Biblioverano", "Museos en verano")}
GENERIC = {normalized(s) for s in ("Cultura", "Cultural", "Actividades", "Institucional", "Agenda Ciudad", "Agenda Teatro Municipal", "Vinculación con el Medio", "Escuela de Salud", "Escuela de Ciencias Sociales", "Gestión cultural", "Educación", "Otros", "Sin categorizar", "Museo", "Bibliotecas", "Exposición", "Charla", "Taller", "Feria", "Turismo", "Investigación", "Emprendimiento", "Salud", "Deportes", "Medioambiente", "Medio ambiente")}


def matches(text: str, terms) -> list[str]:
    """Límites de palabra, también ante puntuación; nunca arte dentro de parte."""
    return [term for term in terms if re.search(r"(?<!\w)" + re.escape(normalized(term)) + r"(?!\w)", text)]


def original_categories(event: Event) -> list[str]:
    metadata = event.classification if isinstance(event.classification, dict) else {}
    raw = metadata.get("raw_categories", event.categories)
    return clean_list([text_from_html(c) for c in raw]) if isinstance(raw, list) else []


def evidence_text(event: Event) -> str:
    return text_from_html(" ".join((event.title, event.source_description or event.description, event.ocr_text)))


def input_signature(event: Event) -> str:
    value = [CLASSIFICATION_VERSION, RULES_REVISION, event.title, original_categories(event), event.source_description or event.description, event.ocr_text]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def content_kind(event: Event, raw: list[str]) -> str:
    labels = [normalized(c) for c in raw]
    if any(label.startswith(("guia de libreria", "guia de ferias", "guia de festivales", "directorio")) for label in labels):
        return "directory"
    try:
        years = (datetime.fromisoformat(event.end_date.replace("Z", "+00:00")) - datetime.fromisoformat(event.start_date.replace("Z", "+00:00"))).days >= 365
    except (ValueError, TypeError):
        years = False
    title = normalized(event.title)
    space = any(label in {"museo", "bibliotecas", "libreria"} for label in labels) or title.startswith(("libreria ", "museo ", "casa museo ", "biblioteca "))
    # Una duración larga por sí sola nunca descarta una exposición/programación real.
    if years and space and (title == normalized(event.venue) or title.startswith(("libreria ", "museo ", "casa museo ", "biblioteca "))):
        return "directory"
    return "event"


def classify_event(event: Event) -> dict:
    raw = original_categories(event)
    keys = {normalized(c) for c in raw}
    text = normalized(evidence_text(event))
    categories = []
    evidence = {}
    for category in CANONICAL_CATEGORIES:
        aliases = keys.intersection({normalized(category), *ALIASES.get(category, ())})
        found = matches(text, CONTENT_TERMS.get(category, ()))
        # En el título, «teatro» sí puede describir una actividad; no se usa el recinto.
        if category == "Teatro" and matches(normalized(event.title), ("teatro",)):
            found.append("teatro")
        if aliases or found:
            categories.append(category)
            evidence[category] = sorted(aliases) + found
    previous = event.classification if isinstance(event.classification, dict) else {}
    signature = input_signature(event)
    ai = previous.get("ai_evidence", []) if previous.get("input_signature") == signature and previous.get("version") == CLASSIFICATION_VERSION else []
    valid_ai = []
    for item in ai if isinstance(ai, list) else []:
        if not isinstance(item, dict):
            continue
        category, quote = item.get("category"), item.get("quote")
        if category in CANONICAL_CATEGORIES and isinstance(quote, str) and len(quote.strip()) >= 4 and normalized(quote) in text:
            valid_ai.append({"category": category, "quote": quote})
            if category not in categories:
                categories.append(category)
    formats = [name for name, terms in FORMAT_TERMS.items() if matches(normalized(" ".join(raw)) + " " + text, terms)]
    audience_text = normalized(" ".join(raw) + " " + event.audience + " " + event.title)
    audiences = [name for name, terms in AUDIENCE_ALIASES.items() if matches(audience_text, terms)]
    programs = [label for label in raw if normalized(label) in PROGRAMS]
    known = GENERIC | PROGRAMS | {normalized(c) for c in CANONICAL_CATEGORIES}
    known |= {alias for aliases in ALIASES.values() for alias in aliases}
    known |= {alias for aliases in AUDIENCE_ALIASES.values() for alias in aliases}
    metadata = {
        "version": CLASSIFICATION_VERSION, "rules_revision": RULES_REVISION, "raw_categories": raw,
        "content_kind": content_kind(event, raw), "categories": categories,
        "formats": formats, "audiences": audiences, "programs": programs,
        "unmapped_labels": [label for label in raw if normalized(label) not in known],
        "method": "rules+ai" if valid_ai else "rules", "evidence": evidence,
        "ai_evidence": valid_ai, "input_signature": signature,
        "ai_checked": bool(previous.get("ai_checked") and previous.get("input_signature") == signature and previous.get("version") == CLASSIFICATION_VERSION),
    }
    event.categories = categories
    event.classification = metadata
    return metadata


def classify_events(events: list[Event]) -> None:
    for event in events:
        classify_event(event)
