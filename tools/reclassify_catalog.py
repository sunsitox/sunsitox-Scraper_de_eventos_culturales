"""Reclasifica el catálogo existente sin scraping ni llamadas a NVIDIA.

Por defecto SOLO consulta y escribe una propuesta local. --apply es explícito,
requiere la migración 20261027000100 y usa una RPC restringida a service_role.
No borra eventos, no altera descripciones/fechas ni historial de usuarios.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import load_dotenv

from eventos.classification import CLASSIFICATION_VERSION
from eventos.cultural_filter import filter_cultural_events
from eventos.models import Event
from eventos.paths import ROOT, OUTPUT_DIR
from eventos.services.supabase import SupabaseConfig, SupabaseExporter, SupabaseRestClient, stable_uuid


def read_all(client, table, columns="*", order="id"):
    rows = []
    for offset in range(0, 100_000, 1000):
        page = client.select(table, {"select": columns, "order": order, "limit": "1000", "offset": str(offset)})
        rows.extend(page)
        if len(page) < 1000:
            return rows
    raise ValueError("Consulta incompleta: se alcanzó el límite de auditoría; no se aplicará nada")


def build_plan(rows, provenance, sources, categories, links, blocked):
    names = {row["id"]: row["name"] for row in categories}
    source_map = {row["id"]: row for row in sources}
    original = {}
    for link in links:
        if link["category_id"] in names:
            original.setdefault(link["event_id"], []).append(names[link["category_id"]])
    provenance = {row["event_id"]: row for row in provenance}
    planned, skipped = [], Counter()
    for row in rows:
        source = source_map.get(row.get("source_id"), {})
        if (row.get("extraction_method") == "manual" or row.get("is_suppressed")
                or row.get("status") not in {"published", "draft"}
                or row.get("organizer_id") in blocked or not source.get("is_active")
                or row["id"] not in provenance):
            skipped["manual_oculto_bloqueado_inactivo_o_sin_procedencia"] += 1
            continue
        old = provenance[row["id"]]
        metadata = old.get("classification") or {}
        event = Event(
            title=row["title"], start_date=row.get("start_at") or "", end_date=row.get("end_at") or "",
            venue=row.get("venue") or "", audience=row.get("audience") or "",
            description=row.get("description") or "", source_description=old.get("source_description") or "",
            ocr_text=old.get("ocr_text") or "", source_name=source.get("name", ""),
            source_url=row.get("source_url") or "", categories=original.get(row["id"], []),
            classification=metadata if isinstance(metadata, dict) else {},
        )
        filter_cultural_events([event], review_action="quarantine")
        planned.append({
            "id": row["id"], "title": row["title"], "source": event.source_name,
            "source_url": event.source_url, "previous_status": row["status"],
            "previous_categories": original.get(row["id"], []),
            "previous_classification": old.get("classification") or {},
            "classification": event.classification,
            "category_ids": {name: stable_uuid("category", name) for name in event.categories},
        })
    return planned, dict(skipped)


def apply_plan(client, plan):
    """Lotes atómicos/repetibles. Un fallo conserva la propuesta local para reintentar."""
    updated = skipped = 0
    for start in range(0, len(plan), 100):
        rows = [{key: row[key] for key in ("id", "classification", "category_ids")} for row in plan[start:start+100]]
        result = client.rpc("apply_catalog_classifications", {"p_rows": rows, "p_version": CLASSIFICATION_VERSION})
        if len(result) != 1:
            raise ValueError("Respuesta inesperada de reclasificación; consulta el estado antes de reintentar")
        updated += int(result[0]["updated"])
        skipped += int(result[0]["skipped"])
    return {"updated": updated, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="aplica categorías/estados; sin esta opción solo simula")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR/"reclassification_preview.json")
    args = parser.parse_args()
    load_dotenv(ROOT/".env")
    client = SupabaseRestClient(SupabaseConfig.from_env())
    if args.apply:
        SupabaseExporter(client).require_classification_schema()
    rows = read_all(client, "events", "id,title,start_at,end_at,venue,audience,description,source_id,source_url,organizer_id,extraction_method,is_suppressed,status")
    blocked = {row["id"] for row in client.select("organizers", {"select":"id", "is_blocked":"eq.true"})}
    plan, skipped = build_plan(rows, read_all(client,"event_provenance",order="event_id"),
        read_all(client,"sources","id,name,is_active"), read_all(client,"categories","id,name"),
        read_all(client,"event_categories","event_id,category_id", "event_id,category_id"), blocked)
    summary = {
        "mode": "apply" if args.apply else "preview_only", "version": CLASSIFICATION_VERSION,
        "queried_at": datetime.now(timezone.utc).isoformat(), "total_events":len(rows),
        "planned":len(plan), "skipped":skipped,
        "decisions":dict(Counter(row["classification"]["decision"] for row in plan)),
        "content_kinds":dict(Counter(row["classification"]["content_kind"] for row in plan)),
        "published_categories":dict(Counter(name for row in plan if row["classification"]["decision"] == "accepted" and row["previous_status"] == "published" for name in row["classification"]["categories"])),
    }
    # Se conserva antes de cualquier escritura remota y no se reutiliza a ciegas en otra ejecución.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary":summary,"plan":plan}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Propuesta local: {args.output}")
    if args.apply:
        print(json.dumps(apply_plan(client, plan)))
    else:
        print("SIMULACIÓN: no se modificó Supabase ni se llamó a NVIDIA.")


if __name__ == "__main__":
    main()
