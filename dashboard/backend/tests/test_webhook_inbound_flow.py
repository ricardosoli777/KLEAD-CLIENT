"""
Testes de integração do fluxo crítico de receita:
  Evolution GO webhook → validação de token → conversa salva → IA qualifica
  → resposta enviada / handoff humano / desqualificação.

Exercita o endpoint POST /webhook/{instance_id} de ponta a ponta com os
provedores externos mockados (qualify_lead e send_outbound_text). O pool de
threads inbound é substituído por execução síncrona para os asserts serem
determinísticos.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as app_module
import routers.webhook as webhook_module
from fastapi.testclient import TestClient

TEST_IID = "wh-flow-test"
TEST_SECRET = "segredo-webhook-teste"
TEST_PHONE = "5511999990077"


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers e fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def evo_message_payload(phone=TEST_PHONE, text="Olá, tenho interesse",
                        from_me=False, is_group=False, push_name="Lead Teste"):
    """Payload no formato Evolution GO: event='Message', data.Info.*"""
    return {
        "event": "Message",
        "data": {
            "Info": {
                "Chat": f"{phone}@s.whatsapp.net" if phone else "",
                "IsFromMe": from_me,
                "IsGroup": is_group,
                "PushName": push_name,
            },
            "Message": {"conversation": text},
        },
    }


def evo_upsert_payload(remote_jid: str, text="Olá, tenho interesse", remote_jid_alt: str = "",
                       message_id: str = ""):
    """Payload no formato Evolution API: event='messages.upsert'."""
    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": remote_jid,
                "remoteJidAlt": remote_jid_alt,
                "fromMe": False,
                "id": message_id,
            },
            "pushName": "Lead Teste",
            "message": {"conversation": text},
        },
    }


def get_conv(phone=TEST_PHONE):
    """Busca a conversa do lead no estado, resolvendo a chave canônica."""
    with app_module.conversations_lock:
        key = app_module._resolve_conversation_key(TEST_IID, phone)
        return app_module.conversations.get(TEST_IID, {}).get(key)


@pytest.fixture()
def client():
    return TestClient(app_module.app, raise_server_exceptions=True)


@pytest.fixture()
def instance():
    """Registra uma instância de teste no estado e limpa tudo ao final.
    _save_conversations vira no-op para o teste não tocar disco/Postgres."""
    with app_module.instances_lock:
        app_module.instances[TEST_IID] = {
            "name": "Instância Webhook Teste",
            "webhook_secret": TEST_SECRET,
            "inbound_ai_enabled": True,
            "ai_response_delay": 2,
            "max_ai_turns": 8,
            "auto_handoff_keywords": ["humano", "atendente"],
            "qualification_prompt": "Qualifique o lead.",
            "api_url": "http://evolution:8080",
            "api_key": "evo-key-teste",
        }
    with app_module.conversations_lock:
        app_module.conversations.pop(TEST_IID, None)
    with patch.object(app_module, "_save_conversations"):
        yield TEST_IID
    with app_module.instances_lock:
        app_module.instances.pop(TEST_IID, None)
    with app_module.conversations_lock:
        app_module.conversations.pop(TEST_IID, None)


@pytest.fixture()
def sync_inbound():
    """Executa _handle_inbound na própria thread em vez do pool — determinístico."""
    with patch.object(webhook_module, "_submit_inbound", new=lambda func, *args: func(*args)):
        yield


def post_webhook(client, payload, token=TEST_SECRET, iid=TEST_IID):
    return client.post(f"/webhook/{iid}", params={"token": token}, json=payload)


def meta_waba_payload(field: str, value: dict, waba_id: str = "WABA-TEST") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba_id, "changes": [{"field": field, "value": value}]}],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Autenticação do webhook Evolution
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookAuth:
    def test_instancia_inexistente_nao_processa(self, client, instance):
        resp = post_webhook(client, evo_message_payload(), iid="nao-existe")
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "reason": "instance not found"}

    def test_sem_secret_configurado_retorna_503(self, client, instance):
        with app_module.instances_lock:
            app_module.instances[TEST_IID]["webhook_secret"] = ""
        resp = post_webhook(client, evo_message_payload(), token="qualquer")
        assert resp.status_code == 503

    def test_token_errado_retorna_401(self, client, instance, sync_inbound):
        resp = post_webhook(client, evo_message_payload(), token="token-invalido")
        assert resp.status_code == 401
        assert get_conv() is None, "mensagem com token inválido não pode virar conversa"

    def test_token_correto_aceita(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response"):
            resp = post_webhook(client, evo_message_payload())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Filtros: fromMe, grupo, sem telefone, evento desconhecido
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookFilters:
    def test_mensagem_propria_ignorada(self, client, instance, sync_inbound):
        resp = post_webhook(client, evo_message_payload(from_me=True))
        assert resp.json() == {"ok": True, "reason": "ignored"}
        assert get_conv() is None

    def test_mensagem_de_grupo_ignorada(self, client, instance, sync_inbound):
        resp = post_webhook(client, evo_message_payload(is_group=True))
        assert resp.json() == {"ok": True, "reason": "ignored"}
        assert get_conv() is None

    def test_sem_telefone_ignorado(self, client, instance, sync_inbound):
        resp = post_webhook(client, evo_message_payload(phone=""))
        assert resp.json() == {"ok": True, "reason": "no phone"}

    def test_evento_desconhecido_ignorado(self, client, instance, sync_inbound):
        resp = post_webhook(client, {"event": "Presence", "data": {}})
        body = resp.json()
        assert body["ok"] is True
        assert "event ignored" in body["reason"]
        assert get_conv() is None

    def test_parser_jid_preserva_lid_e_mantem_pn_legado(self):
        lid = app_module._parse_whatsapp_jid("123456789:4@lid")
        assert lid == {
            "raw": "123456789:4@lid",
            "user": "123456789",
            "server": "lid",
            "type": "lid",
            "device": "4",
            "is_phone": False,
        }
        assert app_module._conversation_phone_key("123456789:4@lid") == "123456789@lid"
        assert app_module._conversation_phone_key(f"{TEST_PHONE}@s.whatsapp.net") == TEST_PHONE

    def test_evolution_go_lid_nao_vira_telefone(self, client, instance, sync_inbound):
        payload = evo_message_payload(phone="123456789", text="Mensagem por LID")
        payload["data"]["Info"]["Chat"] = "123456789@lid"
        with patch.object(app_module, "_schedule_ai_response"):
            resp = post_webhook(client, payload)
        assert resp.json()["ok"] is True
        with app_module.conversations_lock:
            assert "123456789@lid" in app_module.conversations[TEST_IID]
            assert "123456789" not in app_module.conversations[TEST_IID]
            identity = app_module.conversations[TEST_IID]["123456789@lid"]["source_meta"]["evolution_identity"]
        assert identity["lid_jid"] == "123456789@lid"
        assert identity["jid_type"] == "lid"
        assert identity["mapping_observed"] is False


    def _enable_meta(self):
        with app_module.instances_lock:
            app_module.instances[TEST_IID]["meta_app_secret"] = "meta-secret"

    def test_user_id_update_links_aliases_only_to_existing_conversation(self, client, instance):
        self._enable_meta()
        with app_module.conversations_lock:
            app_module.conversations[TEST_IID] = {
                TEST_PHONE: {"name": "Lead", "messages": [], "source_meta": {}}
            }
        value = {
            "wa_id": TEST_PHONE,
            "old_user_id": "BR.OLD",
            "new_user_id": "BR.NEW",
            "parent_user_id": "PARENT.1",
        }
        persist = MagicMock()
        with patch.object(webhook_module, "_verify_meta_signature", return_value=True), \
             patch.object(app_module.app.state, "persist_meta_identity_aliases", persist), \
             patch.object(app_module.app.state, "record_webhook_event"):
            response = client.post(
                f"/webhook/{TEST_IID}",
                json=meta_waba_payload("user_id_update", value),
                headers={"x-hub-signature-256": "sha256=test"},
            )
        assert response.status_code == 200
        persist.assert_called_once()
        assert persist.call_args.args[1] == TEST_PHONE
        assert persist.call_args.args[2]["old_user_id"] == "BR.OLD"
        assert persist.call_args.args[2]["new_user_id"] == "BR.NEW"
        with app_module.conversations_lock:
            assert set(app_module.conversations[TEST_IID]) == {TEST_PHONE}

    def test_user_id_update_conflict_never_persists_or_merges(self, client, instance):
        self._enable_meta()
        with app_module.conversations_lock:
            app_module.conversations[TEST_IID] = {
                "BR.OLD": {"name": "A", "messages": [], "source_meta": {}},
                "BR.NEW": {"name": "B", "messages": [], "source_meta": {}},
            }
        persist = MagicMock()
        with patch.object(webhook_module, "_verify_meta_signature", return_value=True), \
             patch.object(app_module.app.state, "persist_meta_identity_aliases", persist), \
             patch.object(app_module.app.state, "record_webhook_event"):
            response = client.post(
                f"/webhook/{TEST_IID}",
                json=meta_waba_payload("user_id_update", {
                    "old_user_id": "BR.OLD", "new_user_id": "BR.NEW",
                }),
                headers={"x-hub-signature-256": "sha256=test"},
            )
        assert response.status_code == 200
        persist.assert_not_called()
        with app_module.conversations_lock:
            assert set(app_module.conversations[TEST_IID]) == {"BR.OLD", "BR.NEW"}

    def test_business_username_update_is_audited_but_not_linked_to_lead(self, client, instance):
        self._enable_meta()
        persist = MagicMock()
        record = MagicMock()
        with patch.object(webhook_module, "_verify_meta_signature", return_value=True), \
             patch.object(app_module.app.state, "persist_meta_identity_aliases", persist), \
             patch.object(app_module.app.state, "record_webhook_event", record):
            response = client.post(
                f"/webhook/{TEST_IID}",
                json=meta_waba_payload("business_username_updates", {
                    "username": "klead.oficial",
                    "previous_username": "klead",
                    "event": "APPROVED",
                }),
                headers={"x-hub-signature-256": "sha256=test"},
            )
        assert response.status_code == 200
        persist.assert_not_called()
        assert record.call_args.kwargs["event_type"] == "whatsapp_business_username_update"

    def test_incoming_message_persists_profile_username_and_bsuids(self, client, instance, sync_inbound):
        self._enable_meta()
        value = {
            "metadata": {"phone_number_id": "PHONE-ID"},
            "contacts": [{
                "wa_id": TEST_PHONE,
                "user_id": "BR.USER123",
                "parent_user_id": "PARENT.123",
                "profile": {"name": "Lead Meta", "username": "@lead.meta"},
            }],
            "messages": [{
                "from": TEST_PHONE,
                "from_user_id": "BR.USER123",
                "id": "wamid.identity.1",
                "type": "text",
                "text": {"body": "Olá"},
            }],
        }
        persist = MagicMock()
        with patch.object(webhook_module, "_verify_meta_signature", return_value=True), \
             patch.object(app_module.app.state, "persist_meta_identity_aliases", persist), \
             patch.object(app_module.app.state, "record_webhook_event"), \
             patch.object(app_module, "_schedule_ai_response"):
            response = client.post(
                f"/webhook/{TEST_IID}",
                json=meta_waba_payload("messages", value),
                headers={"x-hub-signature-256": "sha256=test"},
            )
        assert response.status_code == 200
        assert persist.called
        source_meta = persist.call_args_list[0].args[2]
        assert source_meta["user_id"] == "BR.USER123"
        assert source_meta["parent_user_id"] == "PARENT.123"
        assert source_meta["username"] == "@lead.meta"
        assert source_meta["waba_id"] == "WABA-TEST"
        assert source_meta["phone_number_id"] == "PHONE-ID"

    def test_evolution_api_lid_nao_vira_telefone(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response"):
            resp = post_webhook(client, evo_upsert_payload("987654321@lid"))
        assert resp.json()["ok"] is True
        with app_module.conversations_lock:
            assert "987654321@lid" in app_module.conversations[TEST_IID]
            assert "987654321" not in app_module.conversations[TEST_IID]

    def test_evolution_api_preserva_par_lid_pn_na_chave_telefonica(self, client, instance, sync_inbound):
        payload = evo_upsert_payload(
            "987654321@lid",
            remote_jid_alt=f"{TEST_PHONE}@s.whatsapp.net",
        )
        with patch.object(app_module, "_schedule_ai_response"):
            resp = post_webhook(client, payload)
        assert resp.json()["ok"] is True
        with app_module.conversations_lock:
            conv = app_module.conversations[TEST_IID][TEST_PHONE]
            identity = conv["source_meta"]["evolution_identity"]
        assert identity["lid_jid"] == "987654321@lid"
        assert identity["pn_jid"] == f"{TEST_PHONE}@s.whatsapp.net"
        assert identity["mapping_observed"] is True
        assert identity["jid_aliases"] == [
            "987654321@lid",
            f"{TEST_PHONE}@s.whatsapp.net",
        ]

    def test_shadow_desligado_por_padrao_nao_altera_metadados(self, client, instance, sync_inbound):
        payload = evo_upsert_payload("777@lid", remote_jid_alt=f"{TEST_PHONE}@s.whatsapp.net")
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(app_module, "_schedule_ai_response"):
            os.environ.pop("IDENTITY_RESOLVER_SHADOW_ENABLED", None)
            post_webhook(client, payload)
        with app_module.conversations_lock:
            source_meta = app_module.conversations[TEST_IID][TEST_PHONE]["source_meta"]
        assert "identity_resolution_shadow" not in source_meta

    def test_pn_lid_sao_resolvidos_para_uma_conversa(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response"):
            post_webhook(client, evo_upsert_payload(
                TEST_PHONE,
                remote_jid_alt="888@lid",
            ))
            post_webhook(client, evo_upsert_payload(
                "888@lid",
                remote_jid_alt=f"{TEST_PHONE}@s.whatsapp.net",
            ))
        with app_module.conversations_lock:
            convs = app_module.conversations[TEST_IID]
        assert set(convs) == {TEST_PHONE}
        assert len(convs[TEST_PHONE]["messages"]) == 2

    def test_mesmo_message_id_em_pn_e_lid_e_processado_uma_vez(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response") as mock_sched:
            post_webhook(client, evo_upsert_payload(
                TEST_PHONE, remote_jid_alt="999@lid", message_id="MSG-1",
            ))
            post_webhook(client, evo_upsert_payload(
                "999@lid", remote_jid_alt=f"{TEST_PHONE}@s.whatsapp.net", message_id="MSG-1",
            ))
        with app_module.conversations_lock:
            convs = app_module.conversations[TEST_IID]
        assert set(convs) == {TEST_PHONE}
        assert len(convs[TEST_PHONE]["messages"]) == 1
        mock_sched.assert_called_once()

    def test_duplicata_historica_lid_nao_desvia_nova_mensagem_do_telefone(self, client, instance, sync_inbound):
        with app_module.conversations_lock:
            app_module.conversations[TEST_IID] = {}
        phone_conv = app_module._get_or_create_conversation(TEST_IID, TEST_PHONE, "Telefone")
        phone_conv["source_meta"] = {}
        lid_conv = app_module._get_or_create_conversation(TEST_IID, "999@lid", "LID")
        lid_conv["source_meta"] = {
            "evolution_identity": {"jid_aliases": ["999@lid"]}
        }
        with patch.object(app_module, "_schedule_ai_response"):
            post_webhook(client, evo_upsert_payload(
                "999@lid", remote_jid_alt=f"{TEST_PHONE}@s.whatsapp.net", message_id="MSG-2",
            ))
        with app_module.conversations_lock:
            convs = app_module.conversations[TEST_IID]
        assert len(convs[TEST_PHONE]["messages"]) == 1
        assert convs["999@lid"]["messages"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Conversa salva e decisão de acionar a IA
# ═══════════════════════════════════════════════════════════════════════════════

class TestInboundConversation:
    def test_mensagem_salva_na_conversa(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response"):
            post_webhook(client, evo_message_payload(text="Quero um orçamento"))
        conv = get_conv()
        assert conv is not None
        assert conv["name"] == "Lead Teste"
        assert conv["unread"] is True
        assert conv["messages"][-1]["role"] == "user"
        assert conv["messages"][-1]["content"] == "Quero um orçamento"

    def test_burst_do_mesmo_lead_vira_uma_conversa(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response"):
            post_webhook(client, evo_message_payload(text="Oi"))
            post_webhook(client, evo_message_payload(text="Tem disponibilidade?"))
        conv = get_conv()
        assert len(conv["messages"]) == 2
        with app_module.conversations_lock:
            assert len(app_module.conversations[TEST_IID]) == 1

    def test_ia_ativa_agenda_resposta(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response") as mock_sched:
            post_webhook(client, evo_message_payload())
        mock_sched.assert_called_once()

    def test_ia_pausada_por_humano_nao_responde(self, client, instance, sync_inbound):
        with patch.object(app_module, "_schedule_ai_response") as mock_sched:
            post_webhook(client, evo_message_payload(text="primeira"))
            conv = get_conv()
            conv["ai_paused"] = True
            post_webhook(client, evo_message_payload(text="segunda"))
        assert mock_sched.call_count == 1, "com humano no controle a IA não pode ser agendada"
        assert get_conv()["messages"][-1]["content"] == "segunda", "mensagem deve ser salva mesmo pausada"

    def test_ia_desabilitada_na_instancia_nao_responde(self, client, instance, sync_inbound):
        with app_module.instances_lock:
            app_module.instances[TEST_IID]["inbound_ai_enabled"] = False
        with patch.object(app_module, "_schedule_ai_response") as mock_sched:
            post_webhook(client, evo_message_payload())
        mock_sched.assert_not_called()
        assert get_conv() is not None, "conversa é salva mesmo sem IA"

    def test_form_ctwa_sem_nome_nao_apaga_nome_existente(self, client, instance, sync_inbound):
        """Regressão: `if nome_form and not conv.get("name") or conv.get("name") == phone`
        sem parênteses sobrescrevia o nome com "" sempre que o nome atual era só
        o telefone (placeholder) e o formulário CTWA não trazia "Nome:" —
        precedência de "and"/"or" fazia o "or" valer sozinho."""
        with patch.object(app_module, "_schedule_ai_response"):
            # push_name vazio -> conv["name"] vira o próprio telefone (placeholder)
            post_webhook(client, evo_message_payload(text="Oi", push_name=""))
            assert get_conv()["name"] == TEST_PHONE

            # Mensagem estilo formulário CTWA sem campo de nome, só email
            post_webhook(client, evo_message_payload(text="Email: lead@example.com", push_name=""))

        conv = get_conv()
        assert conv["name"] == TEST_PHONE, "nome não pode virar string vazia quando o formulário não traz nome"
        assert conv["lead_email"] == "lead@example.com"


# ═══════════════════════════════════════════════════════════════════════════════
# Decisão da IA: reply / handoff / disqualify / keyword
# ═══════════════════════════════════════════════════════════════════════════════

def _ai_result(message="Qual seu orçamento?", action="reply", score=55, reason="engajado"):
    return {"message": message, "action": action, "score": score, "score_reason": reason}


@pytest.fixture()
def ai_mocks():
    """Mocka provedores externos e leituras de arquivo em volta de _ai_respond_to_conv."""
    with patch.object(app_module, "qualify_lead") as mock_qualify, \
         patch.object(app_module, "send_outbound_text", return_value=(True, "", "evolution")) as mock_send, \
         patch.object(app_module, "record_pattern"), \
         patch.object(app_module, "_load_knowledge", return_value=""), \
         patch.object(app_module, "build_knowledge_from_patterns", return_value=""), \
         patch.object(app_module, "_load_agent_memory", return_value=""):
        yield mock_qualify, mock_send


def run_ai(last_text="Quero saber mais"):
    with app_module.instances_lock:
        inst = dict(app_module.instances[TEST_IID])
    conv = get_conv()
    app_module._ai_respond_to_conv(TEST_IID, TEST_PHONE, inst, conv, last_text)


def seed_conversation(client, text="Quero saber mais"):
    with patch.object(app_module, "_schedule_ai_response"):
        post_webhook(client, evo_message_payload(text=text))


class TestAIDecision:
    def test_reply_envia_resposta_e_atualiza_conversa(self, client, instance, sync_inbound, ai_mocks):
        mock_qualify, mock_send = ai_mocks
        mock_qualify.return_value = _ai_result()
        seed_conversation(client)

        run_ai()

        conv = get_conv()
        assert conv["messages"][-1] == {
            "role": "assistant",
            "content": "Qual seu orçamento?",
            "timestamp": conv["messages"][-1]["timestamp"],
        }
        assert conv["turn_count"] == 1
        assert conv["score"] == 55
        assert conv["stage"] == "qualifying"
        mock_send.assert_called_once()
        assert mock_send.call_args[0][3] == "Qual seu orçamento?"

    def test_handoff_marca_precisa_de_humano_e_ainda_responde(self, client, instance, sync_inbound, ai_mocks):
        mock_qualify, mock_send = ai_mocks
        mock_qualify.return_value = _ai_result(
            message="Vou te passar para um especialista.", action="handoff", score=85, reason="lead quente")
        seed_conversation(client)

        run_ai()

        conv = get_conv()
        assert conv["stage"] == "handoff"
        assert conv["needs_human"] is True
        assert conv["handoff_source"] == "ai"
        assert conv["handoff_reason"] == "lead quente"
        mock_send.assert_called_once()

    def test_disqualify_encerra_sem_handoff(self, client, instance, sync_inbound, ai_mocks):
        mock_qualify, _ = ai_mocks
        mock_qualify.return_value = _ai_result(
            message="Obrigado pelo contato!", action="disqualify", score=5, reason="fora do perfil")
        seed_conversation(client)

        run_ai()

        conv = get_conv()
        assert conv["stage"] == "disqualified"
        assert conv["needs_human"] is False

    def test_keyword_de_handoff_forca_estagio_handoff(self, client, instance, sync_inbound, ai_mocks):
        mock_qualify, _ = ai_mocks
        mock_qualify.return_value = _ai_result()  # IA responderia normalmente
        seed_conversation(client, text="quero falar com um humano")

        run_ai(last_text="quero falar com um humano")

        conv = get_conv()
        assert conv["stage"] == "handoff"
        assert conv["needs_human"] is True
        assert conv["handoff_source"] == "keyword"

    def test_resposta_vazia_nao_envia_mensagem(self, client, instance, sync_inbound, ai_mocks):
        mock_qualify, mock_send = ai_mocks
        mock_qualify.return_value = _ai_result(message="", action="reply", score=None, reason="")
        seed_conversation(client)

        run_ai()

        mock_send.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Ponta a ponta: webhook → conversa → IA → resposta
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_fluxo_completo_do_webhook_ate_a_resposta(self, client, instance, sync_inbound, ai_mocks):
        mock_qualify, mock_send = ai_mocks
        mock_qualify.return_value = _ai_result(message="Perfeito! Qual sua região?", score=60)

        def immediate_schedule(instance_id, phone, inst, audio_reply=False):
            """Substitui o debounce de 2s: dispara a IA na hora, no mesmo fluxo."""
            conv = get_conv(phone)
            last = next((m["content"] for m in reversed(conv["messages"]) if m["role"] == "user"), "")
            app_module._ai_respond_to_conv(instance_id, phone, inst, conv, last, audio_reply=audio_reply)

        with patch.object(app_module, "_schedule_ai_response", side_effect=immediate_schedule):
            resp = post_webhook(client, evo_message_payload(text="Oi, vi o anúncio de vocês"))

        assert resp.status_code == 200
        conv = get_conv()
        roles = [m["role"] for m in conv["messages"]]
        assert roles == ["user", "assistant"], "conversa deve ter pergunta do lead e resposta da IA"
        assert conv["messages"][0]["content"] == "Oi, vi o anúncio de vocês"
        assert conv["messages"][1]["content"] == "Perfeito! Qual sua região?"
        assert conv["score"] == 60
        assert conv["turn_count"] == 1
        mock_qualify.assert_called_once()
        assert mock_qualify.call_args.kwargs["history"][0]["content"] == "Oi, vi o anúncio de vocês"
        mock_send.assert_called_once()
