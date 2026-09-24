"""Punto de entrada del agregador de eventos culturales."""

import argparse
import logging
import time

from dotenv import load_dotenv

from eventos.analytics import RunReporter, load_reconcilable_sources
from eventos.availability import EventAvailabilityValidator
from eventos.config import SourceRepository
from eventos.consolidation import deduplicate_resolved
from eventos.exporters import DatasetExporter, load_json_dataset
from eventos.paths import ROOT
from eventos.pipeline import EventPipeline
from eventos.sanitization import EventSanitizer
from eventos.services import NvidiaContentEnricher, SupabaseExporter
from eventos.state import StateStore
from eventos.enrichment_queue import EnrichmentQueue
from eventos.services.nvidia import configure_nvidia_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrae y consolida eventos culturales de Chile.")
    parser.add_argument("--phase", choices=("all", "extract", "process"), default="all", help="Extraer y guardar; procesar checkpoint; o ambas fases")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--supabase",
        action="store_true",
        help="exige cargar el resultado en Supabase además de generar archivos locales",
    )
    destination.add_argument(
        "--local-only",
        action="store_true",
        help="genera archivos locales aunque SUPABASE_ENABLED esté activo",
    )
    parser.add_argument(
        "--sync-existing",
        action="store_true",
        help="sube data/events.json a Supabase sin volver a ejecutar el scraping",
    )
    parser.add_argument(
        "--reconcile-existing",
        action="store_true",
        help=(
            "con --sync-existing, elimina de fuentes exitosas las filas que no aparecen "
            "en el último dataset"
        ),
    )
    parser.add_argument(
        "--enrich-existing",
        action="store_true",
        help=(
            "con --sync-existing, reintenta OCR y redacción solo para filas pendientes "
            "o cuyo contenido cambió"
        ),
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if args.sync_existing and args.phase != "all":
        raise SystemExit("--phase no se combina con --sync-existing")
    if args.sync_existing and args.local_only:
        raise SystemExit("--sync-existing no se puede combinar con --local-only")
    if args.reconcile_existing and not args.sync_existing:
        raise SystemExit("--reconcile-existing requiere --sync-existing")
    if args.enrich_existing and not args.sync_existing:
        raise SystemExit("--enrich-existing requiere --sync-existing")
    push = True if args.supabase else False if args.local_only else None
    started_at = time.perf_counter()
    reporter = RunReporter()
    try:
        if args.sync_existing:
            load_dotenv(ROOT / ".env")
            events = load_json_dataset()
            EventSanitizer().apply(events)
            reconcile_sources = None
            if args.reconcile_existing:
                sources = SourceRepository().load()
                EventAvailabilityValidator().apply(events, sources)
                reconcile_sources = load_reconcilable_sources()
            remote = SupabaseExporter.from_env()
            if args.enrich_existing:
                state = StateStore()
                configure_nvidia_state(state)
                events = deduplicate_resolved(events)
                events, enrichment_candidates = remote.prepare_for_enrichment(events)
                enrichment_keys = {event.event_id for event in enrichment_candidates}
                events = deduplicate_resolved(events)
                enrichment_candidates = [
                    event for event in events if event.event_id in enrichment_keys
                ]
                content_stats = NvidiaContentEnricher(EnrichmentQueue(state)).enrich(
                    enrichment_candidates
                )
                logging.info(
                    "Enriquecimiento pendiente: OCR=%s, redacciones=%s",
                    content_stats["ocr"],
                    content_stats["rewritten"],
                )
            events = deduplicate_resolved(events)
            DatasetExporter().export(events)
            remote.export(
                events,
                reconcile_sources=reconcile_sources,
            )
        else:
            events = EventPipeline().run(push_to_supabase=push, phase=args.phase)
    except Exception:
        reporter.report(None, time.perf_counter() - started_at, status="failed")
        raise
    reporter.report(len(events), time.perf_counter() - started_at)


if __name__ == "__main__":
    main()
