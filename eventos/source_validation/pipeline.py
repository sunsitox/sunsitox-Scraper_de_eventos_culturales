"""Orquestación paralela de la auditoría, separada del scraping de eventos."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from pathlib import Path

from .exporter import SourceValidationExporter
from .fetcher import SourceFetcher
from .loader import load_candidates
from .models import SourceCandidate, SourceValidationResult
from .rules import SourceRuleEngine


class SourceValidationPipeline:
    def __init__(
        self,
        fetcher: SourceFetcher | None = None,
        rules: SourceRuleEngine | None = None,
        exporter: SourceValidationExporter | None = None,
        workers: int = 6,
    ) -> None:
        self.fetcher = fetcher or SourceFetcher()
        self.rules = rules or SourceRuleEngine()
        self.exporter = exporter
        self.workers = max(1, min(12, workers))

    def _validate(self, candidate: SourceCandidate) -> SourceValidationResult:
        observation = self.fetcher.fetch(candidate)
        result = self.rules.evaluate(candidate, observation)
        log = logging.debug if result.verdict == "no_aplica" else logging.info
        log("[%s] %s — %s/100", result.verdict, candidate.name, result.score)
        return result

    def run(self, input_path: Path) -> tuple[list[SourceValidationResult], dict]:
        candidates = load_candidates(input_path)
        logging.info("Auditando %s entradas con %s trabajadores.", len(candidates), self.workers)
        started = time.perf_counter()
        indexed: dict[int, SourceValidationResult] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="auditoria") as executor:
            futures = {executor.submit(self._validate, candidate): index for index, candidate in enumerate(candidates)}
            for future in concurrent.futures.as_completed(futures):
                indexed[futures[future]] = future.result()
        results = [indexed[index] for index in range(len(candidates))]
        elapsed = time.perf_counter() - started
        summary = self.exporter.export(results, elapsed) if self.exporter else {
            "total": len(results), "elapsed_seconds": round(elapsed, 2)
        }
        return results, summary
