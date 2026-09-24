"""Registry + Factory: resuelve el conector declarado en cada JSON."""

from __future__ import annotations

from collections.abc import Callable

from ..config import SourceConfig
from ..http import HttpClient
from .base import Connector

_CONNECTORS: dict[str, type[Connector]] = {}


def register_connector(*names: str) -> Callable[[type[Connector]], type[Connector]]:
    def decorator(connector_class: type[Connector]) -> type[Connector]:
        for name in names:
            if name in _CONNECTORS:
                raise ValueError(f"Conector registrado dos veces: {name}")
            _CONNECTORS[name] = connector_class
        return connector_class

    return decorator


class ConnectorFactory:
    def __init__(self, http: HttpClient):
        self.http = http

    def create(self, source: SourceConfig) -> Connector:
        try:
            connector_class = _CONNECTORS[source.connector]
        except KeyError as exc:
            available = ", ".join(sorted(_CONNECTORS))
            raise ValueError(
                f"Conector desconocido '{source.connector}' en {source.name}. "
                f"Disponibles: {available}"
            ) from exc
        return connector_class(self.http)

    @staticmethod
    def available() -> tuple[str, ...]:
        return tuple(sorted(_CONNECTORS))
