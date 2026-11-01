"""
Testes para os endpoints de webhook Meta (Lead Ads e Forms).
Cobre: verificação GET, recebimento POST, parsing de field_data,
       salvamento no CRM, token inválido, payloads incompletos.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")

sys.path.insert(0, str(Path(__file__).parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_lead_payload(fields: list, leadgen_id="123", page_id="PAGE1", form_id="FORM1"):
    return {
        "object": "page",
        "entry": [{
            "id": page_id,
            "changes": [{
                "field": "leadgen",
                "value": {
                    "leadgen_id": leadgen_id,
                    "page_id":    page_id,
                    "form_id":    form_id,
                    "field_data": fields,
                }
            }]
        }]
    }


FULL_FIELDS = [
    {"name": "full_name",     "values": ["João Silva"]},
    {"name": "email",         "values": ["joao@email.com"]},
    {"name": "phone_number",  "values": ["5511999990001"]},
    {"name": "qual_produto",  "values": ["Plano Premium"]},
    {"name": "quanto_fatura", "values": ["10k a 50k"]},
]


# ═══════════════════════════════════════════════════════════════════════════════
# _extract_meta_field_data
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractFieldData:
    def test_extracts_all_fields(self):
        from main import _extract_meta_field_data
        fields = [
            {"name": "full_name",    "values": ["João"]},
            {"name": "phone_number", "values": ["5511999990001"]},
            {"name": "email",        "values": ["joao@test.com"]},
        ]
        result = _extract_meta_field_data(fields)
        assert result["full_name"] == "João"
        assert result["phone_number"] == "5511999990001"
        assert result["email"] == "joao@test.com"

    def test_empty_values_returns_empty_string(self):
        from main import _extract_meta_field_data
        fields = [{"name": "email", "values": []}]
        result = _extract_meta_field_data(fields)
        assert result["email"] == ""

    def test_empty_list_returns_empty_dict(self):
        from main import _extract_meta_field_data
        assert _extract_meta_field_data([]) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_meta_lead_entry
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseMetaLeadEntry:
    def test_parses_full_entry(self):
        from main import _parse_meta_lead_entry
        entry = make_lead_payload(FULL_FIELDS)["entry"][0]
        result = _parse_meta_lead_entry(entry, "meta-leads")
        assert result["nome"] == "João Silva"
        assert result["telefone"] == "5511999990001"
        assert result["email"] == "joao@email.com"
        assert "Plano Premium" in result["campanha"]
        assert "10k a 50k" in result["campanha"]

    def test_parses_payload_with_nome_completo_alias(self):
        from main import _parse_meta_lead_entry
        fields = [
            {"name": "você_está_buscando_apartamento_alto_padrão_em_são_caetano?", "values": ["SIM"]},
            {"name": "o_que_mais_falta_no_seu_apartamento_atual?", "values": ["PLANTA MODERNA E CONFORTO"]},
            {"name": "hoje,_você_tem_mais_interesse_em", "values": ["ESTOU ABERTO AS DUAS OPÇÕES"]},
            {"name": "email", "values": ["sandra_mansini@hotmail.com"]},
            {"name": "nome_completo", "values": ["Sandra Mansini"]},
            {"name": "telefone", "values": ["+5511973071598"]},
        ]
        entry = make_lead_payload(fields)["entry"][0]
        result = _parse_meta_lead_entry(entry, "meta-forms")
        assert result["nome"] == "Sandra Mansini"
        assert result["telefone"] == "5511973071598"
        assert result["email"] == "sandra_mansini@hotmail.com"
        assert "SIM" in result["campanha"]
        assert "PLANTA MODERNA E CONFORTO" in result["campanha"]
        assert "ESTOU ABERTO AS DUAS OPÇÕES" in result["campanha"]

    def test_combines_first_and_last_name(self):
        from main import _parse_meta_lead_entry
        fields = [
            {"name": "first_name", "values": ["João"]},
            {"name": "last_name",  "values": ["Silva"]},
            {"name": "phone_number", "values": ["5511999990001"]},
        ]
        entry = make_lead_payload(fields)["entry"][0]
        result = _parse_meta_lead_entry(entry, "meta-forms")
        assert result["nome"] == "João Silva"

    def test_normalizes_phone_number(self):
        from main import _parse_meta_lead_entry
        fields = [
            {"name": "full_name",    "values": ["Ana"]},
            {"name": "phone_number", "values": ["+55 (11) 99999-0001"]},
        ]
        entry = make_lead_payload(fields)["entry"][0]
        result = _parse_meta_lead_entry(entry, "meta-leads")
        assert result["telefone"] == "5511999990001"

    def test_returns_none_when_no_field_data(self):
        from main import _parse_meta_lead_entry
        entry = {"id": "PAGE1", "changes": [{"field": "leadgen", "value": {}}]}
        result = _parse_meta_lead_entry(entry, "meta-leads")
        assert result is None

    def test_uses_fetched_value_when_webhook_only_has_ids(self):
        from main import _parse_meta_lead_entry
        entry = {
            "id": "PAGE1",
            "changes": [{
                "field": "leadgen",
                "value": {"leadgen_id": "LG123", "page_id": "PAGE1", "form_id": "FORM1"},
            }],
        }
        fetched_value = {
            "id": "LG123",
            "page_id": "PAGE1",
            "form_id": "FORM1",
            "field_data": FULL_FIELDS,
            "created_time": "2026-05-20T12:00:00+0000",
        }
        result = _parse_meta_lead_entry(entry, "meta-leads", fetched_value=fetched_value)
        assert result["telefone"] == "5511999990001"
        assert result["source_meta"]["leadgen_id"] == "LG123"
        assert result["source_meta"]["page_id"] == "PAGE1"

    def test_extras_become_campanha(self):
        from main import _parse_meta_lead_entry
        fields = [
            {"name": "full_name",    "values": ["Ana"]},
            {"name": "phone_number", "values": ["5511999990001"]},
            {"name": "pergunta_1",   "values": ["Resposta A"]},
        ]
        entry = make_lead_payload(fields)["entry"][0]
        result = _parse_meta_lead_entry(entry, "meta-forms")
        assert "pergunta_1" in result["campanha"]
        assert "Resposta A" in result["campanha"]


# ═══════════════════════════════════════════════════════════════════════════════
# GET — verificação do webhook
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookVerification:
    def _client(self):
        from fastapi.testclient import TestClient
        import main as m
        return TestClient(m.app), m.WEBHOOK_SECRET

    def test_meta_leads_verify_success(self):
        client, secret = self._client()
        r = client.get("/webhook/meta-leads", params={
            "hub.mode": "subscribe",
            "hub.verify_token": secret,
            "hub.challenge": "CHALLENGE123",
        })
        assert r.status_code == 200
        assert r.text == "CHALLENGE123"

    def test_meta_forms_verify_success(self):
        client, secret = self._client()
        r = client.get("/webhook/meta-forms", params={
            "hub.mode": "subscribe",
            "hub.verify_token": secret,
            "hub.challenge": "CHALLENGE456",
        })
        assert r.status_code == 200
        assert r.text == "CHALLENGE456"

    def test_wrong_token_returns_403(self):
        client, _ = self._client()
        r = client.get("/webhook/meta-leads", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "CHALLENGE",
        })
        assert r.status_code == 403

    def test_wrong_mode_returns_403(self):
        client, secret = self._client()
        r = client.get("/webhook/meta-forms", params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": secret,
            "hub.challenge": "CHALLENGE",
        })
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# POST — recebimento de leads
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookReceive:
    def _client(self):
        from fastapi.testclient import TestClient
        import main as m
        return TestClient(m.app), m.WEBHOOK_SECRET

    def test_meta_leads_saves_to_crm(self):
        client, secret = self._client()
        payload = make_lead_payload(FULL_FIELDS)

        with patch("main._record_webhook_event") as mock_event, \
             patch("main._record_meta_form_submission") as mock_submission, \
             patch("main._seed_meta_form_conversation"), \
             patch("main._crm_patch_leadgen_id"):
            r = client.post(
                f"/webhook/meta-leads?token={secret}",
                json=payload,
            )

        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["saved"] == 1
        mock_event.assert_called_once()
        mock_submission.assert_called_once()
        # verifica que os dados do lead foram parseados corretamente
        # assinatura: _record_meta_form_submission({}, origem, lead_data)
        assert mock_submission.call_args[0][1] == "meta-leads"
        lead_data = mock_submission.call_args[0][2]
        assert lead_data["telefone"] == "5511999990001"
        assert lead_data["nome"] == "João Silva"
        assert lead_data["email"] == "joao@email.com"

    def test_meta_forms_saves_to_crm(self):
        client, secret = self._client()
        payload = make_lead_payload(FULL_FIELDS)

        with patch("main._seed_meta_form_conversation"), \
             patch("main._record_webhook_event") as mock_event, \
             patch("main._record_meta_form_submission") as mock_submission, \
             patch("main._crm_patch_leadgen_id"), \
             patch.dict("main.instances", {"meta-1": {"provider": "meta", "meta_page_id": "PAGE1"}}, clear=True):
            r = client.post(
                f"/webhook/meta-forms?token={secret}",
                json=payload,
            )

        assert r.json()["ok"] is True
        mock_event.assert_called_once()
        mock_submission.assert_called_once()
        submission_owner, submission_origin, lead_data = mock_submission.call_args[0]
        assert submission_owner["instancia_id"] == "meta-1"
        assert submission_origin == "meta-forms"
        assert lead_data["leadgen_id"] == "123"
        assert lead_data["page_id"] == "PAGE1"
        assert lead_data["form_id"] == "FORM1"

    def test_meta_forms_seed_inbox_with_form_answers(self):
        client, secret = self._client()
        payload = make_lead_payload(FULL_FIELDS)

        with patch("main.crm_svc.upsert_lead") as mock_upsert, \
             patch("main._seed_meta_form_conversation") as mock_seed, \
             patch.dict("main.instances", {"meta-1": {"provider": "meta", "meta_page_id": "PAGE1"}}, clear=True):
            mock_upsert.return_value = {
                "id": "lead-2",
                "telefone": "5511999990001",
                "nome": "João Silva",
                "instancia_id": "meta-1",
            }
            r = client.post(
                f"/webhook/meta-forms?token={secret}",
                json=payload,
            )

        assert r.status_code == 200
        mock_seed.assert_called_once()
        assert mock_seed.call_args.args[0] == "meta-1"
        lead_data = mock_seed.call_args.args[2]
        assert "Plano Premium" in lead_data["extras_formatted"]

    def test_meta_lead_com_roleta_ativa_recebe_tag_sem_criar_lead_no_crm(self):
        """Roleta ativa: a conversa ganha source_meta.roleta_active=True (vira a
        tag ROLETA no InboxSDR), mas o lead NÃO é criado automaticamente no CRM
        — isso continua só acontecendo quando o atendente qualifica e envia
        manualmente (crm_svc.upsert_lead não deve ser chamado por este fluxo)."""
        client, secret = self._client()
        payload = make_lead_payload(FULL_FIELDS)

        with patch("main.crm_svc.upsert_lead") as mock_upsert, \
             patch("main._seed_meta_form_conversation") as mock_seed, \
             patch("main.assignment_svc.auto_assign_tenant_key_for_instance", return_value="dono@x.com"), \
             patch.dict("main.instances", {"meta-1": {"provider": "meta", "meta_page_id": "PAGE1"}}, clear=True):
            r = client.post(f"/webhook/meta-forms?token={secret}", json=payload)

        assert r.status_code == 200
        mock_seed.assert_called_once()
        lead_data = mock_seed.call_args.args[2]
        assert lead_data["source_meta"]["roleta_active"] is True
        mock_upsert.assert_not_called()

    def test_meta_lead_com_roleta_desativada_nao_recebe_tag(self):
        client, secret = self._client()
        payload = make_lead_payload(FULL_FIELDS)

        with patch("main.crm_svc.upsert_lead"), \
             patch("main._seed_meta_form_conversation") as mock_seed, \
             patch("main.assignment_svc.auto_assign_tenant_key_for_instance", return_value=""), \
             patch.dict("main.instances", {"meta-1": {"provider": "meta", "meta_page_id": "PAGE1"}}, clear=True):
            r = client.post(f"/webhook/meta-forms?token={secret}", json=payload)

        assert r.status_code == 200
        lead_data = mock_seed.call_args.args[2]
        assert "roleta_active" not in (lead_data.get("source_meta") or {})

    def test_build_meta_form_inbox_message_formats_summary(self):
        from main import _build_meta_form_inbox_message

        lead = {
            "nome": "João Silva",
            "telefone": "5511999990001",
            "email": "joao@email.com",
            "campanha": "qual_produto: Plano Premium",
        }
        lead_data = {
            "extras_formatted": "• Qual produto: Plano Premium\n• Quanto fatura: 10k a 50k"
        }

        message = _build_meta_form_inbox_message(lead, lead_data, "meta-forms")

        assert "Novo lead recebido via Meta Forms" in message
        assert "Nome: João Silva" in message
        assert "Telefone: 5511999990001" in message
        assert "Respostas do formulario:" in message
        assert "Plano Premium" in message
        assert "Campanha" not in message

    def test_cep_aparece_como_campo_proprio_sem_duplicar_nas_respostas(self):
        """cep deve virar 'Cep: X' junto de Nome/Telefone/Email — não deve
        aparecer de novo como bullet em 'Respostas do formulario'."""
        from main import _parse_meta_lead_entry, _build_meta_form_inbox_message

        entry = {
            "id": "PAGE1",
            "changes": [{
                "field": "leadgen",
                "value": {
                    "leadgen_id": "123",
                    "page_id": "PAGE1",
                    "form_id": "FORM1",
                    "field_data": [
                        {"name": "nome_completo", "values": ["Sérgio Joaquim Dos Santos"]},
                        {"name": "telefone", "values": ["+5511947383694"]},
                        {"name": "email", "values": ["sergio@example.com"]},
                        {"name": "cep", "values": ["09560105"]},
                        {"name": "qual_produto", "values": ["Plano Premium"]},
                    ],
                },
            }],
        }
        lead_data = _parse_meta_lead_entry(entry, "meta-leads")
        message = _build_meta_form_inbox_message(lead_data, lead_data, "meta-leads")

        assert "Cep: 09560105" in message
        assert "Campanha" not in message
        # cep não deve reaparecer como bullet nas respostas do formulário
        respostas = message.split("Respostas do formulario:")[1]
        assert "09560105" not in respostas
        assert "Plano Premium" in respostas

    def test_invalid_token_rejected(self):
        client, _ = self._client()
        with patch("main.crm_svc.upsert_lead") as mock_upsert:
            r = client.post(
                "/webhook/meta-leads?token=wrong",
                json=make_lead_payload(FULL_FIELDS),
            )
        assert r.status_code == 403
        mock_upsert.assert_not_called()

    def test_missing_token_rejected(self):
        client, _ = self._client()
        with patch("main.crm_svc.upsert_lead") as mock_upsert:
            r = client.post("/webhook/meta-leads", json=make_lead_payload(FULL_FIELDS))
        assert r.status_code == 403
        mock_upsert.assert_not_called()

    def test_instance_webhook_secret_accepted(self):
        """Token por instância (multi-tenant): webhook_secret de qualquer instância autentica Lead Ads."""
        client, _ = self._client()
        payload = make_lead_payload(FULL_FIELDS)
        inst = {"provider": "meta", "meta_page_id": "PAGE1", "webhook_secret": "inst-secret-xyz"}
        with patch("main._record_webhook_event"), \
             patch("main._record_meta_form_submission"), \
             patch("main._seed_meta_form_conversation"), \
             patch("main._crm_patch_leadgen_id"), \
             patch("main.crm_svc.upsert_lead") as mock_upsert, \
             patch.dict("main.instances", {"inst-1": inst}, clear=True):
            mock_upsert.return_value = {"id": "lead-9", "telefone": "5511999990001", "instancia_id": "inst-1"}
            r = client.post(
                "/webhook/meta-leads?token=inst-secret-xyz",
                json=payload,
            )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_lead_without_phone_not_saved(self):
        client, secret = self._client()
        fields_no_phone = [
            {"name": "full_name", "values": ["Sem Telefone"]},
            {"name": "email",     "values": ["sem@telefone.com"]},
        ]
        with patch("main.crm_svc.upsert_lead") as mock_upsert:
            r = client.post(
                f"/webhook/meta-leads?token={secret}",
                json=make_lead_payload(fields_no_phone),
            )
        assert r.json()["saved"] == 0
        mock_upsert.assert_not_called()

    def test_multiple_entries_saved(self):
        client, secret = self._client()
        payload = {
            "object": "page",
            "entry": [
                make_lead_payload(FULL_FIELDS, leadgen_id="1")["entry"][0],
                make_lead_payload([
                    {"name": "full_name",    "values": ["Maria"]},
                    {"name": "phone_number", "values": ["5511999990002"]},
                ], leadgen_id="2")["entry"][0],
            ]
        }
        with patch("main._record_webhook_event"), \
             patch("main._record_meta_form_submission") as mock_submission, \
             patch("main._seed_meta_form_conversation"), \
             patch("main._crm_patch_leadgen_id"):
            r = client.post(
                f"/webhook/meta-leads?token={secret}",
                json=payload,
            )
        assert r.json()["saved"] == 2
        assert mock_submission.call_count == 2

    def test_empty_entry_list_saves_nothing(self):
        client, secret = self._client()
        with patch("main.crm_svc.upsert_lead") as mock_upsert:
            r = client.post(
                f"/webhook/meta-leads?token={secret}",
                json={"object": "page", "entry": []},
            )
        assert r.json()["saved"] == 0
        mock_upsert.assert_not_called()

    def test_callback_global_processa_payload_de_pagina_assinado(self):
        client, _ = self._client()
        with patch.dict(os.environ, {"META_OAUTH_APP_SECRET": "oauth-secret", "META_APP_SECRET": ""}), \
             patch("routers.webhook._verify_meta_signature", return_value=True), \
             patch("main._record_webhook_event"), \
             patch("main._record_meta_form_submission"), \
             patch("main._seed_meta_form_conversation"), \
             patch("main._crm_patch_leadgen_id"), \
             patch.dict("main.instances", {"meta-1": {"provider": "meta", "meta_page_id": "PAGE1"}}, clear=True):
            r = client.post(
                "/webhook/meta-whatsapp",
                json=make_lead_payload(FULL_FIELDS),
                headers={"x-hub-signature-256": "sha256=test"},
            )

        assert r.status_code == 200
        assert r.json()["saved"] == 1

    def test_falls_back_to_single_meta_instance_when_page_id_does_not_match(self):
        client, secret = self._client()
        payload = make_lead_payload(FULL_FIELDS, page_id="UNKNOWN_PAGE")

        with patch.dict(
            "main.instances",
            {"meta-1": {"provider": "meta", "meta_page_id": "PAGE-DOES-NOT-MATCH"}},
            clear=True,
        ), patch("main._record_webhook_event"), \
             patch("main._record_meta_form_submission"), \
             patch("main._seed_meta_form_conversation") as mock_seed, \
             patch("main._crm_patch_leadgen_id"), \
             patch("main._handle_inbound") as mock_inbound:
            r = client.post(
                f"/webhook/meta-leads?token={secret}",
                json=payload,
            )

        assert r.status_code == 200
        assert r.json()["saved"] == 1
        # fallback: usa "meta-1" como instância mesmo sem page_id correspondente
        assert mock_seed.call_args.args[0] == "meta-1"
        mock_inbound.assert_not_called()

    def test_fetches_full_lead_when_webhook_has_only_ids(self):
        client, secret = self._client()
        payload = {
            "object": "page",
            "entry": [{
                "id": "PAGE1",
                "changes": [{
                    "field": "leadgen",
                    "value": {
                        "leadgen_id": "LG123",
                        "page_id": "PAGE1",
                        "form_id": "FORM1",
                    },
                }],
            }],
        }

        fetched_lead = {
            "id": "LG123",
            "page_id": "PAGE1",
            "form_id": "FORM1",
            "field_data": FULL_FIELDS,
            "created_time": "2026-05-20T12:00:00+0000",
        }

        with patch.dict(
            "main.instances",
            {"meta-1": {"provider": "meta", "meta_page_id": "PAGE1", "meta_token": "TOKEN123"}},
            clear=True,
        ), patch("main.requests.get") as mock_get, \
             patch("main._record_webhook_event"), \
             patch("main._record_meta_form_submission"), \
             patch("main._seed_meta_form_conversation") as mock_seed, \
             patch("main._crm_patch_leadgen_id"), \
             patch("main._handle_inbound"):
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = fetched_lead

            r = client.post(
                f"/webhook/meta-leads?token={secret}",
                json=payload,
            )

        assert r.status_code == 200
        assert r.json()["saved"] == 1
        mock_get.assert_called_once()
        # dados do lead buscado da API devem estar no seed da conversa
        lead_data = mock_seed.call_args.args[1]
        assert lead_data["telefone"] == "5511999990001"
        assert lead_data["source_meta"]["leadgen_id"] == "LG123"
        assert lead_data["source_meta"]["form_id"] == "FORM1"
        assert lead_data["source_meta"]["page_id"] == "PAGE1"


# ═══════════════════════════════════════════════════════════════════════════════
# GET /webhook/meta-urls
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetaUrls:
    def _client(self):
        from fastapi.testclient import TestClient
        import main as m
        return TestClient(m.app), os.environ["DASHBOARD_SECRET"]

    def test_returns_two_webhooks(self):
        client, api_key = self._client()
        with patch("routers.webhook._require_admin"), \
             patch("services.auth_service.require_superadmin"):
            r = client.get(
                "/webhook/meta-urls",
                headers={"X-API-Key": api_key},
            )
        assert r.status_code == 200
        data = r.json()
        assert "verify_token" in data
        assert len(data["webhooks"]) == 2
        tipos = [w["tipo"] for w in data["webhooks"]]
        assert any("Lead Ads" in t for t in tipos)
        assert any("Forms" in t or "Click" in t for t in tipos)

    def test_urls_contain_token(self):
        client, api_key = self._client()
        with patch("routers.webhook._require_admin"), \
             patch("services.auth_service.require_superadmin"):
            r = client.get("/webhook/meta-urls", headers={"X-API-Key": api_key})
        data = r.json()
        for wh in data["webhooks"]:
            assert "token=" in wh["url"]

    def test_global_token_requires_superadmin(self):
        """Sem instance_id o token retornado é o global — admin comum não pode ver."""
        from fastapi import HTTPException
        client, api_key = self._client()
        with patch("routers.webhook._require_admin"), \
             patch("services.auth_service.require_superadmin", side_effect=HTTPException(403, "restrito")):
            r = client.get("/webhook/meta-urls", headers={"X-API-Key": api_key})
        assert r.status_code == 403

    def test_instance_id_returns_instance_token(self):
        """Com instance_id, o token retornado é o webhook_secret da própria instância."""
        client, api_key = self._client()
        inst = {"provider": "meta", "webhook_secret": "inst-secret-abc"}
        with patch("routers.webhook._require_admin"), \
             patch("services.auth_service.require_instance_access"), \
             patch.dict("main.instances", {"inst-1": inst}, clear=True):
            r = client.get(
                "/webhook/meta-urls?instance_id=inst-1",
                headers={"X-API-Key": api_key},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["verify_token"] == "inst-secret-abc"
        for wh in data["webhooks"]:
            assert "token=inst-secret-abc" in wh["url"]

    def test_requires_api_key(self):
        client, _ = self._client()
        r = client.get("/webhook/meta-urls")
        assert r.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════════
# Meta Conversions API
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetaCapiService:
    def test_send_meta_capi_event_posts_expected_payload(self):
        from services.meta_capi_service import send_meta_capi_event

        inst = {
            "provider": "meta",
            "meta_capi_enabled": True,
            "meta_capi_access_token": "CAPI_TOKEN",
            "meta_capi_event_source_id": "123456789",
            "meta_capi_test_event_code": "TEST123",
        }
        lead = {
            "id": "lead-123",
            "nome": "João Silva",
            "telefone": "5511999990001",
            "email": "joao@email.com",
            "campanha": "Campanha A",
            "instancia_id": "meta-1",
            "leadgen_id": "1234567890123456",
            "form_id": "FORM123",
            "page_id": "PAGE123",
        }

        with patch("services.meta_capi_service.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.text = "ok"

            ok, detail = send_meta_capi_event(inst, lead, origem="meta-leads")

        assert ok is True
        assert detail == ""
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0].endswith("/123456789/events")
        assert kwargs["params"]["access_token"] == "CAPI_TOKEN"
        payload = kwargs["json"]
        assert payload["test_event_code"] == "TEST123"
        event = payload["data"][0]
        assert event["event_name"] == "Lead"
        assert event["action_source"] == "system_generated"
        assert event["custom_data"]["event_source"] == "crm"
        assert event["custom_data"]["lead_event_source"] == "KLEAD"
        assert event["custom_data"]["lead_id"] == "1234567890123456"
        assert event["custom_data"]["leadgen_id"] == "1234567890123456"
        assert "em" in event["user_data"]
        assert "ph" in event["user_data"]
        assert "external_id" in event["user_data"]
        assert event["user_data"]["lead_id"] == 1234567890123456
        assert event["event_id"] == "1234567890123456:Lead"

    def test_send_meta_capi_event_includes_extended_match_fields(self):
        from services.meta_capi_service import send_meta_capi_event

        inst = {
            "provider": "meta",
            "meta_capi_enabled": True,
            "meta_capi_access_token": "CAPI_TOKEN",
            "meta_capi_event_source_id": "123456789",
        }
        lead = {
            "id": "lead-123",
            "source_meta": {
                "lead_fields": {
                    "nome_completo": "João Silva",
                    "telefone": "5511999990001",
                    "email": "joao@email.com",
                },
                "field_data": {
                    "city": "São Paulo",
                    "state": "SP",
                    "zip": "01001-000",
                    "country": "BR",
                    "fbc": "fb.1.1234567890.abc",
                    "fbp": "fb.1.1234567890.def",
                    "client_ip_address": "200.10.10.10",
                    "client_user_agent": "Mozilla/5.0",
                },
                "referral": {
                    "ctwa_clid": "CTWA123",
                    "headline": "Campanha CTWA",
                },
                "form_answers": {
                    "o_que_mais_falta_no_seu_apartamento_atual": "LOCALIZAÇÃO E PRIVACIDADE",
                },
            },
        }

        with patch("services.meta_capi_service.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.text = "ok"

            ok, detail = send_meta_capi_event(inst, lead, origem="meta-forms")

        assert ok is True
        assert detail == ""
        payload = mock_post.call_args.kwargs["json"]
        event = payload["data"][0]
        user_data = event["user_data"]
        assert event["custom_data"]["event_source"] == "crm"
        assert event["custom_data"]["lead_event_source"] == "KLEAD"
        assert user_data["fbc"] == "fb.1.1234567890.abc"
        assert user_data["fbp"] == "fb.1.1234567890.def"
        assert user_data["client_ip_address"] == "200.10.10.10"
        assert user_data["client_user_agent"] == "Mozilla/5.0"
        assert "ct" in user_data
        assert "st" in user_data
        assert "zp" in user_data
        assert "country" in user_data
        assert event["custom_data"]["meta_referral_ctwa_clid"] == "CTWA123"
        assert event["custom_data"]["meta_form_o_que_mais_falta_no_seu_apartamento_atual"] == "LOCALIZAÇÃO E PRIVACIDADE"

    def test_send_meta_capi_event_is_silent_when_disabled(self):
        from services.meta_capi_service import send_meta_capi_event

        inst = {
            "provider": "meta",
            "meta_capi_enabled": False,
            "meta_capi_access_token": "CAPI_TOKEN",
            "meta_capi_event_source_id": "123456789",
        }
        lead = {
            "id": "lead-123",
            "nome": "João Silva",
            "telefone": "5511999990001",
            "email": "joao@email.com",
        }

        with patch("services.meta_capi_service.requests.post") as mock_post:
            ok, detail = send_meta_capi_event(inst, lead, origem="meta-leads")

        assert ok is False
        assert detail == "meta_capi_disabled"
        mock_post.assert_not_called()

    def test_send_meta_capi_event_writes_audit_entry(self):
        from services import meta_capi_service as svc

        inst = {
            "provider": "meta",
            "meta_capi_enabled": True,
            "meta_capi_access_token": "CAPI_TOKEN",
            "meta_capi_event_source_id": "123456789",
        }
        lead = {
            "id": "lead-123",
            "nome": "João Silva",
            "telefone": "5511999990001",
            "email": "joao@email.com",
            "instancia_id": "meta-1",
        }

        captured: list = []

        def fake_append(entry):
            captured.append(entry)

        def fake_get_audit(*, lead_id="", instance_id="", limit=20):
            return [
                e for e in reversed(captured)
                if (not lead_id or e.get("lead_id") == lead_id)
                and (not instance_id or e.get("instancia_id") == instance_id)
            ][:limit]

        with patch.object(svc, "_append_audit", fake_append), \
             patch.object(svc, "get_meta_capi_audit", fake_get_audit), \
             patch("services.meta_capi_service.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.text = "ok"
            ok, detail = svc.send_meta_capi_event(inst, lead, origem="meta")
            items = svc.get_meta_capi_audit(lead_id="lead-123", instance_id="meta-1", limit=5)

        assert ok is True
        assert detail == ""
        assert len(items) == 1
        assert items[0]["ok"] is True
        assert items[0]["payload"]["data"][0]["custom_data"]["lead_id"] == "lead-123"


class TestMetaCapiEndpoints:
    def test_crm_meta_capi_preview_send_and_audit(self):
        from fastapi.testclient import TestClient
        import main as m
        from services import meta_capi_service as svc

        client = TestClient(m.app)
        api_key = os.environ["DASHBOARD_SECRET"]
        lead = {
            "id": "lead-123",
            "nome": "João Silva",
            "telefone": "5511999990001",
            "email": "joao@email.com",
            "origem": "meta_ads",
            "instancia_id": "meta-1",
            "source_meta": {"referral": {"ctwa_clid": "CTWA123"}},
        }

        captured: list = []

        def fake_append(entry):
            captured.append(entry)

        def fake_get_audit(*, lead_id="", instance_id="", limit=20):
            return [
                e for e in reversed(captured)
                if (not lead_id or e.get("lead_id") == lead_id)
                and (not instance_id or e.get("instancia_id") == instance_id)
            ][:limit]

        with patch.object(svc, "_append_audit", fake_append), \
             patch("routers.crm.get_meta_capi_audit", fake_get_audit), \
             patch("main.crm_svc.get_lead", return_value=lead), \
             patch.dict(
                 "main.instances",
                 {
                     "meta-1": {
                         "provider": "meta",
                         "meta_capi_enabled": True,
                         "meta_capi_access_token": "CAPI_TOKEN",
                         "meta_capi_event_source_id": "123456789",
                         "meta_capi_test_event_code": "TEST123",
                     }
                 },
                 clear=True,
             ), \
             patch("services.meta_capi_service.requests.post") as mock_post, \
             patch("services.auth_service.verify_token", return_value={
                 "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
             }):
            mock_post.return_value.status_code = 200
            mock_post.return_value.text = "ok"

            auth_headers = {"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"}
            preview = client.get(
                "/crm/leads/lead-123/meta-capi/preview",
                headers=auth_headers,
            )
            send = client.post(
                "/crm/leads/lead-123/meta-capi/send",
                headers=auth_headers,
                json={"event_name": "Lead"},
            )
            audit = client.get(
                "/crm/leads/lead-123/meta-capi/audit",
                headers=auth_headers,
            )

        assert preview.status_code == 200
        assert preview.json()["ok"] is True
        assert preview.json()["payload"]["test_event_code"] == "TEST123"

        assert send.status_code == 200
        assert send.json()["ok"] is True
        assert send.json()["audit"][0]["ok"] is True

        assert audit.status_code == 200
        assert len(audit.json()["items"]) == 1
        assert audit.json()["items"][0]["lead_id"] == "lead-123"


class TestMetaStageEvents:
    def test_move_lead_sends_stage_event_for_meta_lead(self):
        from fastapi.testclient import TestClient
        import main as m

        client = TestClient(m.app)
        api_key = os.environ["DASHBOARD_SECRET"]

        lead = {
            "id": "lead-1",
            "telefone": "5511999990001",
            "origem": "meta-leads",
            "instancia_id": "meta-1",
            "coluna_id": "reuniao",
        }
        with patch("main.crm_svc.move_lead") as mock_move, \
             patch("main.crm_svc.get_lead", return_value=lead), \
             patch("routers.crm.apply_assignment_rule_to_lead") as mock_apply, \
             patch.object(m.app.state, "maybe_queue_meta_capi_event") as mock_queue, \
             patch("services.auth_service.verify_token", return_value={
                 "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
             }), \
             patch.dict(
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
             ):
            mock_move.return_value = lead
            mock_apply.return_value = lead

            r = client.patch(
                "/crm/leads/lead-1/move",
                headers={"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"},
                json={"coluna_id": "reuniao"},
            )

        assert r.status_code == 200
        mock_queue.assert_called_once()
        assert mock_queue.call_args[1]["event_name"] == "Schedule"

    def test_update_fields_requeues_event_for_meta_lead(self):
        from fastapi.testclient import TestClient
        import main as m

        client = TestClient(m.app)
        api_key = os.environ["DASHBOARD_SECRET"]

        with patch("main.crm_svc.get_lead") as mock_get, \
             patch("main.crm_svc.update_fields") as mock_update, \
             patch.object(m.app.state, "maybe_queue_meta_capi_event") as mock_queue, \
             patch("services.auth_service.verify_token", return_value={
                 "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
             }), \
             patch.dict(
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
             ):
            before = {
                "id": "lead-1",
                "telefone": "5511999990001",
                "origem": "meta-leads",
                "instancia_id": "meta-1",
                "coluna_id": "qualif",
                "lead_status": "em_contato",
            }
            lead = {
                "id": "lead-1",
                "telefone": "5511999990001",
                "origem": "meta-leads",
                "instancia_id": "meta-1",
                "coluna_id": "quente",
                "lead_status": "qualificado",
            }
            mock_get.return_value = before
            mock_update.return_value = lead

            r = client.patch(
                "/crm/leads/lead-1/fields",
                headers={"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"},
                json={"lead_status": "qualificado"},
            )

        assert r.status_code == 200
        mock_queue.assert_called_once()
        assert mock_queue.call_args[1]["event_name"] == "SubmitApplication"

    def test_contact_log_sends_contact_event_for_meta_lead(self):
        from fastapi.testclient import TestClient
        import main as m

        client = TestClient(m.app)
        api_key = os.environ["DASHBOARD_SECRET"]

        lead = {
            "id": "lead-1",
            "telefone": "5511999990001",
            "origem": "meta-leads",
            "instancia_id": "meta-1",
            "coluna_id": "qualif",
        }
        with patch("main.crm_svc.add_contact_log") as mock_contact, \
             patch("main.crm_svc.get_lead", return_value=lead), \
             patch.object(m.app.state, "maybe_queue_meta_capi_event") as mock_queue, \
             patch("services.auth_service.verify_token", return_value={
                 "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
             }), \
             patch.dict(
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
             ):
            mock_contact.return_value = lead

            r = client.post(
                "/crm/leads/lead-1/contatos",
                headers={"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"},
                json={"tipo": "whatsapp", "nota": "Retomado no aquecimento"},
            )

        assert r.status_code == 200
        mock_queue.assert_called_once()
        assert mock_queue.call_args[1]["event_name"] == "Contact"
