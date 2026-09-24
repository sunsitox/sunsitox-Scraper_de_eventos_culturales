"""Punto de entrada del pipeline de validación de fuentes."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from eventos.paths import OUTPUT_DIR, ROOT
from eventos.source_validation import SourceValidationPipeline
from eventos.source_validation.exporter import SourceValidationExporter
from eventos.source_validation.fetcher import SourceFetcher


DEFAULT_INPUT = ROOT / "data" / "source_candidates.json"
DEFAULT_OUTPUT = OUTPUT_DIR / "source_validation"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida un inventario completo de fuentes culturales sin extraer eventos."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Inventario JSON, CSV o XLSX")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Carpeta de resultados")
    parser.add_argument("--workers", type=int, default=6, help="Dominios que pueden revisarse en paralelo")
    parser.add_argument("--timeout", type=int, default=25, help="Timeout HTTP por solicitud")
    parser.add_argument("--domain-delay", type=float, default=1.25, help="Pausa mínima entre solicitudes al mismo dominio")
    parser.add_argument("--max-bytes", type=int, default=1_500_000, help="Máximo leído por URL")
    parser.add_argument("--no-robots", action="store_true", help="No consultar robots.txt (solo diagnóstico excepcional)")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(
            f"No existe {args.input}. Importa primero el Excel con tools/import_source_inventory.py."
        )
    fetcher = SourceFetcher(
        timeout=args.timeout, domain_delay=args.domain_delay,
        max_bytes=args.max_bytes, check_robots=not args.no_robots,
    )
    pipeline = SourceValidationPipeline(
        fetcher=fetcher,
        exporter=SourceValidationExporter(args.output),
        workers=args.workers,
    )
    _, summary = pipeline.run(args.input)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
