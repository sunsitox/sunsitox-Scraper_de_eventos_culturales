"""Inspección HTTP respetuosa con límites por dominio y robots.txt."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .models import FetchObservation, SourceCandidate


EVENT_LINK_RE = re.compile(r"agenda|cartelera|evento|eventos|programaci[oó]n|calendario|actividad", re.I)
DATE_RE = re.compile(
    r"\b(?:[0-3]?\d[./-][01]?\d(?:[./-](?:20)?\d{2})?|(?:lunes|martes|mi[eé]rcoles|jueves|viernes|sábado|sabado|domingo)\b|(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b)",
    re.I,
)


@dataclass
class _DomainState:
    lock: threading.Lock
    last_request: float = 0.0


class SourceFetcher:
    def __init__(
        self,
        timeout: int = 25,
        domain_delay: float = 1.25,
        max_bytes: int = 1_500_000,
        check_robots: bool = True,
    ) -> None:
        self.timeout = max(1, timeout)
        self.domain_delay = max(0.0, domain_delay)
        self.max_bytes = max(50_000, max_bytes)
        self.check_robots = check_robots
        self.user_agent = "EventosCulturalesSourceValidator/1.0 (+auditoria de fuentes publicas; Chile)"
        self._local = threading.local()
        self._states: dict[str, _DomainState] = {}
        self._states_lock = threading.Lock()
        self._robots: dict[str, bool | None] = {}

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json,application/pdf;q=0.9,*/*;q=0.5",
                "Accept-Language": "es-CL,es;q=0.9,en;q=0.5",
            })
            self._local.session = session
        return session

    def _state(self, origin: str) -> _DomainState:
        with self._states_lock:
            return self._states.setdefault(origin, _DomainState(threading.Lock()))

    def _request(self, url: str) -> requests.Response:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
        state = self._state(origin)
        with state.lock:
            wait = self.domain_delay - (time.monotonic() - state.last_request)
            if wait > 0:
                time.sleep(wait)
            response = self._session().get(url, timeout=self.timeout, allow_redirects=True, stream=True)
            state.last_request = time.monotonic()
            return response

    def _robots_allowed(self, url: str) -> bool | None:
        if not self.check_robots:
            return None
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
        with self._states_lock:
            if origin in self._robots:
                return self._robots[origin]
        robots_url = urljoin(origin, "/robots.txt")
        allowed: bool | None = None
        try:
            response = self._request(robots_url)
            if response.status_code == 200:
                text = response.text[:300_000]
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(text.splitlines())
                allowed = parser.can_fetch(self.user_agent, url)
            elif response.status_code in {401, 403}:
                allowed = False
        except requests.RequestException:
            allowed = None
        with self._states_lock:
            self._robots[origin] = allowed
        return allowed

    @staticmethod
    def _html_facts(content: bytes, encoding: str | None) -> tuple[str, str, int, int, int]:
        html = content.decode(encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        structured = 0
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or script.get_text() or "null")
            except (json.JSONDecodeError, TypeError):
                continue
            stack = payload if isinstance(payload, list) else [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    kind = item.get("@type")
                    kinds = kind if isinstance(kind, list) else [kind]
                    if any(str(value).casefold().endswith("event") for value in kinds if value):
                        structured += 1
                    graph = item.get("@graph")
                    if isinstance(graph, list):
                        stack.extend(graph)
                elif isinstance(item, list):
                    stack.extend(item)
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        event_links = sum(
            1 for anchor in soup.find_all("a", href=True)
            if EVENT_LINK_RE.search(f"{anchor.get_text(' ', strip=True)} {anchor.get('href', '')}")
        )
        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()
        visible = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()[:80_000]
        dates = len(DATE_RE.findall(visible[:40_000]))
        return title[:300], visible, structured, dates, min(event_links, 999)

    def fetch(self, source: SourceCandidate) -> FetchObservation:
        observation = FetchObservation(requested_url=source.url)
        if not source.url:
            observation.error = "sin_url_eventos"
            return observation
        parsed = urlparse(source.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            observation.error = "url_invalida"
            return observation
        observation.robots_allowed = self._robots_allowed(source.url)
        if observation.robots_allowed is False:
            observation.error = "robots_txt_no_permite"
            return observation
        started = time.perf_counter()
        try:
            response = self._request(source.url)
            observation.elapsed_seconds = round(time.perf_counter() - started, 3)
            observation.final_url = response.url
            observation.status_code = response.status_code
            observation.content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                remaining = self.max_bytes - size
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                size += len(chunks[-1])
            content = b"".join(chunks)
            observation.bytes_read = len(content)
            if response.status_code >= 400:
                observation.error = f"http_{response.status_code}"
                return observation
            if "html" in observation.content_type or content.lstrip().startswith(b"<"):
                facts = self._html_facts(content, response.encoding)
                observation.title, observation.visible_text, observation.structured_event_count, observation.date_signal_count, observation.event_link_count = facts
            elif "json" in observation.content_type:
                observation.visible_text = content.decode(response.encoding or "utf-8", errors="replace")[:80_000]
                observation.date_signal_count = len(DATE_RE.findall(observation.visible_text[:40_000]))
            elif "pdf" in observation.content_type:
                observation.title = source.name
                observation.visible_text = "Documento PDF " + source.name + " " + source.categories
            else:
                observation.visible_text = content.decode(response.encoding or "utf-8", errors="replace")[:20_000]
            return observation
        except requests.exceptions.SSLError as exc:
            observation.error = "error_tls: " + str(exc)[:240]
        except requests.exceptions.Timeout as exc:
            observation.error = "timeout: " + str(exc)[:240]
        except requests.RequestException as exc:
            observation.error = "error_red: " + str(exc)[:240]
        observation.elapsed_seconds = round(time.perf_counter() - started, 3)
        return observation
