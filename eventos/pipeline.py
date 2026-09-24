"""Orquestación del proceso completo, independiente de cada Strategy."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from dataclasses import asdict, replace
from collections import defaultdict
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from .availability import EventAvailabilityValidator
from .analytics import SourceSummary, write_source_summaries
from .config import Settings, SourceConfig, SourceRepository
from .cultural_filter import filter_cultural_events, write_cultural_audit
from .connectors import ConnectorFactory
from .consolidation import deduplicate, deduplicate_resolved, filter_regions
from .exporters import DatasetExporter
from .http import HttpClient
from .locations import LocationNormalizer
from .models import Event
from .paths import OUTPUT_DIR, ROOT
from .sanitization import EventSanitizer
from .services import (
    NvidiaContentEnricher,
    SupabaseExporter,
    nvidia_request_stats,
    supabase_enabled,
)
from .services.supabase import SupabaseError
from .text import chile_now
from .state import StateStore, digest, event_rows, load_events
from .enrichment_queue import EnrichmentQueue
from .services.nvidia import configure_nvidia_state


class EventPipeline:
    def __init__(
        self,
        repository: SourceRepository | None = None,
        http: HttpClient | None = None,
        exporter: DatasetExporter | None = None,
        supabase_exporter: SupabaseExporter | None = None,
        location_normalizer: LocationNormalizer | None = None,
        sanitizer: EventSanitizer | None = None,
        content_enricher: NvidiaContentEnricher | None = None,
        state: StateStore | None = None,
    ):
        self.repository = repository or SourceRepository()
        self.http = http
        self.exporter = exporter or DatasetExporter()
        self.supabase_exporter = supabase_exporter
        self.location_normalizer = location_normalizer or LocationNormalizer()
        self.sanitizer = sanitizer or EventSanitizer()
        self.content_enricher = content_enricher or NvidiaContentEnricher()
        self.state = state
        self.cycle = None

    @staticmethod
    def _source_domain(source: SourceConfig) -> str:
        endpoint = str(source.get("api_url") or source.url)
        return urlparse(endpoint).netloc.lower().removeprefix("www.") or source.name

    @staticmethod
    def is_source_active_in_month(source: SourceConfig, month: int) -> bool:
        """Respeta la estacionalidad opcional declarada por cada fuente."""
        if not source.enabled:
            return False
        configured_months = source.get("active_months", [])
        if not configured_months:
            return True
        try:
            return int(month) in {int(item) for item in configured_months}
        except (TypeError, ValueError):
            return False

    def _collect_group(
        self,
        group: list[tuple[int, SourceConfig]],
    ) -> tuple[dict[int, list[Event]], list[SourceSummary]]:
        http = self.http or HttpClient()
        if self.state:
            http.pipeline_state = self.state
        factory = ConnectorFactory(http)
        results: dict[int, list[Event]] = {}
        summaries: list[SourceSummary] = []
        try:
            for index, source in group:
                logging.info("Procesando %s", source.name)
                started_at = time.perf_counter()
                requests_before = http.request_count
                cache_hits_before = http.cache_hits
                status = "succeeded"
                error = None
                events: list[Event] = []
                checkpoint_key = digest(dict(source))
                try:
                    checkpoint = self.state.get("sources", checkpoint_key, {}) if self.state else {}
                    resumed = checkpoint.get("cycle") == self.cycle and self.cycle is not None
                    if resumed:
                        events = load_events(checkpoint["events"])
                        logging.info("  %s: retomada desde checkpoint %s", source.name, checkpoint["stage"])
                    else:
                        connector = factory.create(source)
                        events = connector.collect(source)
                        if getattr(connector, "collection_complete", True) is False:
                            status = "partial"
                        if self.state and status == "succeeded":
                            self.state.put("sources", checkpoint_key, {"cycle": self.cycle, "stage": "raw", "events": event_rows(events)})
                    self.sanitizer.apply(events, source)
                    # Reduce duplicados dentro de la fuente antes de reunir todos los dominios.
                    events = deduplicate(events)
                    if not resumed or checkpoint["stage"] != "complete":
                        EventAvailabilityValidator(http).apply(events, [source])
                    if status == "succeeded" and not events:
                        status = "complete_empty"
                    if self.state and status in {"succeeded", "complete_empty"}:
                        self.state.put("sources", checkpoint_key, {"cycle": self.cycle, "stage": "complete", "events": event_rows(events)})
                except Exception as exc:
                    status = "failed"
                    error = str(exc)[:500]
                    logging.exception(
                        "La fuente %s falló y se omitirá sin detener las demás: %s",
                        source.name,
                        exc,
                    )
                duration = time.perf_counter() - started_at
                results[index] = events
                summaries.append(
                    SourceSummary(
                        source=source.name,
                        domain=self._source_domain(source),
                        status=status,
                        events=len(events),
                        duration_seconds=round(duration, 2),
                        http_requests=http.request_count - requests_before,
                        cache_hits=http.cache_hits - cache_hits_before,
                        error=error,
                    )
                )
                if self.state:
                    self.state.put("source_metrics", checkpoint_key, asdict(summaries[-1]))
                logging.info(
                    "  %s completada en %.1f s (%s eventos)",
                    source.name,
                    duration,
                    len(events),
                )
        finally:
            if self.http is None:
                http.close()
        return results, summaries

    def _collect_sources(
        self, sources: list[SourceConfig]
    ) -> tuple[list[Event], list[SourceSummary]]:
        current_month = chile_now().month
        enabled: list[tuple[int, SourceConfig]] = []
        for index, source in enumerate(sources):
            if not self.is_source_active_in_month(source, current_month):
                if source.enabled and source.get("active_months"):
                    logging.info("%s omitida fuera de su temporada activa.", source.name)
                continue
            enabled.append((index, source))
        groups: dict[str, list[tuple[int, SourceConfig]]] = defaultdict(list)
        if self.http is not None:
            groups["injected-client"] = enabled
        else:
            for item in enabled:
                groups[self._source_domain(item[1])].append(item)

        try:
            configured_workers = int(os.getenv("SOURCE_WORKERS", "4"))
        except ValueError:
            logging.warning("SOURCE_WORKERS no es válido; se utilizarán 4 trabajadores.")
            configured_workers = 4
        configured_workers = max(1, min(8, configured_workers))
        worker_count = min(configured_workers, len(groups)) or 1
        logging.info(
            "Extracción paralela: %s trabajadores para %s dominios; cada dominio conserva su ritmo.",
            worker_count,
            len(groups),
        )
        indexed_results: dict[int, list[Event]] = {}
        summaries: list[SourceSummary] = []
        if worker_count == 1:
            for group in groups.values():
                group_results, group_summaries = self._collect_group(group)
                indexed_results.update(group_results)
                summaries.extend(group_summaries)
        else:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="fuente",
            ) as executor:
                futures = [executor.submit(self._collect_group, group) for group in groups.values()]
                for future in concurrent.futures.as_completed(futures):
                    group_results, group_summaries = future.result()
                    indexed_results.update(group_results)
                    summaries.extend(group_summaries)

        all_events = [
            event
            for index in sorted(indexed_results)
            for event in indexed_results[index]
        ]
        summaries.sort(key=lambda item: item.source.casefold())
        return all_events, summaries

    def run(self, push_to_supabase: bool | None = None, phase: str = "all") -> list[Event]:
        load_dotenv(ROOT / ".env")
        if phase not in {"all", "extract", "process"}:
            raise ValueError("Fase desconocida")
        self.state = self.state or StateStore()
        self.state.prune()
        self.cycle = self.state.begin_cycle() if phase != "process" else None
        queue = EnrichmentQueue(self.state)
        self.content_enricher.queue = queue
        self.location_normalizer.enricher.queue = queue
        configure_nvidia_state(self.state)
        settings = Settings.load()
        sources = self.repository.load()
        should_push = phase != "extract" and (
            (self.supabase_exporter is not None or supabase_enabled())
            if push_to_supabase is None
            else push_to_supabase
        )
        remote = (
            self.supabase_exporter or SupabaseExporter.from_env()
            if should_push
            else None
        )
        config_signature = digest([chile_now().month, [dict(source) for source in sources]])
        if phase == "process":
            snapshot = self.state.get("pipeline", "extracted", {})
            if snapshot.get("config") != config_signature or self.state.clock() - snapshot.get("created", 0) > float(os.getenv("CHECKPOINT_MAX_AGE_HOURS", "24")) * 3600:
                raise ValueError("Falta una extracción reciente compatible. Ejecuta primero --phase extract.")
            self.cycle = snapshot["cycle"]
            all_events = load_events(snapshot["events"])
            source_summaries = [SourceSummary(**row) for row in snapshot["summaries"]]
        else:
            all_events, source_summaries = self._collect_sources(sources)
            self.state.put("pipeline", "extracted", {"cycle": self.cycle, "created": self.state.clock(), "config": config_signature, "events": event_rows(all_events), "summaries": [asdict(row) for row in source_summaries]})
        write_source_summaries(source_summaries)
        if phase == "extract":
            logging.info("Extracción guardada: %s eventos. Continúa con --phase process.", len(all_events))
            return all_events
        self.sanitizer.apply(all_events)
        events = deduplicate(all_events)
        # El ID se calcula antes de enriquecer ubicación para mantenerlo estable entre versiones.
        self.location_normalizer.apply_source_defaults(events, sources)
        raw_inputs = {id(event): replace(event) for event in events}
        new_events = events
        if remote is not None:
            try:
                events, new_events = remote.prepare_for_enrichment(events)
                enrichment_keys = {event.event_id for event in new_events}
                events = deduplicate_resolved(events)
                new_events = [event for event in events if event.event_id in enrichment_keys]
            except (requests.RequestException, SupabaseError, KeyError, TypeError, ValueError) as exc:
                logging.warning(
                    "No se pudo consultar la deduplicación previa de Supabase; "
                    "se omitirán OCR, redacción y ubicación IA para proteger la cuota: %s",
                    exc,
                )
                new_events = []
        # Todo evento permitido consulta su versión local; una cola pendiente puede existir
        # aunque Supabase ya tenga la fila. Nunca reincorporamos eventos suprimidos/removidos.
        for event in events:
            queue.reconcile_input(event, raw_inputs[id(event)])
        content_stats = self.content_enricher.enrich(new_events)
        if content_stats["ocr"] or content_stats["rewritten"]:
            logging.info(
                "Enriquecimiento de eventos nuevos: OCR=%s, redacciones=%s",
                content_stats["ocr"],
                content_stats["rewritten"],
            )
        # Una regla estática de recinto puede encontrar evidencia recién leída
        # desde el afiche y resolver una dirección sin inventarla.
        self.location_normalizer.apply_source_defaults(new_events, sources)
        self.location_normalizer.enrich(new_events)
        # Valida también las salidas producidas por OCR, redacción y ubicación IA.
        self.sanitizer.apply(events)
        events = filter_regions(events, settings)
        if settings.cultural_filter_enabled:
            events, cultural_decisions = filter_cultural_events(
                events, review_action=settings.cultural_review_action
            )
            write_cultural_audit(cultural_decisions)
            decision_counts: dict[str, int] = {}
            for item in cultural_decisions:
                decision_counts[item.decision] = decision_counts.get(item.decision, 0) + 1
            logging.info(
                "Filtro cultural: %s",
                ", ".join(
                    f"{name}={count}" for name, count in sorted(decision_counts.items())
                ),
            )
        location_precisions = self.location_normalizer.finalize(events)
        self.exporter.export(events)
        if should_push:
            assert remote is not None
            reconcile_sources = {
                summary.source
                for summary in source_summaries
                if summary.status in {"succeeded", "complete_empty"}
            }
            remote.export(events, reconcile_sources=reconcile_sources)
        self.state.put("pipeline", "published", {"cycle": self.cycle, "events": event_rows(events)})
        if all(
            summary.status in {"succeeded", "complete_empty"}
            for summary in source_summaries
        ):
            self.state.finish_cycle()
        by_source: dict[str, int] = {}
        for event in events:
            by_source[event.source_name] = by_source.get(event.source_name, 0) + 1
        logging.info("Listo: %s eventos únicos", len(events))
        logging.info("Regiones incluidas: %s", ", ".join(settings.target_regions))
        logging.info(
            "Precisión de ubicación: %s",
            ", ".join(f"{name}={count}" for name, count in sorted(location_precisions.items())),
        )
        nvidia_stats = nvidia_request_stats()
        if nvidia_stats["requests"]:
            logging.info(
                "NVIDIA NIM: %s solicitudes, límite %s RPM, espera regulada %.2f s.",
                nvidia_stats["requests"],
                nvidia_stats["rpm_limit"],
                nvidia_stats["waited_seconds"],
            )
        for name, count in sorted(by_source.items()):
            logging.info("  %s: %s", name, count)
        logging.info("Archivos: %s", OUTPUT_DIR)
        if should_push:
            logging.info("Destino remoto: Supabase")
        return events
