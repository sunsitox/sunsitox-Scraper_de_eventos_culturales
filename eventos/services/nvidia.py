"""Respaldo de extracción semántica mediante NVIDIA NIM."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..config import SourceConfig
from ..models import Event
from ..text import clean, clean_list, normal_date, normalized, now_iso


TRUE_VALUES = {"1", "true", "yes", "si", "sí", "on"}


class NvidiaRateLimiter:
    """Limitador compartido de inicio-a-inicio para respetar el máximo de RPM."""

    def __init__(
        self,
        rpm_limit: int = 40,
        min_interval: float = 0.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.rpm_limit = max(1, rpm_limit)
        self.min_interval = max(min_interval, 60.0 / self.rpm_limit + 0.05)
        self.clock = clock
        self.sleeper = sleeper
        self._last_request: float | None = None
        self._not_before = 0.0
        self._lock = threading.Lock()
        self.requests = 0
        self.waited_seconds = 0.0
        self.consecutive_timeouts = 0
        self.state = None

    def acquire(self) -> None:
        with self._lock:
            now = self.clock()
            deferred_wait = max(0.0, self._not_before - now)
            wait = deferred_wait
            if self._last_request is not None:
                wait = max(wait, self.min_interval - (now - self._last_request))
            if wait:
                self.sleeper(wait)
                self.waited_seconds += wait
                now = self.clock()
            self._last_request = now
            self.requests += 1

    def defer(self, seconds: float) -> None:
        """Detiene globalmente nuevos inicios cuando el proveedor pide esperar."""
        with self._lock:
            self._not_before = max(self._not_before, self.clock() + max(0.0, seconds))
            if self.state:
                self.state.put("pipeline", "nvidia_not_before", time.time() + max(0.0, self._not_before - self.clock()))

    def remaining_wait(self):
        return max(0.0, self._not_before - self.clock())


_RATE_LIMITER: NvidiaRateLimiter | None = None
_RATE_LIMITER_CONFIG: tuple[int, float] | None = None


def nvidia_rate_limiter() -> NvidiaRateLimiter:
    global _RATE_LIMITER, _RATE_LIMITER_CONFIG
    rpm_limit = min(40, max(1, int(os.getenv("NVIDIA_RPM_LIMIT", "40"))))
    min_interval = max(0.0, float(os.getenv("NVIDIA_MIN_INTERVAL_SECONDS", "0")))
    config = (rpm_limit, min_interval)
    if _RATE_LIMITER is None or _RATE_LIMITER_CONFIG != config:
        _RATE_LIMITER = NvidiaRateLimiter(rpm_limit, min_interval)
        _RATE_LIMITER_CONFIG = config
    return _RATE_LIMITER


def configure_nvidia_state(store):
    limiter = nvidia_rate_limiter()
    limiter.state = store
    limiter.defer(max(0.0, store.get("pipeline", "nvidia_not_before", 0) - time.time()))


def retry_after_seconds(value):
    """Retry-After admite segundos o una fecha HTTP; nunca se recorta al max_wait."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            moment = parsedate_to_datetime(value)
            return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def nvidia_request_stats() -> dict[str, int | float]:
    limiter = nvidia_rate_limiter()
    return {
        "requests": limiter.requests,
        "waited_seconds": round(limiter.waited_seconds, 2),
        "rpm_limit": limiter.rpm_limit,
        "min_interval_seconds": round(limiter.min_interval, 2),
    }


