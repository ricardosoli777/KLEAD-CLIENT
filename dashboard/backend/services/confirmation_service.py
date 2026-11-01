"""Regras puras para modelos e mensagens de confirmação do CRM."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy


DEFAULT_OPTIONS = [
    {"id": "confirm", "label": "CONFIRMAR", "emoji": "✅", "synonyms": ["confirmo", "sim"], "auto_reply": "✅ Visita confirmada com sucesso."},
    {"id": "reschedule", "label": "REMARCAR", "emoji": "📅", "synonyms": ["reagendar", "outra data"], "auto_reply": "📅 Sua solicitação de remarcação foi registrada. Entraremos em contato para definir uma nova data."},
    {"id": "wait", "label": "RESPONDER DEPOIS", "emoji": "⏳", "synonyms": ["esperar", "aguardar", "depois"], "auto_reply": "⏳ Tudo bem. Ficaremos aguardando sua confirmação."},
]

LEGACY_DEFAULT_OPTIONS = [
    {"id": "confirm", "label": "CONFIRMAR", "emoji": "✅", "synonyms": ["confirmo", "sim"]},
    {"id": "reschedule", "label": "REMARCAR", "emoji": "📅", "synonyms": ["reagendar", "outra data"]},
    {"id": "wait", "label": "ESPERAR", "emoji": "⏳", "synonyms": ["aguardar", "depois"]},
]

PRE_AUTO_REPLY_DEFAULT_OPTIONS = [
    {"id": "confirm", "label": "CONFIRMAR", "emoji": "✅", "synonyms": ["confirmo", "sim"]},
    {"id": "reschedule", "label": "REMARCAR", "emoji": "📅", "synonyms": ["reagendar", "outra data"]},
    {"id": "wait", "label": "RESPONDER DEPOIS", "emoji": "⏳", "synonyms": ["esperar", "aguardar", "depois"]},
]

LEGACY_DEFAULT_MESSAGES = {
    "appointment": "Olá, {nome}! Podemos confirmar seu atendimento para {data} às {hora}?",
    "visit": "Olá, {nome}! Podemos confirmar sua visita para {data} às {hora}?",
    "meeting": "Olá, {nome}! Podemos confirmar nossa reunião para {data} às {hora}?",
    "proposal": "Olá, {nome}! Podemos conversar sobre a proposta enviada?",
    "custom": "Olá, {nome}! Escolha uma das opções abaixo:",
}

DEFAULT_TEMPLATES = [
    {
        "key": "appointment",
        "name": "Confirmação de atendimento",
        "message_text": "Olá, {nome}! Precisamos confirmar seu atendimento para {data} às {hora}. Escolha uma das opções abaixo para registrarmos sua resposta.",
    },
    {
        "key": "visit",
        "name": "Confirmação de visita",
        "message_text": "Olá, {nome}! Confirme sua visita agendada para {data} às {hora}. Escolha uma das opções abaixo.",
    },
    {
        "key": "meeting",
        "name": "Confirmação de reunião",
        "message_text": "Olá, {nome}! Confirme sua participação na reunião de {data} às {hora}. Escolha uma das opções abaixo.",
    },
    {
        "key": "proposal",
        "name": "Retorno de proposta",
        "message_text": "Olá, {nome}! Informe como deseja prosseguir com a proposta enviada. Escolha uma das opções abaixo.",
    },
    {
        "key": "custom",
        "name": "Mensagem personalizada",
        "message_text": "Olá, {nome}! Precisamos da sua resposta para continuarmos. Escolha uma das opções abaixo.",
    },
]

_ALLOWED_VARIABLES = {"nome", "data", "hora", "servico", "atendente"}
_VARIABLE_RE = re.compile(r"\{([a-z_]+)\}")


def default_templates() -> list[dict]:
    result = []
    for item in DEFAULT_TEMPLATES:
        template = deepcopy(item)
        template["options"] = deepcopy(DEFAULT_OPTIONS)
        result.append(template)
    return result


def normalize_options(options: list[dict]) -> list[dict]:
    if not isinstance(options, list) or not 1 <= len(options) <= 3:
        raise ValueError("Informe de uma a três opções.")
    normalized = []
    ids: set[str] = set()
    labels: set[str] = set()
    for index, raw in enumerate(options):
        option_id = re.sub(r"[^a-z0-9_-]", "", str(raw.get("id", "")).strip().lower())
        label = str(raw.get("label", "")).strip()
        emoji = str(raw.get("emoji", "")).strip()[:8]
        if not option_id or not label:
            raise ValueError("Cada opção precisa de id e texto.")
        label_key = normalize_reply(label)
        if option_id in ids or label_key in labels:
            raise ValueError("IDs e textos das opções não podem se repetir.")
        ids.add(option_id)
        labels.add(label_key)
        synonyms = []
        for synonym in raw.get("synonyms") or []:
            value = str(synonym).strip()
            if value and normalize_reply(value) != label_key:
                synonyms.append(value[:80])
        normalized.append({
            "id": option_id[:64],
            "label": label[:80],
            "emoji": emoji,
            "synonyms": synonyms[:12],
            "auto_reply": str(raw.get("auto_reply") or "").strip()[:1000],
            "position": index,
        })
    return normalized


def validate_message_text(message_text: str) -> str:
    text = str(message_text or "").strip()
    if not text:
        raise ValueError("O texto da mensagem é obrigatório.")
    if len(text) > 4000:
        raise ValueError("O texto da mensagem deve ter no máximo 4000 caracteres.")
    unknown = sorted(set(_VARIABLE_RE.findall(text)) - _ALLOWED_VARIABLES)
    if unknown:
        raise ValueError(f"Variáveis desconhecidas: {', '.join(unknown)}")
    return text


def render_message(message_text: str, options: list[dict], variables: dict, public_url: str = "") -> str:
    text = render_template_text(message_text, variables)
    normalized = normalize_options(options)
    for key in _ALLOWED_VARIABLES:
        text = text.replace("{" + key + "}", str(variables.get(key) or "").strip())
    option_lines = [f"{item['emoji']} {item['label']}".strip() for item in normalized]
    parts = [text, "Responda com uma das opções:", "\n".join(option_lines)]
    if public_url:
        parts.extend(["Se preferir, escolha tocando aqui:", public_url.strip()])
    return "\n\n".join(part for part in parts if part)


def render_template_text(message_text: str, variables: dict) -> str:
    text = validate_message_text(message_text)
    for key in _ALLOWED_VARIABLES:
        text = text.replace("{" + key + "}", str(variables.get(key) or "").strip())
    return text


def emphasize_whatsapp_details(text: str, lead_name: str = "") -> str:
    """Aplica negrito WhatsApp a nomes, datas e horários sem duplicar marcação."""
    result = str(text or "")
    name = str(lead_name or "").strip()

    def bold_matches(value: str, pattern: str, flags: int = 0) -> str:
        # Preserva trechos já delimitados por *...* para a função ser idempotente.
        parts = re.split(r"(\*[^*\n]+\*)", value)
        return "".join(
            part if part.startswith("*") and part.endswith("*")
            else re.sub(pattern, lambda match: f"*{match.group(0)}*", part, flags=flags)
            for part in parts
        )

    if name:
        result = bold_matches(result, re.escape(name), re.IGNORECASE)
        first_name = name.split()[0]
        if first_name and not re.search(rf"\*{re.escape(name)}\*", result, re.IGNORECASE):
            result = bold_matches(result, rf"\b{re.escape(first_name)}\b", re.IGNORECASE)
    patterns = (
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)(?:\s+de\s+\d{4})?\b",
        r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d|h(?:[0-5]\d)?)(?:\s*(?:h|horas?))?\b",
    )
    for pattern in patterns:
        result = bold_matches(result, pattern, re.IGNORECASE)
    return result


def normalize_reply(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized.strip().casefold())


def match_reply(value: str, options: list[dict]) -> str | None:
    candidate = normalize_reply(value)
    if not candidate:
        return None
    matches = []
    for option in normalize_options(options):
        accepted = {normalize_reply(option["label"])}
        accepted.update(normalize_reply(item) for item in option["synonyms"])
        if candidate in accepted:
            matches.append(option["id"])
    return matches[0] if len(matches) == 1 else None
