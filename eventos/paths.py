"""Rutas y valores compartidos por la aplicación."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data"
CACHE_DIR = ROOT / ".cache" / "http"
SOURCES_DIR = ROOT / "sources"
SETTINGS_FILE = ROOT / "settings.json"
PLAYWRIGHT_DIR = ROOT / ".playwright-browsers"

REQUEST_DELAY_SECONDS = 0.8
TIMEOUT_SECONDS = 30
USER_AGENT = "EventosCulturalesMVP/0.3 (+proyecto académico/MVP; Chile)"
