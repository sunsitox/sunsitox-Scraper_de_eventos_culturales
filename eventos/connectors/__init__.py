"""Carga de Strategies y acceso a su Factory."""

from .registry import ConnectorFactory

# Las importaciones registran cada Strategy mediante decoradores.
from . import (  # noqa: F401,E402
    chile_cultura,
    eventon,
    fisa,
    generic,
    html_cards,
    json_api,
    maipu,
    teatro_biobio,
    ticketplus,
    tribe,
    wordpress,
)

__all__ = ["ConnectorFactory"]
