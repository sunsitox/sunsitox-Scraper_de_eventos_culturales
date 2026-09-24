"""Estado durable del pipeline: commits SQLite atómicos por ficha y operación."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import Event
from .paths import ROOT


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def event_rows(events):
    return [asdict(event) for event in events]


def load_events(rows):
    return [Event(**{k: v for k, v in row.items() if k in Event.__dataclass_fields__}) for row in rows]


class StateStore:
    """Una conexión por transacción permite compartir estado entre fuentes paralelas."""

    def __init__(self, path: Path | None = None, clock=time.time):
        self.path = Path(path or os.getenv("PIPELINE_STATE_PATH", str(ROOT / "state/pipeline.sqlite3")))
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS state (scope TEXT, key TEXT, value TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(scope,key))")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, scope, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE scope=? AND key=?", (scope, key)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, scope, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?,?)", (scope, key, json.dumps(value, ensure_ascii=False), self.clock()))

    def begin_cycle(self):
        cycle = self.get("pipeline", "cycle", {})
        age = self.clock() - cycle.get("started", 0)
        if cycle.get("complete") is not False or age > float(os.getenv("CHECKPOINT_MAX_AGE_HOURS", "24")) * 3600:
            cycle = {"id": uuid.uuid4().hex, "started": self.clock(), "complete": False}
            self.put("pipeline", "cycle", cycle)
        return cycle["id"]

    def finish_cycle(self):
        cycle = self.get("pipeline", "cycle", {})
        cycle["complete"] = True
        self.put("pipeline", "cycle", cycle)

    def prune(self, days=90):
        # Historial de IA/fichas con retención acotada. Nunca elimina el ciclo activo.
        with self.connect() as db:
            db.execute("DELETE FROM state WHERE scope != 'pipeline' AND updated < ?", (self.clock() - days * 86400,))


class DetailCache:
    """Reutiliza resultados parseados, incluyendo fichas históricas sin eventos vigentes."""

    def __init__(self, store, source):
        self.store = store
        self.scope = "details:" + digest(dict(source))
        self.refresh = float(source.get("detail_refresh_hours", 24)) * 3600
        self.audit = float(source.get("full_audit_days", 7)) * 86400

    def load(self, url, revision=""):
        row = self.store.get(self.scope, url)
        if row is None or row["revision"] != revision:
            return None
        age = self.store.clock() - row["checked"]
        # Con modified se evita reabrir el histórico; sin versión se revalida por TTL.
        if age >= (self.audit if revision else self.refresh):
            return None
        return load_events(row["events"])

    def save(self, url, events, revision=""):
        self.store.put(self.scope, url, {"revision": revision, "checked": self.store.clock(), "events": event_rows(events)})
