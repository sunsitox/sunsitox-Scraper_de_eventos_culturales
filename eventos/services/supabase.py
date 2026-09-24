"""Carga idempotente del catálogo normalizado mediante la Data REST API de Supabase."""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any
from urllib.parse import urlparse

import requests

from ..consolidation import fingerprint, original_event_url
from ..models import Event
from ..text import chile_now, clean, is_upcoming, normalized


SUPABASE_NAMESPACE = uuid.UUID("c7e63dc3-708d-4ab2-aeb3-6902ccb9a46d")
TRUE_VALUES = {
    "1",
    "true",
    "yes",
    "si",
    "sí",
    "on",
    "enable",
    "enabled",
    "habilitado",
    "habilitada",
}


class SupabaseError(RuntimeError):
    """Error de configuración o de la Data REST API de Supabase."""


def supabase_enabled() -> bool:
    return normalized(os.getenv("SUPABASE_ENABLED", "false")) in TRUE_VALUES


def stable_uuid(kind: str, value: str) -> str:
    return str(uuid.uuid5(SUPABASE_NAMESPACE, f"{kind}:{normalized(value)}"))


def optional_bool(value: object) -> bool | None:
    key = normalized(clean(value))
    if not key:
        return None
    if key in TRUE_VALUES or key in {"gratis", "gratuito", "gratuita"}:
        return True
    if key in {"0", "false", "no", "off"}:
        return False
    return None


def optional_timestamp(value: object) -> str | None:
    """Conserva ISO válido y localiza fechas de evento ingenuas en Chile."""
    text = clean(value)
    if not text:
        return None
    try:
        if len(text) == 10:
            moment = datetime.combine(date.fromisoformat(text), datetime.min.time())
        else:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if moment.tzinfo is None:
        try:
            moment = moment.replace(tzinfo=ZoneInfo("America/Santiago"))
        except ZoneInfoNotFoundError:
            return None
    return moment.isoformat()


def website_origin(value: str) -> str | None:
    parsed = urlparse(clean(value))
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"


