from unittest.mock import MagicMock, patch

import pytest

from services import identity_alias_pg_service as service


def _source(*aliases: str, mapped: bool = False) -> dict:
    return {
        "provider": "evolution",
        "channel": "evolution_go",
        "evolution_identity": {
            "jid_aliases": list(aliases),
            "mapping_observed": mapped,
        },
    }


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setenv("IDENTITY_ALIAS_PERSIST_ENABLED", "true")
    service._schema_ready = False
    service._replace_cache([])
    yield
    service._schema_ready = False
    service._replace_cache([])


def _mock_connection(rows=None):
    cur = MagicMock()
    cur.fetchall.return_value = list(rows or [])
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_extract_aliases_preserves_typed_pn_and_lid_only():
    aliases = service.extract_evolution_aliases(_source(
        "5511999990001@s.whatsapp.net",
        "123:7@lid",
        "ignored@g.us",
    ))
    assert aliases == [
        ("pn_jid", "5511999990001@s.whatsapp.net"),
        ("lid_jid", "123@lid"),
    ]


def test_record_pair_upserts_both_aliases_and_updates_cache():
    conn, cur = _mock_connection()
    with patch.object(service, "ensure_schema", return_value=(True, "")), \
         patch.object(service, "_connect", return_value=conn):
        ok, count = service.record_evolution_observation(
            "inst-a",
            "5511999990001",
            _source("5511999990001@s.whatsapp.net", "123@lid", mapped=True),
        )

    assert (ok, count) == (True, 2)
    assert cur.execute.call_count == 2
    assert all(call.args[0].strip().startswith("INSERT") for call in cur.execute.call_args_list)
    matches = service.find_matches("inst-a", "123@lid", _source("123@lid"))
    assert matches == {"5511999990001": ["123@lid"]}


def test_cache_is_strictly_isolated_by_instance():
    service._replace_cache([
        ("inst-a", "phone-a", "lid_jid", "123@lid"),
        ("inst-b", "phone-b", "lid_jid", "123@lid"),
    ])
    incoming = _source("123@lid")
    assert service.find_matches("inst-a", "new", incoming) == {"phone-a": ["123@lid"]}
    assert service.find_matches("inst-b", "new", incoming) == {"phone-b": ["123@lid"]}


def test_load_cache_restores_aliases_after_restart():
    conn, _ = _mock_connection([
        ("inst-a", "5511999990001", "pn_jid", "5511999990001@s.whatsapp.net"),
        ("inst-a", "5511999990001", "lid_jid", "123@lid"),
    ])
    with patch.object(service, "ensure_schema", return_value=(True, "")), \
         patch.object(service, "_connect", return_value=conn):
        ok, count = service.load_cache()

    assert (ok, count) == (True, 2)
    assert service.find_matches("inst-a", "123@lid", _source("123@lid")) == {
        "5511999990001": ["123@lid"]
    }


def test_conflicting_alias_returns_all_candidates_without_merge():
    service._replace_cache([
        ("inst-a", "phone-a", "lid_jid", "123@lid"),
        ("inst-a", "phone-b", "lid_jid", "123@lid"),
    ])
    assert service.find_matches("inst-a", "123@lid", _source("123@lid")) == {
        "phone-a": ["123@lid"],
        "phone-b": ["123@lid"],
    }


def test_disabled_flag_prevents_database_write(monkeypatch):
    monkeypatch.delenv("IDENTITY_ALIAS_PERSIST_ENABLED", raising=False)
    with patch.object(service, "_connect") as connect:
        ok, detail = service.record_evolution_observation(
            "inst-a", "123@lid", _source("123@lid"),
        )
    assert (ok, detail) == (False, "identity_alias_persist_disabled")
    connect.assert_not_called()


def test_extract_meta_aliases_supports_bsuid_parent_username_and_phone():
    aliases = service.extract_meta_aliases({
        "wa_id": "+55 (11) 99999-0001",
        "user_id": "BR.ABC123",
        "parent_user_id": "PARENT.456",
        "profile": {"username": "@Lead.Exemplo"},
    })
    assert aliases == [
        ("phone", "5511999990001"),
        ("bsuid", "BR.ABC123"),
        ("parent_bsuid", "PARENT.456"),
        ("username", "lead.exemplo"),
    ]


def test_meta_username_history_is_additive_and_case_insensitive():
    aliases = service.extract_meta_aliases({
        "user_id": "BR.STABLE",
        "username": "@Novo_Nome",
        "previous_username": "Nome.Antigo",
    })
    assert aliases == [
        ("bsuid", "BR.STABLE"),
        ("username", "novo_nome"),
        ("username", "nome.antigo"),
    ]


def test_record_meta_observation_uses_same_additive_table():
    conn, cur = _mock_connection()
    with patch.object(service, "ensure_schema", return_value=(True, "")), \
         patch.object(service, "_connect", return_value=conn):
        ok, count = service.record_meta_observation(
            "inst-meta",
            "BR.ABC123",
            {
                "wa_id": "5511999990001",
                "user_id": "BR.ABC123",
                "username": "lead_teste",
                "waba_id": "WABA-1",
            },
        )
    assert (ok, count) == (True, 3)
    assert cur.execute.call_count == 3
    assert service.find_matches(
        "inst-meta", "outro", {"user_id": "BR.ABC123"},
    ) == {}
