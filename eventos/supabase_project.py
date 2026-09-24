"""Resolución segura del project ref utilizado por Supabase CLI."""

from __future__ import annotations

import os
import re
import sys

from dotenv import load_dotenv

from .paths import ROOT

PROJECT_REF_PATTERN = re.compile(r"(?i)([a-z]{20})\.supabase\.co")
EXACT_PROJECT_REF_PATTERN = re.compile(r"^[a-z]{20}$")


def resolve_project_ref(value: str | None = None) -> str:
    """Acepta un ref, una URL o un enlace Markdown que contenga la URL."""
    load_dotenv(ROOT / ".env", override=True)
    candidate = (value or os.getenv("SUPABASE_URL") or "").strip()
    if EXACT_PROJECT_REF_PATTERN.fullmatch(candidate):
        return candidate.lower()

    match = PROJECT_REF_PATTERN.search(candidate)
    if match:
        return match.group(1).lower()

    raise ValueError(
        "No se pudo obtener el project ref. Configura SUPABASE_URL en .env "
        "o entrega un identificador de exactamente 20 letras minúsculas."
    )


def main() -> int:
    try:
        print(resolve_project_ref(os.getenv("SUPABASE_PROJECT_INPUT")))
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
