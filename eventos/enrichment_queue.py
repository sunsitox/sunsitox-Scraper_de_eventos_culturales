"""Cola persistente con versiones de entrada separadas para OCR, redacción y ubicación."""

import os
import uuid
from dataclasses import asdict

from .state import digest


class EnrichmentQueue:
    def __init__(self, store):
        self.store = store
        self.attempt = uuid.uuid4().hex

    def key(self, stage, event):
        if stage == "ocr":
            # Identidad del recurso, no hash de sus bytes: una URL estable requiere revalidación periódica.
            return digest(["ocr-v1", event.image_url, os.getenv("NVIDIA_OCR_MODEL", "meta/llama-3.2-11b-vision-instruct")])
        fields = ("title", "start_date", "end_date", "venue", "address", "commune", "region", "organizer", "categories", "price", "currency", "source_description", "ocr_text")
        return digest([stage + "-v1", {k: getattr(event, k) for k in fields}, os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")])

    def ready(self, stage, event):
        key = self.key(stage, event)
        row = self.store.get("ai:" + stage, key, {})
        expired = stage == "ocr" and self.store.clock() - row.get("finished", 0) >= float(os.getenv("OCR_CACHE_DAYS", "30")) * 86400
        if row.get("status") == "completed" and not expired:
            for field, value in row["output"].items():
                setattr(event, field, value)
            return False
        if row.get("attempt") == self.attempt:
            return False
        if stage == "ocr" and (
            (expired and row.get("status") == "completed")
            or row.get("status") in {"pending", "processing", "retryable_error"}
        ):
            event.ocr_text = ""
        # processing de otra ejecución se recupera: pudo interrumpirse entre llamada y commit.
        self.store.put("ai:" + stage, key, {"status": "pending", "event": asdict(event), "attempts": row.get("attempts", 0)})
        return True

    def start(self, stage, event):
        key = self.key(stage, event)
        row = self.store.get("ai:" + stage, key, {})
        row.update(status="processing", attempt=self.attempt, attempts=row.get("attempts", 0) + 1)
        self.store.put("ai:" + stage, key, row)
        return key

    def attempts(self, stage, event):
        return self.store.get("ai:" + stage, self.key(stage, event), {}).get("attempts", 0)

    def finish(self, stage, key, output=None, error=""):
        row = self.store.get("ai:" + stage, key, {})
        row.update(status="completed" if output is not None else "retryable_error", output=output or {}, error=error[:500], finished=self.store.clock())
        self.store.put("ai:" + stage, key, row)

    def reconcile_input(self, event, raw):
        """Invalida redacción por cambios factuales, aun si Supabase solo comparó descripción."""
        identity = event.event_id or digest([raw.source_name, raw.source_url, raw.title, raw.start_date[:10]])
        fields = ("title", "start_date", "end_date", "venue", "address", "commune", "region", "organizer", "categories", "price", "currency", "image_url", "source_description")
        current = {key: getattr(raw, key) for key in fields}
        previous = self.store.get("inputs", identity, {})
        old = previous.get("raw")
        invalidated = bool(previous.get("invalidated") or (old and old != current))
        image_changed = bool(previous.get("image_changed") or (old and old.get("image_url") != raw.image_url))
        if invalidated:
            event.description = raw.source_description or raw.description
            event.source_description = raw.source_description or raw.description
            event.is_rewritten = False
            if image_changed:
                event.ocr_text = ""
        self.store.put("inputs", identity, {"raw": current, "invalidated": invalidated, "image_changed": image_changed})
