"""Convierte el Excel de investigación en el inventario canónico del validador."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eventos.paths import ROOT
from eventos.source_validation.loader import load_candidates, write_canonical_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa fuentes desde un Excel, CSV o JSON.")
    parser.add_argument("input", type=Path, help="Archivo producido durante la investigación")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "source_candidates.json")
    args = parser.parse_args()
    candidates = load_candidates(args.input)
    write_canonical_inventory(candidates, args.output)
    print(f"Importadas {len(candidates)} fuentes en {args.output}")


if __name__ == "__main__":
    main()
