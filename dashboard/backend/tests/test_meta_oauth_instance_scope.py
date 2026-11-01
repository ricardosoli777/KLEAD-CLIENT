"""Regressões do isolamento da conexão Meta OAuth por instância."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))


def _connection():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_upsert_nova_conexao_cria_vinculo_pendente_da_instancia():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.return_value = None
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        ok, connection_id = svc.upsert_connection(
            "inst-a", "owner@example.com", "meta-user", "encrypted", None, "scope-a"
        )

    assert ok is True
    assert connection_id
    sql_calls = [call.args[0] for call in cur.execute.call_args_list]
    assert any("WHERE ima.instance_id = %s" in sql for sql in sql_calls)
    assert any("INSERT INTO meta_connections" in sql for sql in sql_calls)
    assert any("INSERT INTO instance_meta_assets" in sql and "'pending'" in sql for sql in sql_calls)
    assert conn.commit.called


def test_reconectar_instancia_atualiza_somente_conexao_ja_vinculada():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.return_value = ("conn-inst-a", 1)
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        ok, connection_id = svc.upsert_connection(
            "inst-a", "owner@example.com", "meta-user", "encrypted-new", None, "scope-a"
        )

    assert ok is True
    assert connection_id == "conn-inst-a"
    sql_calls = [call.args[0] for call in cur.execute.call_args_list]
    assert any("UPDATE meta_connections" in sql for sql in sql_calls)
    assert not any("INSERT INTO meta_connections" in sql for sql in sql_calls)


def test_reconectar_conexao_legada_compartilhada_cria_conexao_exclusiva():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.return_value = ("conn-legada", 2)
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        ok, connection_id = svc.upsert_connection(
            "inst-a", "owner@example.com", "meta-user", "encrypted-new", None, "scope-a"
        )

    assert ok is True
    assert connection_id != "conn-legada"
    sql_calls = [call.args[0] for call in cur.execute.call_args_list]
    assert any("INSERT INTO meta_connections" in sql for sql in sql_calls)
    assert not any("UPDATE meta_connections" in sql for sql in sql_calls)


def test_busca_conexao_usa_instance_id_e_retorna_dono_da_chave():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.return_value = (
        "conn-a", "owner@example.com", "meta-user", "encrypted", None,
        "scope-a", "active", None,
    )
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        result = svc.get_connection_for_instance("inst-a")

    assert result["id"] == "conn-a"
    assert result["user_email"] == "owner@example.com"
    sql, params = cur.execute.call_args.args
    assert "WHERE ima.instance_id = %s" in sql
    assert params == ("inst-a",)


def test_instancias_do_mesmo_usuario_nao_compartilham_lookup():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.side_effect = [
        ("conn-a", "owner@example.com", "meta-a", "enc-a", None, "", "active", None),
        ("conn-b", "owner@example.com", "meta-b", "enc-b", None, "", "active", None),
    ]
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        first = svc.get_connection_for_instance("inst-a")
        second = svc.get_connection_for_instance("inst-b")

    assert first["id"] == "conn-a"
    assert second["id"] == "conn-b"
    assert [call.args[1] for call in cur.execute.call_args_list] == [("inst-a",), ("inst-b",)]


def test_conexao_legada_sem_vinculo_e_reivindicada_uma_unica_vez():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.return_value = ("conn-legada",)
    cur.rowcount = 1
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        claimed = svc.claim_unlinked_connection("inst-a", "owner@example.com")

    assert claimed is True
    first_sql, first_params = cur.execute.call_args_list[0].args
    assert "NOT EXISTS" in first_sql
    assert first_params == ("owner@example.com",)
    link_sql, link_params = cur.execute.call_args_list[1].args
    assert "ON CONFLICT (instance_id) DO NOTHING" in link_sql
    assert link_params[1:] == ("inst-a", "conn-legada")


def test_desconectar_legado_compartilhado_nao_desativa_outra_instancia():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.side_effect = [("conn-legada",), (1,)]
    with patch.object(svc, "ensure_schema", return_value=(True, "")), \
         patch.object(svc, "_connect", return_value=conn):
        ok, _ = svc.disconnect_instance("inst-a")

    assert ok is True
    sql_calls = [call.args[0] for call in cur.execute.call_args_list]
    assert any(sql.startswith("DELETE FROM instance_meta_assets") for sql in sql_calls)
    assert not any(sql.startswith("UPDATE meta_connections") for sql in sql_calls)


def test_assinatura_waba_usa_endpoint_subscribed_apps_sem_token_no_corpo():
    from services import meta_oauth_service as svc

    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True}
    with patch.object(svc.requests, "post", return_value=response) as post:
        result = svc.subscribe_whatsapp_business_account("waba-123", "oauth-secret")

    assert result["ok"] is True
    post.assert_called_once_with(
        f"{svc.META_BASE_URL}/waba-123/subscribed_apps",
        params={"access_token": "oauth-secret"},
        data={},
        timeout=15,
    )


def test_oauth_pede_permissoes_de_webhook_e_leitura_de_leads():
    from services import meta_oauth_service as svc

    assert "pages_manage_metadata" in svc.REQUIRED_SCOPES
    assert "leads_retrieval" in svc.REQUIRED_SCOPES


def test_page_access_token_fica_somente_no_backend():
    from services import meta_oauth_service as svc

    response = MagicMock(status_code=200)
    response.json.return_value = {"id": "page-1", "access_token": "page-secret"}
    with patch.object(svc.requests, "get", return_value=response):
        result = svc.get_page_access_token("page-1", "user-secret")

    assert result == {"ok": True, "access_token": "page-secret"}


def test_salvar_ativos_oauth_assina_leadgen_e_persiste_page_token():
    from core.state import instances, instances_lock
    from routers import meta_oauth as router

    previous = dict(instances)
    saved = []
    state = SimpleNamespace(
        decrypt_field_for_user=lambda *_args: "user-token",
        subscribe_meta_page_leadgen=lambda token, page: {
            "ok": token == "page-token" and page == "page-1"
        },
        save_instance_config=lambda iid: saved.append(iid),
        normalize_meta_ad_account_id=lambda value: value.removeprefix("act_"),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    payload = {
        "page_id": "page-1",
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "pixel_id": "pixel-1",
    }
    with instances_lock:
        instances.clear()
        instances["meta-1"] = {"provider": "meta"}
    try:
        with patch.object(router.auth_svc, "require_instance_access", return_value={"email": "owner@test.com"}), \
             patch.object(router, "_connection_for_instance", return_value={
                 "id": "conn-1", "user_email": "owner@test.com", "access_token_encrypted": "encrypted"
             }), \
             patch.object(router.meta_oauth_svc, "subscribe_whatsapp_business_account", return_value={"ok": True}), \
             patch.object(router.meta_oauth_svc, "get_page_access_token", return_value={
                 "ok": True, "access_token": "page-token"
             }), \
             patch.object(router.meta_oauth_svc, "list_lead_forms", return_value={"ok": True, "data": []}), \
             patch.object(router.meta_oauth_pg, "save_lead_routes", return_value=(True, "")), \
             patch.object(router.meta_oauth_pg, "upsert_instance_assets", return_value=(True, "")):
            result = router.meta_save_assets("meta-1", payload, request, _="key")

        assert result["lead_ads_subscribed"] is True
        assert instances["meta-1"]["meta_page_access_token"] == "page-token"
        assert instances["meta-1"]["meta_page_subscribed"] is True
        assert saved == ["meta-1"]
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_lead_ads_only_nao_exige_nem_assina_waba():
    from core.state import instances, instances_lock
    from routers import meta_oauth as router

    previous = dict(instances)
    saved_payload = {}
    state = SimpleNamespace(
        decrypt_field_for_user=lambda *_args: "user-token",
        subscribe_meta_page_leadgen=lambda *_args: {"ok": True},
        save_instance_config=lambda _iid: None,
        normalize_meta_ad_account_id=lambda value: value,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    payload = {
        "page_id": "page-1", "page_name": "Página",
        "lead_ads_enabled": True, "lead_ads_default": True,
        "whatsapp_enabled": False, "lead_forms": [],
        "waba_id": "stale-waba", "phone_number_id": "stale-phone",
    }
    with instances_lock:
        instances.clear()
        instances["lead-only"] = {"provider": "meta", "phone_number_id": "stale-phone"}
    try:
        def capture_assets(_iid, _conn, assets):
            saved_payload.update(assets)
            return True, ""

        with patch.object(router.auth_svc, "require_instance_access", return_value={"email": "owner@test.com"}), \
             patch.object(router, "_connection_for_instance", return_value={
                 "id": "conn-1", "user_email": "owner@test.com", "access_token_encrypted": "encrypted"
             }), \
             patch.object(router.meta_oauth_svc, "subscribe_whatsapp_business_account") as subscribe_waba, \
             patch.object(router.meta_oauth_svc, "get_page_access_token", return_value={"ok": True, "access_token": "page-token"}), \
             patch.object(router.meta_oauth_svc, "list_lead_forms", return_value={"ok": True, "data": []}), \
             patch.object(router.meta_oauth_pg, "save_lead_routes", return_value=(True, "")), \
             patch.object(router.meta_oauth_pg, "upsert_instance_assets", side_effect=capture_assets):
            result = router.meta_save_assets("lead-only", payload, request, _="key")

        assert result["whatsapp_enabled"] is False
        assert result["lead_ads_subscribed"] is True
        assert saved_payload["waba_id"] == ""
        assert saved_payload["phone_number_id"] == ""
        assert instances["lead-only"]["phone_number_id"] == ""
        assert instances["lead-only"]["meta_waba_subscribed"] is False
        subscribe_waba.assert_not_called()
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_resolve_lead_instance_prioriza_formulario_e_faz_fallback_pagina():
    from services import meta_oauth_pg_service as svc

    conn, cur = _connection()
    cur.fetchone.side_effect = [("form-owner",), None, ("page-owner",)]
    with patch.object(svc, "ensure_schema", return_value=(True, "")), patch.object(svc, "_connect", return_value=conn):
        assert svc.resolve_lead_instance("page-1", "form-1") == "form-owner"
        assert svc.resolve_lead_instance("page-1", "unknown-form") == "page-owner"


def test_listagem_de_formularios_usa_page_access_token():
    from routers import meta_oauth as router

    state = SimpleNamespace(decrypt_field_for_user=lambda *_args: "user-token")
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    with patch.object(router.auth_svc, "require_instance_access", return_value={"email": "owner@test.com"}), \
         patch.object(router, "_connection_for_instance", return_value={
             "user_email": "owner@test.com", "access_token_encrypted": "encrypted"
         }), \
         patch.object(router.meta_oauth_svc, "list_businesses", return_value={"ok": True, "data": []}), \
         patch.object(router.meta_oauth_svc, "list_pages", return_value={"ok": True, "data": [{"id": "page-1", "name": "Página"}]}), \
         patch.object(router.meta_oauth_svc, "get_instagram_for_page", return_value={"ok": True, "raw": {}}), \
         patch.object(router.meta_oauth_svc, "get_page_access_token", return_value={"ok": True, "access_token": "page-token"}), \
         patch.object(router.meta_oauth_svc, "list_lead_forms", return_value={
             "ok": True, "data": [{"id": "form-1", "name": "Formulário"}]
         }) as list_forms, \
         patch.object(router.meta_oauth_pg, "get_lead_routes", return_value={"page_instance_id": "", "forms": {}}):
        result = router.meta_list_assets("meta-1", request, _="key")

    list_forms.assert_called_once_with("page-1", "page-token")
    assert result["lead_forms"][0]["name"] == "Formulário"
    assert result["pages"][0]["lead_forms_error"] == ""
