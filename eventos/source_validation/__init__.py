"""Pipeline independiente para auditar fuentes potenciales de eventos."""

from .models import SourceCandidate, SourceValidationResult
from .pipeline import SourceValidationPipeline

__all__ = ["SourceCandidate", "SourceValidationResult", "SourceValidationPipeline"]
