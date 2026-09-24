"""Exportadores JSON/CSV del conjunto consolidado y de cada región."""

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .models import Event
from .paths import OUTPUT_DIR
from .text import normalized, slug


def load_json_dataset(json_path: Path = OUTPUT_DIR / "events.json") -> list[Event]:
    """Reconstruye eventos desde el último JSON para reintentar una carga remota."""
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"No existe {json_path}. Ejecuta primero una extracción completa."
        ) from exc
    if not isinstance(payload, list):
        raise ValueError(f"El archivo {json_path} no contiene una lista de eventos.")
    fields = set(Event.__dataclass_fields__)
    events: list[Event] = []
    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"El evento {index} de {json_path} no es un objeto JSON.")
        values = {key: value for key, value in row.items() if key in fields}
        if not isinstance(values.get("categories", []), list):
            values["categories"] = []
        event = Event(**values)
        # Compatibilidad con JSON creados antes de exponer este indicador.
        if "is_rewritten" not in row:
            event.is_rewritten = bool(
                event.source_description
                and event.description
                and normalized(event.source_description) != normalized(event.description)
            )
        events.append(event)
    return events


def write_dataset(events: list[Event], json_path: Path, csv_path: Path) -> None:
    payload = [asdict(event) for event in events]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_payload = [
        {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
            for key, value in row.items()
        }
        for row in payload
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(Event.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(csv_payload)


class DatasetExporter:
    def __init__(self, output_dir: Path = OUTPUT_DIR):
        self.output_dir = output_dir

    def export(self, events: list[Event]) -> None:
        self.output_dir.mkdir(exist_ok=True)
        write_dataset(events, self.output_dir / "events.json", self.output_dir / "events.csv")
        region_dir = self.output_dir / "by_region"
        region_dir.mkdir(exist_ok=True)
        for region in sorted({event.region for event in events if event.region}):
            regional = [event for event in events if event.region == region]
            stem = slug(region)
            write_dataset(regional, region_dir / f"{stem}.json", region_dir / f"{stem}.csv")
