"""Filtro global, explicable y conservador de pertinencia cultural."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import Event
from .paths import OUTPUT_DIR
from .text import normalized
from .classification import classify_event, evidence_text


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

NON_CULTURAL_TERMS = INDUSTRY_TERMS | {
    "entrenamiento funcional", "basquetbol", "basquetball", "futbol", "natacion",
    "gimnasia", "fitness", "pilates", "dia de spa", "orcid", "vacunacion",
    "control de salud", "acompanamiento telefonico", "servicio de acompanamiento",
}
PRIMARY_SPORT_TERMS = {
    "basquetbol", "basquetball", "futbol", "natacion", "gimnasia", "fitness",
    "entrenamiento funcional", "zumba", "aerobox", "pilates", "acondicionamiento fisico",
    "boxeo", "voleibol", "volleyball", "tenis", "dia de spa",
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
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)
        )
    )


def assess_cultural_relevance(event: Event) -> CulturalDecision:
    """Clasifica como aceptado, revisión o excluido con una razón auditable."""
    metadata = classify_event(event)
    # La reputación/nombre de una institución no demuestra el contenido del evento.
    text = normalized(evidence_text(event))
    cultural = _matches(text, CULTURAL_TERMS - {"cultura", "cultural", "territorio", "memoria", "tradicional", "cafe", "museo"})
    public_program = _matches(text, PUBLIC_PROGRAM_TERMS)
    industry = _matches(text, NON_CULTURAL_TERMS)

    def result(decision, reason):
        metadata.update(decision=decision, reason=reason)
        return CulturalDecision(event, decision, reason, cultural, industry)

    if metadata["content_kind"] == "directory":
        return result("excluded", "Ficha de directorio/espacio cultural, no una ocurrencia de evento.")

    title = normalized(event.title)
    primary_sport = _matches(title, PRIMARY_SPORT_TERMS)
    explicit_art = _matches(title, {"concierto", "danza", "cueca", "folclor", "teatro", "exposicion", "patrimonio"})
    if primary_sport and not explicit_art:
        return result("excluded", "La finalidad principal publicada es deportiva/bienestar; la música de acompañamiento no la convierte en una actividad artística.")
    if _matches(title, {"kung fu", "kung-fu", "karate", "taekwondo"}) and not explicit_art:
        return result("review", "Actividad marcial: debe verificarse si es práctica deportiva o una manifestación cultural; una mención incidental de filosofía no basta.")

    if event.categories:
        return result("accepted", "Presenta contenido o una disciplina cultural explícita; no se usa el nombre de la fuente.")
    if industry:
        return result("excluded", "Predomina una actividad deportiva, de servicio, académica técnica o comercial sin dimensión cultural explícita.")
    if public_program:
        return result("review", "Es una actividad pública, pero falta evidencia de su contenido cultural.")
    if cultural:
        return result("review", "Hay indicios culturales, pero falta una disciplina verificable.")
    return result("review", "No hay evidencia suficiente para aceptar ni excluir automáticamente.")


def filter_cultural_events(
    events: list[Event], *, review_action: str = "quarantine"
) -> tuple[list[Event], list[CulturalDecision]]:
    """Clasificación y publicación son distintas: keep no convierte review en accepted.

    quarantine conserva la revisión como borrador; keep permite publicarla.
    Las supresiones administrativas se comprueban después, en Supabase.
    """
    if review_action not in {"keep", "exclude", "quarantine"}:
        raise ValueError("cultural_review_action debe ser keep, exclude o quarantine")
    decisions = [assess_cultural_relevance(event) for event in events]
    keep_review = review_action.casefold() != "exclude"
    for item in decisions:
        item.event.classification["review_action"] = review_action
        item.event.classification["publication_status"] = (
            "draft" if item.decision == "review" and review_action == "quarantine"
            else "archived" if item.decision == "excluded" else "published"
        )
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
