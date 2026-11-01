import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_meta_webhook_uses_bsuid_as_primary_identity():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "contacts": [{
                        "profile": {"name": "Lead BSUID"},
                        "user_id": "BR.13491208655302741918",
                        "username": "@leadbsuid",
                    }],
                    "messages": [{
                        "from": "5511999990001",
                        "from_user_id": "BR.13491208655302741918",
                        "type": "text",
                        "text": {"body": "Oi"},
                    }],
                },
            }],
        }],
    }

    with patch.dict(
        "main.instances",
        {
            "meta-1": {
                "provider": "meta",
                "meta_app_secret": "secret",
                "phone_number_id": "123",
                "meta_token": "TOKEN",
            }
        },
        clear=True,
    ), patch("routers.webhook._verify_meta_signature", return_value=True), \
         patch.object(m.app.state, "record_webhook_event") as mock_event, \
         patch.object(m.app.state, "save_conversations"), \
         patch.object(m.app.state, "handle_inbound") as mock_handle:
        response = client.post("/webhook/meta-1", json=payload, headers={"x-hub-signature-256": "sha256=test"})

    assert response.status_code == 200
    mock_event.assert_called_once()
    # bsuid é usado como conv_key — handle_inbound recebe bsuid como segundo argumento
    assert mock_handle.call_args.args[1] == "BR.13491208655302741918"
    assert mock_handle.call_args.args[0] == "meta-1"


def test_meta_webhook_without_phone_uses_typed_bsuid_and_marks_unavailable():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)
    bsuid = "BR.ONLY123"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA-1",
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "PHONE-ID"},
                    "contacts": [{
                        "profile": {"name": "Sem telefone", "username": "@privado"},
                        "user_id": bsuid,
                    }],
                    "messages": [{
                        "from": bsuid,
                        "from_user_id": bsuid,
                        "type": "text",
                        "text": {"body": "Oi"},
                    }],
                },
            }],
        }],
    }
    with patch.dict("main.instances", {
        "meta-1": {"provider": "meta", "meta_app_secret": "secret"},
    }, clear=True), patch.dict("main.conversations", {}, clear=True), \
         patch("routers.webhook._verify_meta_signature", return_value=True), \
         patch.object(m.app.state, "record_webhook_event"), \
         patch.object(m.app.state, "save_conversations"), \
         patch.object(m.app.state, "persist_meta_identity_aliases"), \
         patch.object(m.app.state, "handle_inbound") as handle:
        response = client.post(
            "/webhook/meta-1", json=payload,
            headers={"x-hub-signature-256": "sha256=test"},
        )
    assert response.status_code == 200
    assert handle.call_args.args[1] == f"bsuid:{bsuid}"
    source_meta = handle.call_args.args[5]
    assert source_meta["phone_available"] is False
    assert source_meta["outbound_available"] is False
    assert "wa_id" not in source_meta


def test_username_only_key_is_scoped_by_waba():
    from routers.webhook import _meta_conversation_key

    first = _meta_conversation_key(
        "meta-1", phone="", bsuid="", parent_bsuid="",
        username="@mesmo.nome", waba_id="WABA-A",
    )
    second = _meta_conversation_key(
        "meta-1", phone="", bsuid="", parent_bsuid="",
        username="@mesmo.nome", waba_id="WABA-B",
    )
    assert first == "username:WABA-A:mesmo.nome"
    assert second == "username:WABA-B:mesmo.nome"
    assert first != second


def test_phone_less_lead_is_qualified_but_reply_delivery_is_blocked():
    import main as m

    key = "bsuid:BR.ONLY123"
    conv = {
        "name": "Sem telefone",
        "messages": [{"role": "user", "content": "Tenho interesse"}],
        "turn_count": 0,
        "source_meta": {"phone_available": False, "outbound_available": False},
    }
    inst = {"inbound_ai_enabled": True, "max_ai_turns": 8}
    result = {
        "message": "Posso ajudar.",
        "action": "reply",
        "score": 55,
        "score_reason": "interesse",
        "injection_flagged": False,
    }
    with patch.dict("main.conversations", {"meta-1": {key: conv}}, clear=True), \
         patch("main.auth_svc.instance_plan_allows", return_value=True), \
         patch("main.qualify_lead", return_value=result), \
         patch("main.record_pattern"), \
         patch("main._send_reply") as send, \
         patch("main._save_conversation"):
        m._ai_respond_to_conv("meta-1", key, inst, conv, "Tenho interesse")
    send.assert_not_called()
    assert conv["score"] == 55
    assert conv["messages"][-1]["delivery_status"] == "blocked_no_supported_destination"


def test_crm_send_message_uses_recipient_for_bsuid_lead():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)
    api_key = os.environ["DASHBOARD_SECRET"]

    lead = {
        "id": "lead-1",
        "nome": "Lead BSUID",
        "telefone": "",
        "bsuid": "BR.13491208655302741918",
        "instancia_id": "meta-1",
    }

    with patch("main.crm_svc.get_lead", return_value=lead), \
         patch("main.crm_svc.add_note"), \
         patch.dict(
             "main.instances",
             {
                 "meta-1": {
                     "provider": "meta",
                     "phone_number_id": "123",
                     "meta_token": "TOKEN",
                 }
             },
             clear=True,
    ), patch("routers.crm.send_outbound_text", return_value=(True, "", "meta")) as mock_send, \
       patch("services.auth_service.verify_token", return_value={
           "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
       }):
        response = client.post(
            "/crm/leads/lead-1/message",
            headers={"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"},
            json={"message": "Olá"},
        )

    assert response.status_code == 200
    args = mock_send.call_args.args
    assert args[1] == "meta-1"
    assert args[2] == "BR.13491208655302741918"


