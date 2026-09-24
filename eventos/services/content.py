"""OCR de afiches y redacción editorial para eventos nuevos."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from ..models import Event
from ..text import clean, normalized
from .nvidia import NvidiaExtractor, TRUE_VALUES, post_nvidia


def _json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise original_error
    if not isinstance(value, dict):
        raise ValueError("NVIDIA no devolvió un objeto JSON")
    return value


def _choice_content(response: requests.Response) -> str:
    response.raise_for_status()
    return clean(response.json()["choices"][0]["message"]["content"])


def _mark_permanent_failure(exc: requests.HTTPError) -> None:
    status = exc.response.status_code if exc.response is not None else 0
    if status in {401, 403, 410}:
        NvidiaExtractor.unavailable_reason = clean(exc)[:300]


class NvidiaContentEnricher:
    """Procesa únicamente el subconjunto que el pipeline identifica como nuevo."""

    def __init__(self, queue=None):
        self.queue = queue

    def enrich(self, events: list[Event]) -> dict[str, int]:
        stats = {"ocr": 0, "rewritten": 0}
        api_key = os.getenv("NVIDIA_API_KEY")
        enabled = normalized(os.getenv("NVIDIA_CONTENT_ENRICHMENT", "true")) in TRUE_VALUES
        if not events:
            return stats
        for event in events:
            event.source_description = clean(event.source_description) or clean(event.description)
        if not api_key or not enabled or NvidiaExtractor.unavailable_reason:
            if self.queue:
                for event in events:
                    if event.image_url:
                        self.queue.ready("ocr", event)
                    self.queue.ready("rewrite", event)
            return stats
        stats["ocr"] = self._ocr(events, api_key)
        stats["rewritten"] = self._rewrite(events, api_key)
        return stats

    def _ocr(self, events: list[Event], api_key: str) -> int:
        enabled = normalized(os.getenv("NVIDIA_OCR_ENABLED", "true")) in TRUE_VALUES
        model = clean(
            os.getenv("NVIDIA_OCR_MODEL", "meta/llama-3.2-11b-vision-instruct")
        )
        if not enabled or not model:
            return 0
        max_images = max(0, int(os.getenv("NVIDIA_OCR_MAX_IMAGES", "100")))
        timeout = max(1.0, float(os.getenv("NVIDIA_OCR_TIMEOUT_SECONDS", "45")))
        retries = max(0, int(os.getenv("NVIDIA_OCR_MAX_RETRIES", "1")))
        max_wait = max(1.0, float(os.getenv("NVIDIA_OCR_RETRY_MAX_WAIT_SECONDS", "15")))
        max_elapsed = max(
            1.0,
            float(os.getenv("NVIDIA_OCR_RETRY_MAX_ELAPSED_SECONDS", "100")),
        )
        changed = 0
        candidates = []
        for event in events:
            if not clean(event.image_url):
                continue
            if self.queue and not self.queue.ready("ocr", event):
                continue
            if not clean(event.ocr_text):
                candidates.append(event)
            elif self.queue:
                self.queue.finish("ocr", self.queue.key("ocr", event), {"ocr_text": event.ocr_text})
        if self.queue:
            candidates.sort(key=lambda event: self.queue.attempts("ocr", event))
        if max_images:
            candidates = candidates[:max_images]
        for event in candidates:
            if self.queue and not self.queue.ready("ocr", event):
                continue
            checkpoint = self.queue.start("ocr", event) if self.queue else None
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    'Lee el afiche de este evento chileno. Devuelve SOLO JSON '
                                    '{"ocr_text":"..."}. Transcribe únicamente texto visible; '
                                    "no inventes ni describas la imagen."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": event.image_url}},
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": min(4096, max(512, int(os.getenv("NVIDIA_MAX_TOKENS", "4096")))),
            }
            try:
                result = _json_object(
                    _choice_content(
                        post_nvidia(
                            payload,
                            api_key,
                            retries=retries,
                            timeout=timeout,
                            max_wait=max_wait,
                            max_elapsed=max_elapsed,
                            operation="OCR NVIDIA",
                        )
                    )
                )
                ocr_text = clean(result.get("ocr_text"))
                if not isinstance(result.get("ocr_text"), str):
                    raise ValueError("Respuesta OCR sin campo ocr_text válido")
                if ocr_text:
                    event.ocr_text = ocr_text
                    changed += 1
                if self.queue:
                    self.queue.finish("ocr", checkpoint, {"ocr_text": ocr_text})
            except requests.HTTPError as exc:
                if self.queue:
                    self.queue.finish("ocr", checkpoint, error=str(exc))
                _mark_permanent_failure(exc)
                logging.warning("OCR NVIDIA falló para %s: %s", event.source_url, exc)
            except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
                if self.queue:
                    self.queue.finish("ocr", checkpoint, error=str(exc))
                logging.warning("OCR NVIDIA no produjo texto utilizable para %s: %s", event.source_url, exc)
            if NvidiaExtractor.unavailable_reason:
                break
        return changed

    def _rewrite(self, events: list[Event], api_key: str) -> int:
        enabled = normalized(os.getenv("NVIDIA_REWRITE_ENABLED", "true")) in TRUE_VALUES
        if not enabled or NvidiaExtractor.unavailable_reason:
            return 0
        batch_size = min(25, max(1, int(os.getenv("NVIDIA_REWRITE_BATCH_SIZE", "10"))))
        description_chars = max(500, int(os.getenv("NVIDIA_REWRITE_INPUT_CHARS", "3000")))
        timeout = max(1.0, float(os.getenv("NVIDIA_REWRITE_TIMEOUT_SECONDS", "90")))
        retries = max(0, int(os.getenv("NVIDIA_REWRITE_MAX_RETRIES", "3")))
        max_wait = max(1.0, float(os.getenv("NVIDIA_REWRITE_RETRY_MAX_WAIT_SECONDS", "60")))
        max_elapsed = max(
            1.0,
            float(os.getenv("NVIDIA_REWRITE_RETRY_MAX_ELAPSED_SECONDS", "360")),
        )
        candidates = []
        for event in events:
            if self.queue and not self.queue.ready("rewrite", event):
                continue
            if event.is_rewritten:
                if self.queue:
                    self.queue.finish("rewrite", self.queue.key("rewrite", event), {"description": event.description, "is_rewritten": True})
            elif clean(event.source_description or event.description) or clean(event.ocr_text):
                candidates.append(event)
        changed = 0
        total_batches = (len(candidates) + batch_size - 1) // batch_size
        for batch_number, start in enumerate(
            range(0, len(candidates), batch_size),
            start=1,
        ):
            batch = candidates[start : start + batch_size]
            checkpoints = [self.queue.start("rewrite", event) for event in batch] if self.queue else []
            completed = set()
            changed_before = changed
            records = [
                {
                    "index": index,
                    "title": clean(event.title),
                    "start_date": clean(event.start_date),
                    "end_date": clean(event.end_date),
                    "venue": clean(event.venue),
                    "address": clean(event.address),
                    "commune": clean(event.commune),
                    "region": clean(event.region),
                    "organizer": clean(event.organizer),
                    "categories": event.categories,
                    "source_description": clean(event.source_description or event.description)[:description_chars],
                    "poster_ocr": clean(event.ocr_text)[:description_chars],
                }
                for index, event in enumerate(batch)
            ]
            prompt = (
                'Redacta una descripción editorial original y concisa para cada evento. Devuelve SOLO '
                '{"events":[{"index":0,"description":"..."}]}. Conserva todos los hechos, nombres, '
                "fechas, precios y condiciones; no inventes información ni agregues opiniones. No copies "
                "frases extensas de la fuente o del afiche. Si la evidencia es insuficiente, resume solo "
                "los datos disponibles. Registros:\n" + json.dumps(records, ensure_ascii=False)
            )
            payload = {
                "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
                "messages": [
                    {
                        "role": "system",
                        "content": "Eres un editor factual de una agenda cultural chilena.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": min(4096, max(512, int(os.getenv("NVIDIA_MAX_TOKENS", "4096")))),
                "chat_template_kwargs": {"enable_thinking": False},
            }
            try:
                result = _json_object(
                    _choice_content(
                        post_nvidia(
                            payload,
                            api_key,
                            retries=retries,
                            timeout=timeout,
                            max_wait=max_wait,
                            max_elapsed=max_elapsed,
                            operation="Redacción NVIDIA",
                        )
                    )
                )
                rewritten = result.get("events")
                if not isinstance(rewritten, list):
                    raise ValueError('La redacción de NVIDIA no contiene una lista en "events"')
                for row in rewritten:
                    if not isinstance(row, dict) or not isinstance(row.get("index"), int):
                        continue
                    index = row["index"]
                    description = clean(row.get("description"))
                    if 0 <= index < len(batch) and description:
                        batch[index].description = description
                        batch[index].is_rewritten = True
                        changed += 1
                        completed.add(index)
                        if self.queue:
                            self.queue.finish("rewrite", checkpoints[index], {"description": description, "is_rewritten": True})
            except requests.HTTPError as exc:
                _mark_permanent_failure(exc)
                logging.warning("Redacción NVIDIA falló para un lote: %s", exc)
            except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
                logging.warning("Redacción NVIDIA no produjo contenido utilizable: %s", exc)
            finally:
                if self.queue:
                    for index, checkpoint in enumerate(checkpoints):
                        if index not in completed:
                            self.queue.finish("rewrite", checkpoint, error="Lote sin respuesta válida para este evento")
            if NvidiaExtractor.unavailable_reason:
                break
            if batch_number == 1 or batch_number % 5 == 0 or batch_number == total_batches:
                logging.info(
                    "Redacción NVIDIA: lote %s/%s, %s eventos redactados en el lote, %s acumulados.",
                    batch_number,
                    total_batches,
                    changed - changed_before,
                    changed,
                )
        return changed
