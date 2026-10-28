"""IA acotada para categorías ambiguas; sin OCR adicional ni datos inventados."""

import json
import logging
import os

import requests

from ..classification import CANONICAL_CATEGORIES, classify_event, evidence_text
from .ai_budget import StageBudget
from .content import _mark_permanent_failure
from .nvidia import NvidiaExtractor, TRUE_VALUES, post_nvidia
from .nvidia_response import chat_content, json_object
from ..text import normalized


class NvidiaCategoryClassifier:
    def __init__(self, queue=None):
        self.queue = queue

    def enrich(self, events, *, allow_requests=True):
        candidates = []
        for event in events:
            metadata = classify_event(event)
            if event.categories or metadata.get("ai_checked") or metadata["content_kind"] != "event" or not evidence_text(event).strip():
                continue
            if self.queue and not self.queue.ready("classify", event):
                classify_event(event)
                continue
            candidates.append(event)
        key = os.getenv("NVIDIA_API_KEY")
        if not allow_requests or not key or NvidiaExtractor.unavailable_reason or normalized(os.getenv("NVIDIA_CLASSIFICATION_ENABLED", "true")) not in TRUE_VALUES:
            return 0
        budget = StageBudget("CLASSIFICATION", "Clasificación NVIDIA", 300)
        batch_size = min(20, max(1, int(os.getenv("NVIDIA_CLASSIFICATION_BATCH_SIZE", "10"))))
        max_events = max(1, int(os.getenv("NVIDIA_CLASSIFICATION_MAX_EVENTS", "100")))
        changed = 0
        for start in range(0, min(len(candidates), max_events), batch_size):
            if not budget.can_start(len(candidates) - start):
                break
            batch = candidates[start:min(start + batch_size, max_events)]
            checkpoints = [self.queue.start("classify", e) for e in batch] if self.queue else []
            completed = set()
            success = False
            records = [{"index": i, "text": evidence_text(e)[:5000]} for i, e in enumerate(batch)]
            payload = {
                "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
                "stream": False, "temperature": 0, "max_tokens": 3000,
                "messages": [{"role": "user", "content": (
                    'Clasifica contenido cultural, no instituciones. Devuelve SOLO '
                    '{"events":[{"index":0,"evidence":[{"category":"Música","quote":"cita literal del texto"}]}]}. '
                    'Usa únicamente las categorías permitidas. Cada asignación requiere una cita literal '
                    'sustantiva que demuestre esa disciplina; nombres de artistas, recintos o fechas solos '
                    'no bastan. No inventes hechos. Si falta evidencia, evidence debe ser []. '
                    'No clasifiques deporte, servicios o fichas de establecimientos como cultura. '
                    + 'Categorías: ' + json.dumps(CANONICAL_CATEGORIES, ensure_ascii=False)
                    + '\nRegistros: ' + json.dumps(records, ensure_ascii=False)
                )}],
            }
            try:
                response = post_nvidia(payload, key, retries=1, timeout=45, max_wait=30,
                                       max_elapsed=budget.request_limit(100), operation="Clasificación NVIDIA")
                rows = json_object(chat_content(response), "events").get("events")
                if not isinstance(rows, list):
                    raise ValueError("La clasificación no contiene una lista events")
                for row in rows:
                    if not isinstance(row, dict) or type(row.get("index")) is not int:
                        continue
                    index = row["index"]
                    if index in completed or not 0 <= index < len(batch) or not isinstance(row.get("evidence"), list):
                        continue
                    event = batch[index]
                    text = normalized(records[index]["text"])
                    evidence = row["evidence"]
                    if any(not isinstance(item, dict) or item.get("category") not in CANONICAL_CATEGORIES
                           or not isinstance(item.get("quote"), str)
                           or sum(c.isalpha() for c in item["quote"]) < 8
                           or normalized(item["quote"]) not in text for item in evidence):
                        continue
                    event.classification["ai_evidence"] = evidence
                    event.classification["ai_checked"] = True
                    classify_event(event)
                    changed += bool(event.categories)
                    completed.add(index)
                    success = True
                    if self.queue:
                        self.queue.finish("classify", checkpoints[index], {"classification": event.classification})
            except requests.HTTPError as exc:
                _mark_permanent_failure(exc)
                logging.warning("Clasificación NVIDIA falló: %s", exc)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                logging.warning("Clasificación NVIDIA sin respuesta válida: %s", exc)
            finally:
                budget.record(success)
                if self.queue:
                    for index, checkpoint in enumerate(checkpoints):
                        if index not in completed:
                            self.queue.finish("classify", checkpoint, error="Clasificación pendiente sin evidencia válida")
            if NvidiaExtractor.unavailable_reason:
                break
        logging.info("Clasificación NVIDIA: %s eventos clasificados; el resto conserva revisión/checkpoints.", changed)
        return changed
