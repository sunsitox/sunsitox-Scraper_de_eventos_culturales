"""Filtro global, explicable y conservador de pertinencia cultural."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import Event
from .paths import OUTPUT_DIR
from .text import normalized


# Dimensiones culturales amplias. Se evita usar por sí solas palabras ambiguas
# como "feria", "expo" o "evento", porque también describen encuentros B2B.
CULTURAL_TERMS = {
    "arte", "artes", "artesania", "artesanal", "audiovisual", "baile",
    "carnaval", "cine", "circo", "comic", "comics", "cosplay", "cultura",
    "cultural", "danza", "diseno", "folclor", "folklore", "fotografia",
    "fonda", "fondas", "cueca", "ramada", "dieciochero", "fiestas patrias",
    "gastronomia", "gastronomica", "gastronomico", "identidad", "literatura", "libro",
    "memoria", "museo", "musica", "musical", "opera", "patrimonio",
    "poesia", "tradicion", "tradicional", "teatro", "territorio", "vino",
    "vendimia", "enoturismo", "cafe", "anime", "videojuego", "videojuegos",
}

PUBLIC_PROGRAM_TERMS = {
    "actividad", "cartelera", "celebracion", "encuentro", "experiencia",
    "festival", "muestra", "programacion", "taller", "visita guiada",
}

# Señales de finalidad predominantemente sectorial/comercial. Nunca excluyen si
# hay una señal cultural sustantiva: una feria gastronómica puede ser pertinente.
INDUSTRY_TERMS = {
    "acuicultura", "construccion", "defensa", "equipamiento clinico",
    "industria minera", "industria naval", "inmobiliaria", "mineria",
    "networking", "oportunidades de negocio", "proveedores", "sector salud",
    "soluciones medicas", "tecnologia sanitaria",
}


@dataclass(frozen=True)
class CulturalDecision:
    event: Event
    decision: str
    reason: str
    cultural_signals: tuple[str, ...] = ()
    industry_signals: tuple[str, ...] = ()


def _matches(text: str, terms: set[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            term
            for term in terms
            if re.search(rf"(?:^|\s){re.escape(term)}(?:$|\s)", text)
        )
    )


def assess_cultural_relevance(event: Event) -> CulturalDecision:
    """Clasifica como aceptado, revisión o excluido con una razón auditable."""
    text = normalized(" ".join([
        event.title,
        " ".join(event.categories),
        event.description,
        event.organizer,
        event.source_name,
    ]))
    cultural = _matches(text, CULTURAL_TERMS)
    public_program = _matches(text, PUBLIC_PROGRAM_TERMS)
    industry = _matches(text, INDUSTRY_TERMS)

    if cultural:
        return CulturalDecision(
            event, "accepted", "Presenta una dimensión cultural explícita.", cultural, industry
        )
    if industry:
        return CulturalDecision(
            event,
            "excluded",
            "Predomina una finalidad industrial o comercial sin dimensión cultural explícita.",
            cultural,
            industry,
        )
    if public_program:
        return CulturalDecision(
            event,
            "review",
            "Es una actividad pública, pero la información no permite afirmar su dimensión cultural.",
            cultural,
            industry,
        )
    return CulturalDecision(
        event,
        "review",
        "No hay evidencia suficiente para aceptar ni excluir automáticamente.",
        cultural,
        industry,
    )


def filter_cultural_events(
    events: list[Event], *, review_action: str = "keep"
) -> tuple[list[Event], list[CulturalDecision]]:
    """Aplica el filtro. Los casos grises se conservan por defecto para evitar pérdidas."""
    decisions = [assess_cultural_relevance(event) for event in events]
    keep_review = review_action.casefold() != "exclude"
    accepted = [
        item.event
        for item in decisions
        if item.decision == "accepted" or (item.decision == "review" and keep_review)
    ]
    return accepted, decisions


def write_cultural_audit(
    decisions: list[CulturalDecision], output_dir: Path = OUTPUT_DIR
) -> None:
    """Guarda todas las decisiones para poder explicar inclusiones y exclusiones."""
    output_dir.mkdir(exist_ok=True)
    payload = [
        {
            "event": asdict(item.event),
            "decision": item.decision,
            "reason": item.reason,
            "cultural_signals": list(item.cultural_signals),
            "industry_signals": list(item.industry_signals),
        }
        for item in decisions
    ]
    (output_dir / "cultural_filter_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