def optional_float(value: object, lower: float, upper: float) -> float | None:
    try:
        number = float(clean(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if lower <= number <= upper else None


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    secret_key: str
    batch_size: int = 200
    timeout: int = 60
    max_retries: int = 3
    retry_max_wait_seconds: int = 30

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        url = clean(os.getenv("SUPABASE_URL")).rstrip("/")
        secret_key = clean(
            os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )
        if not url or not secret_key:
            raise SupabaseError(
                "Faltan SUPABASE_URL y SUPABASE_SECRET_KEY "
                "(o SUPABASE_SERVICE_ROLE_KEY para proyectos antiguos)."
            )
        if not url.startswith("https://"):
            raise SupabaseError("SUPABASE_URL debe comenzar con https://")
        try:
            batch_size = max(1, int(os.getenv("SUPABASE_BATCH_SIZE", "200")))
            timeout = max(1, int(os.getenv("SUPABASE_TIMEOUT_SECONDS", "60")))
            max_retries = max(0, int(os.getenv("SUPABASE_MAX_RETRIES", "3")))
            retry_max_wait_seconds = max(
                1, int(os.getenv("SUPABASE_RETRY_MAX_WAIT_SECONDS", "30"))
            )
        except ValueError as exc:
            raise SupabaseError("Los límites de Supabase deben ser números enteros.") from exc
        return cls(
            url=url,
            secret_key=secret_key,
            batch_size=batch_size,
            timeout=timeout,
            max_retries=max_retries,
            retry_max_wait_seconds=retry_max_wait_seconds,
        )


class SupabaseRestClient:
    def __init__(
        self,
        config: SupabaseConfig,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "apikey": config.secret_key,
                "Content-Type": "application/json",
                "User-Agent": "EventosCulturalesMVP/0.4 (server-side ingestion)",
            }
        )
        # Las claves secretas sb_secret_* no son JWT y deben viajar solo en apikey.
        if not config.secret_key.startswith("sb_secret_"):
            self.session.headers["Authorization"] = f"Bearer {config.secret_key}"

    def _endpoint(self, table: str) -> str:
        return f"{self.config.url}/rest/v1/{table}"

    def _validate(self, response: requests.Response, table: str, operation: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = clean(response.text)[:500]
            raise SupabaseError(
                f"Supabase rechazó {operation} en {table} "
                f"(HTTP {response.status_code}): {detail}"
            ) from exc

    @staticmethod
    def _retry_after_seconds(response: requests.Response | None) -> float | None:
        if response is None:
            return None
        raw_value = clean(response.headers.get("Retry-After"))
        try:
            return max(0.0, float(raw_value)) if raw_value else None
        except ValueError:
            return None

    def _retry_delay(self, attempt: int, response: requests.Response | None = None) -> float:
        provider_delay = self._retry_after_seconds(response)
        if provider_delay is not None:
            return min(provider_delay, float(self.config.retry_max_wait_seconds))
        return min(float(2 ** (attempt - 1)), float(self.config.retry_max_wait_seconds))

    def _request_with_retry(
        self,
        request: Any,
        *,
        table: str,
        operation: str,
        retryable: bool = True,
    ) -> requests.Response:
        """Reintenta solo operaciones REST idempotentes ante fallas transitorias."""
        retries = self.config.max_retries if retryable else 0
        last_error: requests.RequestException | None = None
        for attempt in range(retries + 1):
            response: requests.Response | None = None
            try:
                response = request()
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                transient = True
            else:
                transient = response.status_code in {408, 425, 429, 500, 502, 503, 504}
                if not transient:
                    return response

            if attempt >= retries:
                if last_error is not None:
                    raise last_error
                assert response is not None
                return response

            delay = self._retry_delay(attempt + 1, response)
            logging.warning(
                "Supabase: %s en %s tuvo una falla temporal; reintento %s/%s en %.1f s.",
                operation,
                table,
                attempt + 1,
                retries,
                delay,
            )
            time.sleep(delay)
        raise SupabaseError(f"No se pudo completar {operation} en {table}")

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str = "id") -> None:
        if not rows:
            return
        for start in range(0, len(rows), self.config.batch_size):
            batch = rows[start : start + self.config.batch_size]
            response = self._request_with_retry(
                lambda: self.session.post(
                    self._endpoint(table),
                    params={"on_conflict": on_conflict},
                    headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                    json=batch,
                    timeout=self.config.timeout,
                ),
                table=table,
                operation="upsert",
            )
            self._validate(response, table, "upsert")

    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._request_with_retry(
            lambda: self.session.get(
                self._endpoint(table),
                params=params,
                timeout=self.config.timeout,
            ),
            table=table,
            operation="select",
        )
        self._validate(response, table, "select")
        payload = response.json()
        if not isinstance(payload, list):
            raise SupabaseError(f"Supabase devolvió una respuesta no válida al consultar {table}")
        return [row for row in payload if isinstance(row, dict)]

    def rpc(
        self,
        function: str,
        payload: dict[str, Any],
        *,
        retryable: bool = True,
    ) -> list[dict[str, Any]]:
        response = self._request_with_retry(
            lambda: self.session.post(
                f"{self.config.url}/rest/v1/rpc/{function}",
                json=payload,
                timeout=self.config.timeout,
            ),
            table=function,
            operation="rpc",
            retryable=retryable,
        )
        self._validate(response, function, "rpc")
        result = response.json()
        if not isinstance(result, list):
            raise SupabaseError(f"La función {function} no devolvió una lista")
        return [row for row in result if isinstance(row, dict)]

    def blocked_organizer_names(self) -> set[str]:
        rows = self.select(
            "organizers",
            {"select": "name", "is_blocked": "eq.true"},
        )
        return {normalized(row.get("name")) for row in rows if clean(row.get("name"))}

    def match_existing_events(self, events: list[Event]) -> dict[str, dict[str, Any]]:
        if not events:
            return {}
        # No usamos una URL compartida por varios eventos de la misma corrida;
        # normalmente corresponde a una cartelera y no a una ficha individual.
        url_counts: dict[str, int] = {}
        for event in events:
            url = original_event_url(event)
            if url:
                url_counts[url] = url_counts.get(url, 0) + 1
        candidates = [
            {
                "local_key": clean(event.event_id) or fingerprint(event),
                "external_key": clean(event.event_id) or fingerprint(event),
                "source_url": (
                    original_event_url(event)
                    if url_counts.get(original_event_url(event), 0) == 1
                    else ""
                ),
            }
            for event in events
        ]
        rows = self.rpc("match_existing_events", {"candidates": candidates})
        return {
            clean(row.get("local_key")): row
            for row in rows
            if clean(row.get("local_key"))
        }

    def delete_in(self, table: str, column: str, values: list[str]) -> None:
        if not values:
            return
        for start in range(0, len(values), self.config.batch_size):
            batch = values[start : start + self.config.batch_size]
            response = self._request_with_retry(
                lambda: self.session.delete(
                    self._endpoint(table),
                    params={column: f"in.({','.join(batch)})"},
                    headers={"Prefer": "return=minimal"},
                    timeout=self.config.timeout,
                ),
                table=table,
                operation="delete",
            )
            self._validate(response, table, "delete")

    def delete_where(self, table: str, filters: dict[str, str]) -> None:
        """Elimina por filtros PostgREST explícitos sin descargar las filas."""
        response = self._request_with_retry(
            lambda: self.session.delete(
                self._endpoint(table),
                params=filters,
                headers={"Prefer": "return=minimal"},
                timeout=self.config.timeout,
            ),
            table=table,
            operation="delete",
        )
        self._validate(response, table, "delete")

    def delete_expired_events(self, current_time: str, day_start: str) -> None:
        """Borra eventos terminados; las relaciones desaparecen por ON DELETE CASCADE."""
        self.delete_where("events", {"end_at": f"lt.{current_time}"})
        self.delete_where(
            "events",
            {"end_at": "is.null", "start_at": f"lt.{day_start}"},
        )

    def delete_stale_source_events(self, source_id: str, seen_before: str) -> int:
        """Borra filas no vistas en el snapshot exitoso, conservando exclusiones manuales."""
        stale_ids: list[str] = []
        page_size = 1000
        offset = 0
        while True:
            rows = self.select(
                "events",
                {
                    "select": "id",
                    "source_id": f"eq.{source_id}",
                    "last_seen_at": f"lt.{seen_before}",
                    "is_suppressed": "eq.false",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
            )
            stale_ids.extend(clean(row.get("id")) for row in rows if clean(row.get("id")))
            if len(rows) < page_size:
                break
            offset += page_size
        self.delete_in("events", "id", stale_ids)
        return len(stale_ids)

    def stage_catalog(self, run_id: str, bundle: "SupabaseBundle") -> None:
        """Sube fragmentos a staging sin alterar todavía el catálogo público."""
        entities = {
            "sources": bundle.sources,
            "organizers": bundle.organizers,
            "communes": bundle.communes,
            "categories": bundle.categories,
            "events": bundle.events,
            "event_provenance": bundle.event_provenance,
            "event_categories": bundle.event_categories,
            "media_assets": bundle.media_assets,
        }
        self.delete_where("catalog_staging", {"run_id": f"eq.{run_id}"})
        for entity, rows in entities.items():
            chunks = [
                rows[start : start + self.config.batch_size]
                for start in range(0, len(rows), self.config.batch_size)
            ] or [[]]
            for chunk_index, chunk in enumerate(chunks):
                self.upsert(
                    "catalog_staging",
                    [
                        {
                            "run_id": run_id,
                            "entity": entity,
                            "chunk_index": chunk_index,
                            "payload": chunk,
                        }
                    ],
                    on_conflict="run_id,entity,chunk_index",
                )

    def publish_staged_catalog(
        self,
        run_id: str,
        *,
        reconcile_source_ids: list[str],
        seen_at: str,
        delete_expired: bool,
        current_time: str,
        day_start: str,
    ) -> dict[str, int]:
        """Activa un lote completo dentro de una única transacción PostgreSQL."""
        rows = self.rpc(
            "publish_staged_catalog",
            {
                "p_run_id": run_id,
                "p_reconcile_source_ids": reconcile_source_ids,
                "p_seen_at": seen_at,
                "p_delete_expired": delete_expired,
                "p_current_time": current_time,
                "p_day_start": day_start,
            },
            # Si el servidor completó la transacción pero la respuesta se perdió,
            # repetir esta RPC fallaría porque el staging ya fue eliminado.
            retryable=False,
        )
        result = rows[0] if rows else {}
        return {
            "removed_stale": int(result.get("removed_stale") or 0),
            "removed_expired": int(result.get("removed_expired") or 0),
        }


@dataclass
class SupabaseBundle:
    sources: list[dict[str, Any]] = field(default_factory=list)
    organizers: list[dict[str, Any]] = field(default_factory=list)
    communes: list[dict[str, Any]] = field(default_factory=list)
    categories: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    event_categories: list[dict[str, Any]] = field(default_factory=list)
    media_assets: list[dict[str, Any]] = field(default_factory=list)
    event_provenance: list[dict[str, Any]] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)


def build_supabase_bundle(events: list[Event], seen_at: str | None = None) -> SupabaseBundle:
    seen_at = optional_timestamp(seen_at) or datetime.now(timezone.utc).isoformat()
    sources: dict[str, dict[str, Any]] = {}
    organizers: dict[str, dict[str, Any]] = {}
    communes: dict[str, dict[str, Any]] = {}
    categories: dict[str, dict[str, Any]] = {}
    event_rows: dict[str, dict[str, Any]] = {}
    category_links: dict[tuple[str, str], dict[str, Any]] = {}
    media: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, Any]] = {}

    for event in events:
        # Defensa adicional para cargas realizadas desde un JSON antiguo.
        if not is_upcoming(event):
            continue
        external_key = clean(event.event_id) or fingerprint(event)
        event_id = stable_uuid("event", external_key)
        source_name = clean(event.source_name) or "Fuente desconocida"
        source_id = stable_uuid("source", source_name)
        organizer_name = clean(event.organizer)
        organizer_id = stable_uuid("organizer", organizer_name) if organizer_name else None
        commune_name = clean(event.commune)
        commune_key = "|".join([clean(event.country), clean(event.region), commune_name])
        commune_id = stable_uuid("commune", commune_key) if commune_name else None
        fallback_location = ", ".join(
            value
            for value in (
                clean(event.venue),
                clean(event.address),
                clean(event.commune or event.city),
                clean(event.region),
            )
            if value
        ) or clean(event.country) or "Chile"
        fallback_precision = (
            "exact"
            if clean(event.address)
            else "venue"
            if clean(event.venue)
            else "commune"
            if clean(event.commune)
            else "city"
            if clean(event.city)
            else "region"
            if clean(event.region)
            else "country"
        )

        sources[source_id] = {
            "id": source_id,
            "name": source_name,
            "website_url": website_origin(event.source_url or event.official_url),
            "is_active": True,
            "last_seen_at": seen_at,
        }
        if organizer_id:
            organizers[organizer_id] = {
                "id": organizer_id,
                "name": organizer_name,
                "last_seen_at": seen_at,
            }
        if commune_id:
            communes[commune_id] = {
                "id": commune_id,
                "name": commune_name,
                "region": clean(event.region) or None,
                "country": clean(event.country) or "Chile",
            }

        event_rows[event_id] = {
            "id": event_id,
            "external_key": external_key,
            "source_id": source_id,
            "organizer_id": organizer_id,
            "commune_id": commune_id,
            "title": clean(event.title),
            "start_at": optional_timestamp(event.start_date),
            "end_at": optional_timestamp(event.end_date),
            "venue": clean(event.venue) or None,
            "address": clean(event.address) or None,
            "city": clean(event.city) or None,
            "region": clean(event.region) or None,
            "country": clean(event.country) or "Chile",
            "postal_code": clean(event.postal_code) or None,
            "latitude": optional_float(event.latitude, -90.0, 90.0),
            "longitude": optional_float(event.longitude, -180.0, 180.0),
            "location": clean(event.location) or fallback_location,
            "location_precision": clean(event.location_precision) or fallback_precision,
            "location_source": clean(event.location_source) or (
                "source-data"
                if any(clean(value) for value in (event.venue, event.address, event.commune, event.city))
                else "geographic-fallback"
            ),
            "status": "published",
            "audience": clean(event.audience) or None,
            "is_free": optional_bool(event.is_free),
            "description": clean(event.description) or None,
            "price_text": clean(event.price) or None,
            "currency": clean(event.currency) or "CLP",
            "image_url": clean(event.image_url) or None,
            "source_url": clean(event.source_url) or None,
            "official_url": clean(event.official_url) or None,
            "extraction_method": clean(event.extraction_method) or None,
            "scraped_at": optional_timestamp(event.extracted_at) or seen_at,
            "last_seen_at": seen_at,
        }
        provenance[event_id] = {
            "event_id": event_id,
            "original_url": original_event_url(event) or None,
            "source_description": clean(event.source_description) or None,
            "ocr_text": clean(event.ocr_text) or None,
            "is_rewritten": bool(event.is_rewritten),
        }

        for category_name in event.categories:
            name = clean(category_name)
            if not name:
                continue
            category_id = stable_uuid("category", name)
            categories[category_id] = {"id": category_id, "name": name}
            category_links[(event_id, category_id)] = {
                "event_id": event_id,
                "category_id": category_id,
            }

        image_url = clean(event.image_url)
        if image_url:
            media_id = stable_uuid("media", f"{event_id}|{image_url}")
            media[media_id] = {
                "id": media_id,
                "event_id": event_id,
                "url": image_url,
                "media_type": "image",
                "is_primary": True,
            }

    return SupabaseBundle(
        sources=list(sources.values()),
        organizers=list(organizers.values()),
        communes=list(communes.values()),
        categories=list(categories.values()),
        events=list(event_rows.values()),
        event_categories=list(category_links.values()),
        media_assets=list(media.values()),
        event_provenance=list(provenance.values()),
        event_ids=list(event_rows),
    )


