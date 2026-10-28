"""Valida respuestas sin registrar claves, afiches ni el cuerpo del proveedor."""

import json

import requests


def chat_content(response: requests.Response) -> str:
    response.raise_for_status()
    headers = response.headers
    content_type = headers.get("Content-Type", "desconocido")
    if not isinstance(content_type, str):
        content_type = "desconocido"
    content_type = content_type.split(";", 1)[0][:80]
    body = response.content
    size = len(body) if isinstance(body, (str, bytes)) else "desconocido"
    context = f"HTTP {response.status_code}, tipo={content_type}, bytes={size}"
    try:
        envelope = response.json()
    except ValueError as exc:
        # Extra data puede proceder de la envoltura HTTP, no del texto del modelo.
        raise ValueError(f"Envoltura HTTP NVIDIA no es JSON válido ({context})") from exc
    if not isinstance(envelope, dict):
        raise ValueError(f"Envoltura HTTP NVIDIA no es un objeto ({context})")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError(f"Respuesta NVIDIA sin choices válidos ({context})")
    choice = choices[0]
    if choice.get("finish_reason") in {"length", "content_filter"}:
        raise ValueError(f"Respuesta NVIDIA incompleta o filtrada (fin={choice['finish_reason']})")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        if not all(isinstance(part, dict) and part.get("type") == "text"
                   and isinstance(part.get("text"), str) for part in content):
            raise ValueError("Respuesta NVIDIA con bloques de contenido no compatibles")
        content = "\n".join(part["text"] for part in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"Respuesta NVIDIA sin texto utilizable ({context})")
    return content


def json_object(content: str, expected_key: str) -> dict:
    """Acepta cercos/preambulos y una lista unitaria, nunca inventa campos."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        if text.startswith(("{", "[")):
            # No rescatar subobjetos de una estructura truncada ni elegir entre
            # varios objetos concatenados: podría publicarse un resultado parcial.
            raise ValueError(
                f"JSON del modelo incompleto o con datos adicionales "
                f"(campo={expected_key}, caracteres={len(text)})"
            ) from None
        # Recupera un objeto completo precedido por una explicación del modelo.
        decoder = json.JSONDecoder()
        value = None
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and expected_key in candidate:
                value = candidate
                break
        if value is None:
            raise ValueError(
                f"Contenido del modelo no contiene JSON completo con {expected_key} "
                f"(caracteres={len(text)})"
            ) from None
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict) or expected_key not in value:
        raise ValueError(f"Contenido del modelo no contiene un objeto con {expected_key}")
    return value
