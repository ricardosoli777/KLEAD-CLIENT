"""Resolvedor de identidade em modo shadow, sem efeitos colaterais."""

from __future__ import annotations

from typing import Any


def _identity_aliases(source_meta: dict[str, Any] | None) -> set[str]:
    evolution = (source_meta or {}).get("evolution_identity") or {}
    aliases = set(evolution.get("jid_aliases") or [])
    for key in ("pn_jid", "lid_jid", "raw_jid"):
        value = str(evolution.get(key) or "").strip()
        if value and "@" in value:
            aliases.add(value)
    return aliases


def resolve_evolution_shadow(
    *,
    current_key: str,
    incoming_source_meta: dict[str, Any] | None,
    conversations: dict[str, dict[str, Any]],
    persisted_matches: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Sugere correspondência por aliases sem modificar as conversas."""
    incoming_aliases = _identity_aliases(incoming_source_meta)
    if not incoming_aliases:
        return {"status": "insufficient_identity", "candidate_count": 0}

    candidate_aliases: dict[str, set[str]] = {}
    for key, conv in conversations.items():
        if key == current_key:
            continue
        shared = sorted(incoming_aliases & _identity_aliases(conv.get("source_meta")))
        if shared:
            candidate_aliases.setdefault(key, set()).update(shared)

    for key, shared in (persisted_matches or {}).items():
        if key != current_key:
            candidate_aliases.setdefault(key, set()).update(shared)

    candidates = [
        {"conversation_key": key, "shared_aliases": sorted(shared)}
        for key, shared in sorted(candidate_aliases.items())
    ]

    if not candidates:
        return {"status": "no_match", "candidate_count": 0}
    if len(candidates) == 1:
        return {
            "status": "match_suggested",
            "candidate_count": 1,
            "suggested_key": candidates[0]["conversation_key"],
            "shared_alias_count": len(candidates[0]["shared_aliases"]),
        }
    return {"status": "conflict", "candidate_count": len(candidates), "suggested_key": ""}