def post_nvidia(
    payload: dict[str, Any],
    api_key: str,
    *,
    retries: int | None = None,
    timeout: float | None = None,
    max_wait: float | None = None,
    max_elapsed: float | None = None,
    operation: str = "NVIDIA NIM",
) -> requests.Response:
    # El limitador sincroniza el inicio de solicitudes de todos los trabajadores.
    # La respuesta puede procesarse en paralelo sin superar los 40 inicios por minuto.
    retries = max(
        0,
        int(os.getenv("NVIDIA_MAX_RETRIES", "8")) if retries is None else int(retries),
    )
    timeout = max(
        1.0,
        float(os.getenv("NVIDIA_TIMEOUT_SECONDS", "90")) if timeout is None else float(timeout),
    )
    max_wait = max(
        1.0,
        float(os.getenv("NVIDIA_RETRY_MAX_WAIT_SECONDS", "120"))
        if max_wait is None
        else float(max_wait),
    )
    max_elapsed = max(
        0.0,
        float(os.getenv("NVIDIA_RETRY_MAX_ELAPSED_SECONDS", "900"))
        if max_elapsed is None
        else float(max_elapsed),
    )
    response: requests.Response | None = None
    last_error: requests.RequestException | None = None
    started_at = time.monotonic()
    for attempt in range(retries + 1):
        elapsed = time.monotonic() - started_at
        if attempt and max_elapsed and elapsed >= max_elapsed:
            logging.warning(
                "%s agotó el tiempo total de reintentos configurado (%.0f s).",
                operation,
                max_elapsed,
            )
            break
        limiter = nvidia_rate_limiter()
        if isinstance(limiter, NvidiaRateLimiter) and max_elapsed and limiter.remaining_wait() >= max_elapsed - elapsed:
            raise requests.Timeout(f"{operation}: pausa del proveedor pendiente; se reanudará en otra ejecución")
        limiter.acquire()
        elapsed = time.monotonic() - started_at
        if max_elapsed and elapsed >= max_elapsed:
            raise requests.Timeout(f"{operation}: presupuesto de espera agotado")
        request_timeout = timeout
        if max_elapsed:
            request_timeout = max(1.0, min(timeout, max_elapsed - elapsed))
        try:
            response = requests.post(
                NvidiaExtractor.endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=request_timeout,
            )
            last_error = None
            limiter.consecutive_timeouts = 0
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            response = None
            if isinstance(limiter, NvidiaRateLimiter):
                limiter.consecutive_timeouts += 1

        retriable_status = response is not None and response.status_code in {
            408, 425, 429, 500, 502, 503, 504,
        }
        if response is not None and not retriable_status:
            return response
        retry_after = clean(response.headers.get("Retry-After")) if response is not None else ""
        delay = retry_after_seconds(retry_after) if retry_after else None
        if delay is None:
            delay = 0.0
            if response is not None and response.status_code == 429:
                delay = 60.0  # El proveedor limitó la cuota pero no publicó Retry-After.
            elif isinstance(limiter, NvidiaRateLimiter) and limiter.consecutive_timeouts >= max(2, int(os.getenv("NVIDIA_TIMEOUT_THRESHOLD", "3"))):
                delay = max(0.0, float(os.getenv("NVIDIA_TIMEOUT_COOLDOWN_SECONDS", "60")))
                limiter.consecutive_timeouts = 0
        if delay:
            limiter.defer(delay)
        if attempt >= retries:
            break
        if max_elapsed and time.monotonic() - started_at + delay > max_elapsed:
            logging.warning(
                "%s agotó el tiempo total de reintentos configurado (%.0f s).",
                operation,
                max_elapsed,
            )
            break
        logging.warning(
            "%s %s; reintento %s/%s, pausa adicional %.1f s (se mantiene el límite RPM).",
            operation,
            f"respondió {response.status_code}" if response is not None else "no respondió a tiempo",
            attempt + 1,
            retries,
            delay,
        )
    if response is not None:
        return response
    if last_error is not None:
        raise last_error
    raise requests.RequestException(f"{operation} no produjo una respuesta")


