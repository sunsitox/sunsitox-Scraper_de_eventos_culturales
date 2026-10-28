"""Lectura declarativa de campos, fichas y enlaces de carteleras HTML."""

import re
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from ..extractors import extract_jsonld
from ..text import clean


def same_site_url(base: str, href: str) -> str:
    url = urldefrag(urljoin(base, href))[0]
    origin, target = urlparse(base), urlparse(url)
    if target.scheme not in {"http", "https"} or target.hostname != origin.hostname:
        raise ValueError("La cartelera contiene un enlace fuera del dominio permitido")
    return url


def selector_value(card, rule) -> str:
    if not rule:
        return ""
    if isinstance(rule, str):
        selector, attribute, options = rule, "text", {}
    else:
        selector = clean(rule.get("selector"))
        attribute = clean(rule.get("attribute")) or "text"
        options = rule
    node = card if selector == ":scope" else card.select_one(selector) if selector else None
    if not node:
        return ""
    value = clean(node.get_text(" ")) if attribute == "text" else clean(node.get(attribute, ""))
    pattern = options.get("pattern")
    if pattern:
        match = re.search(pattern, value, re.I)
        value = clean(match.group(1) if match and match.lastindex else match.group(0) if match else "")
    if options.get("format") == "time" and value:
        match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:h(?:rs?\.?|oras?)?)?", value, re.I)
        if not match or int(match[1]) > 23 or int(match[2] or 0) > 59:
            return ""
        value = f"{int(match[1]):02d}:{int(match[2] or 0):02d}"
    return value


def detail_values(http, source, url):
    selectors = source.get("detail_selectors", {})
    jsonld_fields = source.get("detail_jsonld_fields", [])
    if not selectors and not jsonld_fields:
        return {}
    if not isinstance(selectors, dict):
        raise ValueError("detail_selectors debe ser un objeto de selectores")
    url = same_site_url(source.url, url)
    html = http.get_html(url, verify=bool(source.get("verify_ssl", True)))
    if not html:
        raise ValueError("La ficha de detalle no devolvió HTML")
    soup = BeautifulSoup(html, "html.parser")
    for selector in source.get("detail_remove_selectors", []):
        for node in soup.select(selector):
            node.decompose()
    values = {}
    if jsonld_fields:
        candidates = extract_jsonld(html, source, url)
        matching = [event for event in candidates if event.official_url.rstrip("/") == url.rstrip("/")]
        candidates = matching or candidates
        if len(candidates) != 1:
            raise ValueError("La ficha no contiene un único evento JSON-LD identificable")
        event = candidates[0]
        values.update({field: getattr(event, field) for field in jsonld_fields})
    values.update({field: selector_value(soup, rule) for field, rule in selectors.items()})
    missing = [field for field in source.get("detail_required_fields", []) if not values.get(field)]
    if missing:
        raise ValueError("La ficha perdió campos obligatorios: " + ", ".join(missing))
    return {field: value for field, value in values.items() if value}
