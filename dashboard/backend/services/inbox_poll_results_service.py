"""Consulta votos de enquetes enviadas no Evolution GO, sem alterar webhooks de mensagens."""

import hashlib
from urllib.parse import quote

import requests


def fetch_results(api_url: str, api_key: str, message_id: str) -> dict | None:
    url = f"{api_url.rstrip('/')}/polls/{quote(message_id, safe='')}/results"
    response = requests.get(url, headers={"apikey": api_key}, timeout=10)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = body["data"]
    if not isinstance(body, dict) or body.get("pollMessageId") != message_id:
        raise ValueError("Resposta de enquete inválida recebida do provedor.")
    return body


def parse_votes(result: dict, options: list[str]) -> list[dict]:
    """Traduz hashes SHA-256 do Evolution GO para o snapshot enviado."""
    by_hash = {hashlib.sha256(option.encode("utf-8")).hexdigest(): option for option in options}
    raw_votes = result.get("votes")
    if not isinstance(raw_votes, list):
        raise ValueError("Lista de votos inválida recebida do provedor.")
    votes = []
    for raw in raw_votes:
        if not isinstance(raw, dict):
            continue
        voter_key = str(raw.get("voterJid") or raw.get("voterPhone") or "").strip()[:255]
        selected = raw.get("selectedOptions")
        if not voter_key or not isinstance(selected, list):
            continue
        mapped = [by_hash[value.lower()] for value in selected if isinstance(value, str) and value.lower() in by_hash]
        if not mapped and selected:
            continue
        votes.append({
            "voter_key": voter_key,
            "voter_name": str(raw.get("voterName") or "")[:255],
            "selected_options": list(dict.fromkeys(mapped)),
            "vote_message_id": str(raw.get("voteMessageId") or "")[:255],
            "voted_at": raw.get("votedAt") or None,
        })
    return votes
