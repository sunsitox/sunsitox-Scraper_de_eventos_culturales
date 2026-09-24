"""Objetos de configuración y repositorio de fuentes JSON."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import SETTINGS_FILE, SOURCES_DIR


@dataclass(frozen=True)
class SourceConfig(Mapping[str, Any]):
    name: str
    url: str
    connector: str = "generic"
    region: str = ""
    commune: str = ""
    city: str = ""
    organizer: str = ""
    official: bool = True
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], filename: str = "") -> "SourceConfig":
        required = ("name", "url")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError(f"Fuente incompleta {filename}: falta {', '.join(missing)}")
        known = {
            "name", "url", "connector", "region", "commune", "city",
            "organizer", "official", "enabled",
        }
        options = {key: value for key, value in payload.items() if key not in known}
        if filename:
            options["_config_file"] = filename
        return cls(
            name=str(payload["name"]),
            url=str(payload["url"]),
            connector=str(payload.get("connector", "generic")),
            region=str(payload.get("region", "")),
            commune=str(payload.get("commune", "")),
            city=str(payload.get("city", "")),
            organizer=str(payload.get("organizer", "")),
            official=bool(payload.get("official", True)),
            enabled=bool(payload.get("enabled", True)),
            options=options,
        )

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.options[key]

    def __iter__(self) -> Iterator[str]:
        yield from (
            "name", "url", "connector", "region", "commune", "city",
            "organizer", "official", "enabled",
        )
        yield from self.options

    def __len__(self) -> int:
        return 9 + len(self.options)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


@dataclass(frozen=True)
class Settings:
    target_regions: tuple[str, ...] = ()
    include_unknown_region: bool = False
    cultural_filter_enabled: bool = True
    cultural_review_action: str = "keep"

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            target_regions=tuple(payload.get("target_regions", [])),
            include_unknown_region=bool(payload.get("include_unknown_region", False)),
            cultural_filter_enabled=bool(payload.get("cultural_filter_enabled", True)),
            cultural_review_action=str(payload.get("cultural_review_action", "keep")),
        )


class SourceRepository:
    """Repository pattern: aísla del pipeline el almacenamiento de fuentes."""

    def __init__(self, directory: Path = SOURCES_DIR):
        self.directory = directory

    def load(self) -> list[SourceConfig]:
        sources: list[SourceConfig] = []
        for path in sorted(self.directory.glob("*.json")):
            if path.name.startswith("_"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            sources.append(SourceConfig.from_dict(payload, path.name))
        if not sources:
            raise ValueError(f"No hay fuentes configuradas en {self.directory}")
        names: set[str] = set()
        for source in sources:
            if source.name in names:
                raise ValueError(f"Nombre de fuente duplicado: {source.name}")
            names.add(source.name)
        return sources