class SupabaseExporter:
    def __init__(self, client: SupabaseRestClient):
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseExporter":
        return cls(SupabaseRestClient(SupabaseConfig.from_env()))

    def prepare_for_enrichment(self, events: list[Event]) -> tuple[list[Event], list[Event]]:
        """Filtra organizadores bloqueados y separa eventos nuevos de conocidos."""
        blocked = self.client.blocked_organizer_names()
        allowed = [event for event in events if normalized(event.organizer) not in blocked]
        blocked_count = len(events) - len(allowed)
        if blocked_count:
            logging.info("Supabase: %s eventos omitidos por organizador bloqueado", blocked_count)

        matches = self.client.match_existing_events(allowed)
        ocr_enabled = normalized(os.getenv("NVIDIA_OCR_ENABLED", "true")) in TRUE_VALUES
        publishable: list[Event] = []
        new_events: list[Event] = []
        suppressed_count = 0
        for event in allowed:
            local_key = clean(event.event_id) or fingerprint(event)
            match = matches.get(local_key)
            if not match:
                publishable.append(event)
                new_events.append(event)
                continue
            if match.get("stored_is_suppressed") is True:
                suppressed_count += 1
                continue
            # Mantiene el identificador remoto incluso cuando la coincidencia fue
            # por URL y la clave combinada cambió levemente.
            fresh_source_description = clean(event.source_description or event.description)
            stored_description = clean(match.get("stored_description"))
            stored_source_description = clean(match.get("stored_source_description"))
            stored_ocr_text = clean(match.get("stored_ocr_text"))
            stored_rewritten_value = match.get("stored_is_rewritten")
            if isinstance(stored_rewritten_value, bool):
                stored_is_rewritten = stored_rewritten_value
            else:
                # Compatibilidad mientras se aplica la migración que expone el indicador.
                stored_is_rewritten = bool(
                    stored_source_description
                    and stored_description
                    and normalized(stored_source_description) != normalized(stored_description)
                )
            source_changed = bool(
                fresh_source_description
                and stored_source_description
                and normalized(fresh_source_description) != normalized(stored_source_description)
            )
            event.event_id = clean(match.get("stored_external_key")) or local_key
            if source_changed:
                # Publica provisionalmente la nueva información factual y vuelve a intentar
                # la redacción; nunca presenta una versión antigua como si estuviera actualizada.
                event.description = fresh_source_description
                event.source_description = fresh_source_description
                event.is_rewritten = False
            else:
                event.description = stored_description or event.description
                event.source_description = (
                    stored_source_description or fresh_source_description or event.description
                )
                event.is_rewritten = stored_is_rewritten
            event.ocr_text = stored_ocr_text or event.ocr_text
            publishable.append(event)
            needs_ocr = bool(
                ocr_enabled and clean(event.image_url) and not clean(event.ocr_text)
            )
            needs_rewrite = bool(
                not event.is_rewritten
                and clean(event.source_description or event.ocr_text)
            )
            if source_changed or needs_ocr or needs_rewrite:
                new_events.append(event)
        if suppressed_count:
            logging.info("Supabase: %s eventos omitidos por exclusión individual", suppressed_count)
        logging.info(
            "Supabase: %s eventos omiten OCR/IA; %s son nuevos o requieren migración editorial.",
            len(publishable) - len(new_events),
            len(new_events),
        )
        return publishable, new_events

    def _without_blocked_organizers(self, events: list[Event]) -> list[Event]:
        method = getattr(self.client, "blocked_organizer_names", None)
        if not callable(method):
            return events
        blocked = method()
        return [event for event in events if normalized(event.organizer) not in blocked]

    def _run_row(
        self,
        run_id: str,
        started_at: str,
        status: str,
        events_count: int,
        *,
        finished_at: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "runner": "github-actions" if os.getenv("GITHUB_ACTIONS") == "true" else "local",
            "repository": clean(os.getenv("GITHUB_REPOSITORY")) or None,
            "github_run_id": clean(os.getenv("GITHUB_RUN_ID")) or None,
            "git_sha": clean(os.getenv("GITHUB_SHA")) or None,
            "event_name": clean(os.getenv("GITHUB_EVENT_NAME")) or None,
        }
        return {
            "id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "events_count": events_count,
            "error_message": error_message,
            "metadata": metadata,
        }

    def export(
        self,
        events: list[Event],
        *,
        reconcile_sources: set[str] | None = None,
    ) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        events = self._without_blocked_organizers(events)
        bundle = build_supabase_bundle(events, started_at)
        self.client.upsert(
            "scrape_runs",
            [self._run_row(run_id, started_at, "running", len(bundle.events))],
        )
        try:
            current = chile_now()
            day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
            reconcile_enabled = normalized(
                os.getenv("SUPABASE_RECONCILE_STALE", "true")
            ) in TRUE_VALUES
            reconcile_source_ids = [
                stable_uuid("source", source_name)
                for source_name in sorted(reconcile_sources or set(), key=str.casefold)
                if reconcile_enabled and clean(source_name)
            ]
            delete_expired = normalized(
                os.getenv("SUPABASE_DELETE_EXPIRED", "true")
            ) in TRUE_VALUES
            self.client.stage_catalog(run_id, bundle)
            publication = self.client.publish_staged_catalog(
                run_id,
                reconcile_source_ids=reconcile_source_ids,
                seen_at=started_at,
                delete_expired=delete_expired,
                current_time=current.isoformat(),
                day_start=day_start.isoformat(),
            )
            logging.info(
                "Supabase: publicación atómica completada; obsoletos=%s, vencidos=%s.",
                publication["removed_stale"],
                publication["removed_expired"],
            )
        except Exception as exc:
            try:
                self.client.upsert(
                    "scrape_runs",
                    [
                        self._run_row(
                            run_id,
                            started_at,
                            "failed",
                            len(bundle.events),
                            finished_at=datetime.now(timezone.utc).isoformat(),
                            error_message=clean(exc)[:2000],
                        )
                    ],
                )
            except Exception:
                logging.exception("No se pudo registrar el fallo de la carga en Supabase")
            raise

        self.client.upsert(
            "scrape_runs",
            [
                self._run_row(
                    run_id,
                    started_at,
                    "succeeded",
                    len(bundle.events),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            ],
        )
        logging.info("Supabase: %s eventos sincronizados (ejecución %s)", len(bundle.events), run_id)
