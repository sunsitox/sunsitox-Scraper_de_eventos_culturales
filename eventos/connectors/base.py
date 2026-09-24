"""Contrato Strategy para cualquier fuente de eventos."""

from abc import ABC, abstractmethod

from ..config import SourceConfig
from ..http import HttpClient
from ..models import Event


class Connector(ABC):
    def __init__(self, http: HttpClient):
        self.http = http
        self.state = getattr(http, "pipeline_state", None)

    @abstractmethod
    def collect(self, source: SourceConfig) -> list[Event]:
        """Devuelve eventos normalizados desde una fuente."""
