"""Resumen operativo de una ejecución completa del recolector."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import OUTPUT_DIR

LOGGER = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Convierte segundos a un valor legible HH:MM:SS."""
    total_seconds = int(round(max(0.0, seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


@dataclass(frozen=True)
class RunSummary:
    """Dos métricas visibles y metadatos mínimos de la última ejecución."""

    total_events: int | None
    duration_seconds: float
    duration: str
    status: str
    finished_at: str


@dataclass(frozen=True)
class SourceSummary:
    """Métricas operativas de una fuente, sin modificar el resumen principal."""

    source: str
    domain: str
    status: str
    events: int
    duration_seconds: float
    http_requests: int
    cache_hits: int
    error: str | None = None


def write_source_summaries(
    summaries: list[SourceSummary], output_dir: Path = OUTPUT_DIR
) -> None:
    """Guarda tiempos por fuente para encontrar cuellos de botella posteriores."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / "source_metrics.json"
        destination.write_text(
            json.dumps([asdict(item) for item in summaries], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.warning("No se pudieron guardar las métricas por fuente: %s", exc)


def load_reconcilable_sources(
    metrics_path: Path = OUTPUT_DIR / "source_metrics.json",
) -> set[str]:
    """Recupera snapshots completos, incluidos los que terminaron legítimamente vacíos."""
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"No existe {metrics_path}; no se puede reconciliar sin métricas de fuentes."
        ) from exc
    if not isinstance(payload, list):
        raise ValueError(f"{metrics_path} no contiene una lista de métricas.")
    return {
        str(row.get("source", "")).strip()
        for row in payload
        if isinstance(row, dict)
        and row.get("status") in {"succeeded", "complete_empty"}
        and isinstance(row.get("events"), int)
        and row["events"] >= 0
        and str(row.get("source", "")).strip()
    }


class RunReporter:
    """Publica el resumen en consola, JSON y GitHub Actions."""

    def __init__(self, output_dir: Path = OUTPUT_DIR) -> None:
        self.output_dir = output_dir

    def report(
        self,
        total_events: int | None,
        duration_seconds: float,
        *,
        status: str = "succeeded",
    ) -> RunSummary:
        duration = format_duration(duration_seconds)
        summary = RunSummary(
            total_events=total_events,
            duration_seconds=round(max(0.0, duration_seconds), 2),
            duration=duration,
            status=status,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        total_label = str(total_events) if total_events is not None else "NO DISPONIBLE"
        LOGGER.info(
            "RESUMEN FINAL | TOTAL EVENTOS: %s | TIEMPO TOTAL: %s",
            total_label,
            duration,
        )

        self._write_json(summary)
        self._write_github_summary(total_label, duration)
        return summary

    def _write_json(self, summary: RunSummary) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            destination = self.output_dir / "run_summary.json"
            destination.write_text(
                json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("No se pudo guardar el resumen de ejecución: %s", exc)

    @staticmethod
    def _write_github_summary(total_label: str, duration: str) -> None:
        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if not summary_path:
            return

        markdown = (
            "## Resumen de extracción\n\n"
            "| Total de eventos | Tiempo total |\n"
            "|---:|---:|\n"
            f"| {total_label} | {duration} |\n\n"
        )
        try:
            with Path(summary_path).open("a", encoding="utf-8") as summary_file:
                summary_file.write(markdown)
        except OSError as exc:
            LOGGER.warning("No se pudo publicar el resumen de GitHub Actions: %s", exc)