def parse_nvidia_content(content: str) -> list[dict]:
    """Valida la envoltura JSON de NIM y conserva compatibilidad con respuestas antiguas."""
    normalized = content.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        normalized = "\n".join(lines).strip()

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        payload = None
        for index, character in enumerate(normalized):
            if character not in "[{":
                continue
            try:
                candidate, _ = decoder.raw_decode(normalized[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, list) or (
                isinstance(candidate, dict) and isinstance(candidate.get("events"), list)
            ):
                payload = candidate
                break
        if payload is None:
            raise original_error
    if isinstance(payload, dict):
        records = payload.get("events")
    elif isinstance(payload, list):
        records = payload
    else:
        records = None

    if not isinstance(records, list):
        raise ValueError('La respuesta de NVIDIA debe contener una lista en la clave "events".')
    return [record for record in records if isinstance(record, dict)]


class NvidiaExtractor:
    endpoint = "https://integrate.api.nvidia.com/v1/chat/completions"
    unavailable_reason = ""

    def extract(self, html: str, source: SourceConfig, page_url: str) -> list[Event]:
        if self.unavailable_reason:
            return []
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            return []
        input_chars = int(os.getenv("NVIDIA_INPUT_CHARS", "12000"))
        max_tokens = int(os.getenv("NVIDIA_MAX_TOKENS", "4096"))
        visible_text = clean(BeautifulSoup(html, "html.parser").get_text(" "))[:input_chars]
        prompt = (
            'Extrae eventos culturales. Devuelve SOLO un objeto JSON con la forma {"events": [...]}. '
            "Cada evento puede contener estos campos: "
            "title,start_date,end_date,venue,address,commune,city,region,country,postal_code,"
            "latitude,longitude,organizer,"
            "categories,audience,is_free,"
            "description,image_url,price,currency,official_url. Usa cadena vacía si no aparece; "
            "categories debe ser una lista JSON de cadenas; no inventes datos. "
            f"Contexto conocido de la fuente: comuna={source.commune or 'desconocida'}, "
            f"ciudad={source.city or 'desconocida'}, región={source.region or 'desconocida'}. "
            "Texto:\n" + visible_text
        )
        payload = {
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
            "messages": [
                {"role": "system", "content": "Eres un extractor preciso de eventos chilenos."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        finish_reason = "desconocido"
        try:
            response = post_nvidia(payload, api_key)
            response.raise_for_status()
            choice = response.json()["choices"][0]
            finish_reason = choice.get("finish_reason") or "desconocido"
            content = choice["message"]["content"]
            records = parse_nvidia_content(content)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in {401, 403, 410}:
                detail = exc.response.text[:300] if exc.response is not None else str(exc)
                NvidiaExtractor.unavailable_reason = detail
                logging.warning(
                    "NVIDIA NIM no está disponible (%s); se desactiva el respaldo IA para esta ejecución.",
                    status,
                )
            else:
                logging.warning("NVIDIA NIM no pudo interpretar %s: %s", page_url, exc)
            return []
        except (json.JSONDecodeError, ValueError) as exc:
            logging.warning(
                "NVIDIA NIM devolvió JSON inválido para %s (fin=%s): %s",
                page_url,
                finish_reason,
                exc,
            )
            return []
        except (requests.RequestException, KeyError, IndexError, TypeError) as exc:
            logging.warning("NVIDIA NIM no pudo interpretar %s: %s", page_url, exc)
            return []
        events: list[Event] = []
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict) or not clean(record.get("title")):
                continue
            categories = clean_list(record.get("categories") or record.get("category"))
            values = {
                key: clean(record.get(key))
                for key in Event.__dataclass_fields__
                if key in record and key != "categories"
            }
            event = Event(categories=categories, **values)
            event.start_date = normal_date(event.start_date)
            event.end_date = normal_date(event.end_date)
            event.commune = event.commune or source.commune
            event.city = event.city or source.city or source.commune
            event.region = event.region or source.region
            event.organizer = event.organizer or source.organizer
            event.source_description = event.description
            event.categories = event.categories or clean_list(source.get("default_categories", []))
            event.source_name = source.name
            event.source_url = page_url
            event.official_url = event.official_url if source.official else ""
            event.extracted_at = now_iso()
            event.extraction_method = "nvidia-nim"
            events.append(event)
        return events


def parse_nvidia_locations(content: str) -> list[dict[str, Any]]:
    normalized_content = content.strip()
    if normalized_content.startswith("```"):
        lines = normalized_content.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        normalized_content = "\n".join(lines).strip()
    try:
        payload = json.loads(normalized_content)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        payload = None
        for index, character in enumerate(normalized_content):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(normalized_content[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("locations"), list):
                payload = candidate
                break
        if payload is None:
            raise original_error
    records = payload.get("locations") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError('La respuesta de ubicación debe contener una lista en "locations".')
    return [record for record in records if isinstance(record, dict)]


class NvidiaLocationEnricher:
    """Completa por lotes solo vacíos de ubicación respaldados por texto del evento."""

    def __init__(self, queue=None):
        self.queue = queue

    def enrich(self, events: list[Event]) -> int:
        if self.queue:
            events = [event for event in events if self.queue.ready("location", event)]
        if NvidiaExtractor.unavailable_reason:
            return 0
        api_key = os.getenv("NVIDIA_API_KEY")
        enabled = normalized(os.getenv("NVIDIA_LOCATION_ENRICHMENT", "true")) in TRUE_VALUES
        if not api_key or not enabled or not events:
            return 0

        batch_size = min(50, max(1, int(os.getenv("NVIDIA_LOCATION_BATCH_SIZE", "20"))))
        max_batches = max(0, int(os.getenv("NVIDIA_LOCATION_MAX_BATCHES", "5")))
        description_chars = max(0, int(os.getenv("NVIDIA_LOCATION_DESCRIPTION_CHARS", "500")))
        timeout = max(1.0, float(os.getenv("NVIDIA_LOCATION_TIMEOUT_SECONDS", "60")))
        retries = max(0, int(os.getenv("NVIDIA_LOCATION_MAX_RETRIES", "3")))
        max_wait = max(
            1.0,
            float(os.getenv("NVIDIA_LOCATION_RETRY_MAX_WAIT_SECONDS", "30")),
        )
        max_elapsed = max(
            1.0,
            float(os.getenv("NVIDIA_LOCATION_RETRY_MAX_ELAPSED_SECONDS", "240")),
        )
        changed = 0
        batches = 0
        for start in range(0, len(events), batch_size):
            if batches >= max_batches:
                logging.warning(
                    "NVIDIA ubicación: quedan %s eventos sin revisar por NVIDIA_LOCATION_MAX_BATCHES.",
                    len(events) - start,
                )
                break
            batch = events[start : start + batch_size]
            checkpoints = [self.queue.start("location", event) for event in batch] if self.queue else []
            compact = [
                {
                    "index": index,
                    "title": clean(event.title),
                    "venue": clean(event.venue),
                    "address": clean(event.address),
                    "commune": clean(event.commune),
                    "city": clean(event.city),
                    "region": clean(event.region),
                    "postal_code": clean(event.postal_code),
                    "latitude": clean(event.latitude),
                    "longitude": clean(event.longitude),
                    "description": clean(event.source_description or event.description)[:description_chars],
                    "ocr_text": clean(event.ocr_text)[:description_chars],
                    "source": clean(event.source_name),
                }
                for index, event in enumerate(batch)
            ]
            prompt = (
                'Normaliza ubicaciones de eventos chilenos. Devuelve SOLO {"locations": [...]}. '
                "Cada elemento debe incluir index y únicamente campos respaldados por el texto: "
                "venue,address,commune,city,region,postal_code,latitude,longitude,online. "
                "No inventes direcciones, recintos ni coordenadas; solo interpreta datos explícitos. "
                "Si no hay evidencia, usa cadena vacía o false. Registros:\n"
                + json.dumps(compact, ensure_ascii=False)
            )
            payload = {
                "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
                "messages": [
                    {
                        "role": "system",
                        "content": "Eres un normalizador conservador de ubicaciones de Chile.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": min(4096, max(512, int(os.getenv("NVIDIA_MAX_TOKENS", "4096")))),
                "chat_template_kwargs": {"enable_thinking": False},
            }
            batches += 1
            try:
                response = post_nvidia(
                    payload,
                    api_key,
                    retries=retries,
                    timeout=timeout,
                    max_wait=max_wait,
                    max_elapsed=max_elapsed,
                    operation="Ubicación NVIDIA",
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                records = parse_nvidia_locations(content)
            except requests.HTTPError as exc:
                if self.queue:
                    for checkpoint in checkpoints:
                        self.queue.finish("location", checkpoint, error=str(exc))
                status = exc.response.status_code if exc.response is not None else 0
                if status in {401, 403, 410}:
                    NvidiaExtractor.unavailable_reason = clean(exc)[:300]
                logging.warning("NVIDIA no pudo normalizar un lote de ubicaciones: %s", exc)
                continue
            except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
                if self.queue:
                    for checkpoint in checkpoints:
                        self.queue.finish("location", checkpoint, error=str(exc))
                logging.warning("NVIDIA devolvió una ubicación no utilizable: %s", exc)
                continue

            completed = set()
            for record in records:
                index = record.get("index")
                if not isinstance(index, int) or not 0 <= index < len(batch):
                    continue
                event = batch[index]
                event_changed = False
                for field in (
                    "venue", "address", "commune", "city", "region", "postal_code",
                    "latitude", "longitude",
                ):
                    value = clean(record.get(field))
                    if value and not clean(getattr(event, field)):
                        setattr(event, field, value)
                        event_changed = True
                if record.get("online") is True and not clean(event.venue) and not clean(event.address):
                    event.venue = "Online"
                    event_changed = True
                if event_changed:
                    event.location_source = "nvidia-nim"
                    changed += 1
                completed.add(index)
                if self.queue:
                    self.queue.finish("location", checkpoints[index], {field: getattr(event, field) for field in ("venue", "address", "commune", "city", "region", "postal_code", "latitude", "longitude", "location_source")})
            if self.queue:
                for index, checkpoint in enumerate(checkpoints):
                    if index not in completed:
                        self.queue.finish("location", checkpoint, error="Sin resultado para este evento")
        if batches:
            logging.info(
                "NVIDIA ubicación: %s candidatos, %s lotes, %s enriquecidos.",
                len(events),
                batches,
                changed,
            )
        return changed
