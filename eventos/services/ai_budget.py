"""Presupuestos por etapa: la IA pendiente no debe impedir la publicación."""

import logging
import os
import time

import requests


class StageBudget:
    def __init__(self, stage: str, label: str, default_seconds: int):
        self.label = label
        self.deadline = time.monotonic() + max(
            1.0, float(os.getenv(f"NVIDIA_{stage}_STAGE_BUDGET_SECONDS", str(default_seconds)))
        )
        self.failure_limit = max(
            1, int(os.getenv(f"NVIDIA_{stage}_FAILURE_THRESHOLD", "3"))
        )
        self.failures = 0

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def request_limit(self, per_operation: float) -> float:
        remaining = self.remaining()
        if remaining < 1.0:
            raise requests.Timeout(f"{self.label}: presupuesto de etapa agotado")
        return min(per_operation, remaining)

    def can_start(self, pending: int) -> bool:
        reason = ""
        if self.remaining() < 1.0:
            reason = "presupuesto de tiempo agotado"
        elif self.failures >= self.failure_limit:
            reason = f"{self.failures} operaciones consecutivas sin resultado válido"
        if reason:
            logging.warning(
                "%s se suspende en esta ejecución (%s); %s pendientes. "
                "Se conservan los checkpoints y continúa el pipeline hacia la sincronización.",
                self.label, reason, pending,
            )
            return False
        return True

    def record(self, success: bool) -> None:
        self.failures = 0 if success else self.failures + 1