def test_meta_webhook_stores_source_meta_for_ctwa_conversation():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "contacts": [{
                        "profile": {"name": "Lead Ads"},
                        "wa_id": "5511999990001",
                        "user_id": "BR.13491208655302741918",
                        "username": "@leadads",
                    }],
                    "messages": [{
                        "id": "wamid.123",
                        "from": "5511999990001",
                        "from_user_id": "BR.13491208655302741918",
                        "type": "text",
                        "text": {
                            "body": (
                                "Olá! Preenchi seu formulário e gostaria de saber mais sobre sua empresa.\n\n"
                                "você_está_buscando_apartamento_alto_padrão_em_são_caetano?: SIM\n"
                                "o_que_mais_falta_no_seu_apartamento_atual?: LOCALIZAÇÃO E PRIVACIDADE\n"
                                "email: lead@example.com\n"
                                "nome_completo: Michelle Nascimento\n"
                                "telefone: +5511999990001"
                            )
                        },
                        "referral": {
                            "source_url": "https://facebook.com/ad",
                            "source_id": "12001234",
                            "headline": "Campanha CTWA",
                            "ctwa_clid": "CTWA123",
                        },
                    }],
                },
            }],
        }],
    }

    with patch.dict(
        "main.instances",
        {
            "meta-1": {
                "provider": "meta",
                "meta_app_secret": "secret",
                "phone_number_id": "123",
                "meta_token": "TOKEN",
            }
        },
        clear=True,
    ), patch.dict("main.conversations", {}, clear=True), \
         patch("routers.webhook._verify_meta_signature", return_value=True), \
         patch.object(m.app.state, "record_webhook_event"), \
         patch.object(m.app.state, "save_conversations"), \
         patch.object(m.app.state, "handle_inbound") as mock_handle:
        response = client.post("/webhook/meta-1", json=payload, headers={"x-hub-signature-256": "sha256=test"})

        assert response.status_code == 200
        # Conversa é criada/atualizada diretamente (sem upsert_lead automático)
        conv = m.conversations["meta-1"]["BR.13491208655302741918"]
        assert conv["lead_origem"] == "meta_ads"
        assert conv["lead_campanha"] == "Campanha CTWA"
        assert conv["lead_email"] == "lead@example.com"
        assert conv["source_meta"]["referral"]["source_id"] == "12001234"
        assert mock_handle.call_args.args[3] == "Olá! Preenchi seu formulário e gostaria de saber mais sobre sua empresa."


def test_send_to_crm_preserves_meta_source_and_triggers_capi():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)
    api_key = os.environ["DASHBOARD_SECRET"]
    conv_key = "BR.13491208655302741918"
    source_meta = {
        "provider": "meta",
        "origem": "meta_ads",
        "channel": "whatsapp_cloud_api",
        "message_id": "wamid.123",
        "evolution_identity": {
            "pn_jid": "5511999990077@s.whatsapp.net",
            "lid_jid": "60460305014809@lid",
            "jid_aliases": [
                "5511999990077@s.whatsapp.net",
                "60460305014809@lid",
            ],
            "mapping_observed": True,
        },
        "referral": {
            "source_id": "12001234",
            "headline": "Campanha CTWA",
            "ctwa_clid": "CTWA123",
        },
    }

    with patch.dict(
        "main.instances",
        {
            "meta-1": {
                "provider": "meta",
                "meta_capi_enabled": True,
                "meta_capi_event_source_id": "123",
                "meta_capi_access_token": "TOKEN",
            }
        },
        clear=True,
    ), patch.dict(
        "main.conversations",
        {
            "meta-1": {
                conv_key: {
                    "name": "Lead Ads",
                    "messages": [{"role": "user", "content": "Tenho interesse"}],
                    "score": 42,
                    "score_reason": "bom fit",
                    "lead_origem": "meta_ads",
                    "lead_email": "lead@example.com",
                    "lead_campanha": "Campanha CTWA",
                    "source_meta": source_meta,
                }
            }
        },
        clear=True,
    ), patch("main.crm_svc.get_leads", return_value=[]), \
         patch("main.crm_svc.upsert_lead") as mock_upsert, \
         patch.object(m.app.state, "maybe_queue_meta_capi_event") as mock_queue, \
         patch("services.auth_service.verify_token", return_value={
             "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
         }):
        mock_upsert.return_value = {
            "id": "lead-1",
            "origem": "meta_ads",
            "source_meta": source_meta,
        }
        response = client.post(
            f"/conversations/meta-1/{conv_key}/send-to-crm",
            headers={"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"},
        )

    assert response.status_code == 200
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["origem"] == "meta_ads"
    assert kwargs["email"] == "lead@example.com"
    assert kwargs["campanha"] == "Campanha CTWA"
    assert kwargs["source_meta"]["referral"]["ctwa_clid"] == "CTWA123"
    assert kwargs["source_meta"]["evolution_identity"]["lid_jid"] == "60460305014809@lid"
    mock_queue.assert_called_once()
