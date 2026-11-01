from copy import deepcopy

from services.identity_resolver_service import resolve_evolution_shadow


def _conv(*aliases: str) -> dict:
    return {"source_meta": {"evolution_identity": {"jid_aliases": list(aliases)}}}


def test_shadow_suggests_single_alias_match_without_mutating_store():
    conversations = {
        "5511999990001": _conv("5511999990001@s.whatsapp.net", "123@lid"),
        "123@lid": _conv("123@lid"),
    }
    before = deepcopy(conversations)
    result = resolve_evolution_shadow(
        current_key="123@lid",
        incoming_source_meta=_conv("123@lid", "5511999990001@s.whatsapp.net")["source_meta"],
        conversations=conversations,
    )
    assert result == {
        "status": "match_suggested",
        "candidate_count": 1,
        "suggested_key": "5511999990001",
        "shared_alias_count": 2,
    }
    assert conversations == before


def test_shadow_reports_conflict_instead_of_choosing_contact():
    conversations = {
        "old-phone": _conv("123@lid"),
        "other-phone": _conv("123@lid"),
        "123@lid": _conv("123@lid"),
    }
    result = resolve_evolution_shadow(
        current_key="123@lid",
        incoming_source_meta=_conv("123@lid")["source_meta"],
        conversations=conversations,
    )
    assert result == {"status": "conflict", "candidate_count": 2, "suggested_key": ""}


def test_shadow_does_not_match_without_shared_alias():
    result = resolve_evolution_shadow(
        current_key="123@lid",
        incoming_source_meta=_conv("123@lid")["source_meta"],
        conversations={"5511999990001": _conv("5511999990001@s.whatsapp.net")},
    )
    assert result == {"status": "no_match", "candidate_count": 0}


def test_shadow_requires_evolution_identity_metadata():
    result = resolve_evolution_shadow(
        current_key="123@lid",
        incoming_source_meta={},
        conversations={},
    )
    assert result == {"status": "insufficient_identity", "candidate_count": 0}


def test_shadow_uses_persisted_match_after_restart_without_mutation():
    conversations = {"123@lid": _conv("123@lid")}
    before = deepcopy(conversations)
    result = resolve_evolution_shadow(
        current_key="123@lid",
        incoming_source_meta=_conv("123@lid")['source_meta'],
        conversations=conversations,
        persisted_matches={"5511999990001": ["123@lid"]},
    )
    assert result == {
        "status": "match_suggested",
        "candidate_count": 1,
        "suggested_key": "5511999990001",
        "shared_alias_count": 1,
    }
    assert conversations == before


def test_shadow_persisted_conflict_never_selects_a_contact():
    result = resolve_evolution_shadow(
        current_key="123@lid",
        incoming_source_meta=_conv("123@lid")['source_meta'],
        conversations={},
        persisted_matches={"phone-a": ["123@lid"], "phone-b": ["123@lid"]},
    )
    assert result == {"status": "conflict", "candidate_count": 2, "suggested_key": ""}
