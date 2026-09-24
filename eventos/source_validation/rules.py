"""Reglas auditables para decidir si una fuente sirve para scraping cultural."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import FetchObservation, RuleResult, SourceCandidate, SourceValidationResult


CULTURE_TERMS = {
    "arte", "artes", "artesania", "audiovisual", "biblioteca", "cine", "circo",
    "cultura", "cultural", "danza", "exposicion", "festival", "folclor", "gastronomia",
    "literatura", "museo", "musica", "patrimonio", "teatro", "taller", "vendimia", "vino",
}
EVENT_TERMS = {
    "agenda", "actividad", "actividades", "calendario", "cartelera", "evento", "eventos",
    "fecha", "horario", "inscripcion", "programa", "programacion", "proximamente", "temporada",
}
CHILE_TERMS = {
    "chile", "chileno", "chilena", "santiago", "valparaiso", "biobio", "araucania", "coquimbo",
    "maule", "nuble", "antofagasta", "atacama", "tarapaca", "ohiggins", "rancagua", "temuco",
    "valdivia", "puerto montt", "punta arenas", "arica", "iquique", "concepcion", "chillan",
}
BLOCK_TERMS = {
    "domain for sale", "dominio en venta", "account suspended", "sitio suspendido", "parking page",
    "access denied", "captcha", "verify you are human", "under construction",
}
SUPPORTED_CONTENT = {"text/html", "application/xhtml+xml", "application/json", "application/ld+json", "application/pdf"}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _hits(text: str, terms: set[str]) -> list[str]:
    padded = " " + _normalize(text) + " "
    return sorted(term for term in terms if f" {term} " in padded or (" " in term and term in padded))


class SourceRuleEngine:
    """Specification pattern: cada condición produce evidencia y puntaje."""

    def evaluate(self, source: SourceCandidate, obs: FetchObservation) -> SourceValidationResult:
        now = datetime.now(timezone.utc).isoformat()
        if not source.eligible and not source.url:
            return SourceValidationResult(
                source=source, verdict="no_aplica", score=0, confidence="alta", checked_at=now,
                observation=obs,
                rules=[RuleResult("canal_digital", False, 0, "Referencia territorial sin URL de eventos")],
                reasons=["No es todavía una fuente digital; conservar como pista territorial."],
            )
        if not source.url:
            return SourceValidationResult(
                source=source, verdict="rechazada", score=0, confidence="alta", checked_at=now,
                observation=obs,
                rules=[RuleResult("url_valida", False, 0, "La fuente elegible no tiene URL de eventos")],
                reasons=["Falta una URL pública que pueda validarse."],
            )

        metadata = " ".join([source.name, source.institution, source.source_type, source.categories, source.channel, source.region, source.commune])
        page = " ".join([obs.title, obs.visible_text])
        combined = metadata + " " + page
        culture_hits = _hits(combined, CULTURE_TERMS)
        event_hits = _hits(page + " " + source.url, EVENT_TERMS)
        chile_hits = _hits(combined, CHILE_TERMS)
        normalized_page = _normalize(page)
        blocked_hits = [term for term in BLOCK_TERMS if term in normalized_page]
        domain = urlparse(obs.final_url or source.url).netloc.casefold()
        path = urlparse(obs.final_url or source.url).path.casefold()
        supported = obs.content_type in SUPPORTED_CONTENT or any(key in obs.content_type for key in ("html", "json", "pdf"))
        accessible = 200 <= obs.status_code < 400 and not obs.error
        recurring_path = bool(re.search(r"agenda|event|cartelera|programa|calendario|actividad|festival|feria", path))
        chile_signal = domain.endswith(".cl") or bool(chile_hits)
        event_page_signal = (
            obs.structured_event_count > 0
            or obs.event_link_count >= 2
            or (len(event_hits) >= 2 and obs.date_signal_count >= 1)
            or ("pdf" in obs.content_type and len(event_hits) >= 1)
        )

        rules = [
            RuleResult("accesible", accessible, 20 if accessible else 0, obs.error or f"HTTP {obs.status_code}"),
            RuleResult("robots_permite", obs.robots_allowed is not False, 5 if obs.robots_allowed is not False else 0, "permitido o no declarado" if obs.robots_allowed is not False else "robots.txt lo impide"),
            RuleResult("contenido_compatible", supported, 5 if supported else 0, obs.content_type or "sin Content-Type"),
            RuleResult("relevancia_cultural", len(culture_hits) >= 2, min(20, len(culture_hits) * 4), ", ".join(culture_hits[:8]) or "sin términos culturales"),
            RuleResult("lenguaje_de_eventos", len(event_hits) >= 2, min(15, len(event_hits) * 3), ", ".join(event_hits[:8]) or "sin términos de agenda"),
            RuleResult("eventos_estructurados", obs.structured_event_count > 0, 20 if obs.structured_event_count else 0, f"{obs.structured_event_count} objetos Event JSON-LD"),
            RuleResult("fechas_detectadas", obs.date_signal_count > 0, min(10, obs.date_signal_count * 2), f"{obs.date_signal_count} señales"),
            RuleResult("enlaces_de_eventos", obs.event_link_count >= 2, min(10, obs.event_link_count), f"{obs.event_link_count} enlaces"),
            RuleResult("cobertura_chile", chile_signal, 10 if chile_signal else 0, domain or "sin dominio"),
            RuleResult("ruta_recurrente", recurring_path, 5 if recurring_path else 0, path or "/"),
            RuleResult("pagina_no_bloqueada", not blocked_hits, 5 if not blocked_hits else 0, ", ".join(blocked_hits) or "sin indicadores de bloqueo/parking"),
        ]
        score = min(100, sum(rule.points for rule in rules))
        reasons = [rule.detail for rule in rules if rule.passed is False]

        if obs.robots_allowed is False:
            verdict, confidence = "rechazada", "alta"
            reasons.insert(0, "El sitio no permite este agente en robots.txt.")
        elif obs.status_code in {404, 410} or blocked_hits:
            verdict, confidence = "rechazada", "alta"
        elif not accessible:
            verdict, confidence = "revision_manual", "media"
        elif not supported:
            verdict, confidence = "rechazada", "alta"
        elif score >= 65 and len(culture_hits) >= 2 and event_page_signal and chile_signal:
            verdict, confidence = "aprobada", "alta" if score >= 80 else "media"
        elif source.previous_status.casefold() == "integrada activa":
            verdict, confidence = "revision_manual", "alta"
            reasons.insert(0, "La fuente ya está integrada, pero la página pública no aportó evidencia suficiente para aprobarla automáticamente.")
        elif score < 35 or not culture_hits:
            verdict, confidence = "rechazada", "media"
        else:
            verdict, confidence = "revision_manual", "media"
        return SourceValidationResult(
            source=source, verdict=verdict, score=score, confidence=confidence,
            checked_at=now, observation=obs, rules=rules, reasons=reasons[:8],
        )
