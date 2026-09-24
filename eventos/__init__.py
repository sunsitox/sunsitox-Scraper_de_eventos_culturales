"""Componentes del agregador de eventos culturales."""

from .models import Event
from .pipeline import EventPipeline

__all__ = ["Event", "EventPipeline"]
