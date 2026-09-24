"""Salidas operativas de la auditoría de fuentes."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .models import SourceValidationResult


class SourceValidationExporter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def export(self, results: list[SourceValidationResult], elapsed_seconds: float) -> dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = [result.to_dict() for result in results]
        (self.output_dir / "results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        flat = []
        for result in results:
            obs = result.observation
            flat.append({
                "id": result.source.id, "fuente": result.source.name,
                "institucion": result.source.institution, "region": result.source.region,
                "url_solicitada": result.source.url, "url_final": obs.final_url,
                "veredicto": result.verdict, "puntaje": result.score,
                "confianza": result.confidence, "http": obs.status_code,
                "content_type": obs.content_type, "robots_permite": obs.robots_allowed,
                "eventos_jsonld": obs.structured_event_count,
                "senales_fecha": obs.date_signal_count, "enlaces_eventos": obs.event_link_count,
                "duracion_segundos": obs.elapsed_seconds, "error": obs.error,
                "motivos": " | ".join(result.reasons), "revisada_el": result.checked_at,
            })
        with (self.output_dir / "results.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(flat[0]) if flat else ["id"])
            writer.writeheader()
            writer.writerows(flat)

        approved = [result.source.to_dict() | {"validation_score": result.score} for result in results if result.verdict == "aprobada"]
        review = [result.to_dict() for result in results if result.verdict == "revision_manual"]
        (self.output_dir / "approved_sources.json").write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.output_dir / "review_queue.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        counts = Counter(result.verdict for result in results)
        summary = {
            "total": len(results), "approved": counts["aprobada"],
            "manual_review": counts["revision_manual"], "rejected": counts["rechazada"],
            "not_applicable": counts["no_aplica"], "elapsed_seconds": round(elapsed_seconds, 2),
            "approval_rate_over_digital": round(
                counts["aprobada"] / max(1, len(results) - counts["no_aplica"]) * 100, 2
            ),
        }
        (self.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
