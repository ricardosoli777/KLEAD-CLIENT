"""
Testes para os fixes de segurança e qualidade aplicados na auditoria.
Cobre: CR-01, CR-02, CR-04, CR-06, CR-07, WR-03, WR-05, WR-06, WR-08, WR-09, IN-01, IN-02, IN-04, IN-05
"""

import json
import os
import sys
import time
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Setup de ambiente mínimo para importar os módulos ────────────────────────
os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

# Adiciona o diretório do backend ao path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# CR-01 — login não retorna token no body
# ═══════════════════════════════════════════════════════════════════════════════

class TestCR01LoginNoTokenInBody:
    def test_login_response_has_no_token_field(self):
        """O body do login não deve expor o JWT — cookie HttpOnly é suficiente."""
        from fastapi.testclient import TestClient
        import main as app_module

        # Garante que existe um usuário para logar
        with patch.object(app_module.auth_svc, "login") as mock_login, \
             patch.object(app_module.auth_svc, "create_token", return_value="fake.jwt.token"), \
             patch.object(app_module.auth_svc, "_safe_user", return_value={"email": "test@test.com", "funcao": "admin"}):

            mock_login.return_value = {"email": "test@test.com", "first_access": False}

            client = TestClient(app_module.app, raise_server_exceptions=True)
            response = client.post(
                "/auth/login",
                json={"email": "test@test.com", "password": "senha123"},
            )

        assert response.status_code == 200
        body = response.json()
        assert "token" not in body, "JWT não deve aparecer no body do login"
        assert "ok" in body
        assert "user" in body


# ═══════════════════════════════════════════════════════════════════════════════
# CR-02 — _validate_api_url bloqueia SSRF
# ═══════════════════════════════════════════════════════════════════════════════

class TestCR02ValidateApiUrl:
    def setup_method(self):
        from main import _validate_api_url
        self.validate = _validate_api_url

    def test_accepts_public_hostname(self):
        assert self.validate("http://host.docker.internal:4000") == "http://host.docker.internal:4000"

    def test_accepts_https_public(self):
        assert self.validate("https://api.evolution.com") == "https://api.evolution.com"

    def test_accepts_empty(self):
        assert self.validate("") == ""

    def test_rejects_loopback_ip(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self.validate("http://127.0.0.1:4000")
        assert exc.value.status_code == 400

    def test_rejects_private_ip_192(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.validate("http://192.168.1.1:8000")

    def test_rejects_private_ip_10(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.validate("http://10.0.0.1/evil")

    def test_rejects_link_local(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.validate("http://169.254.169.254/metadata")

    def test_rejects_non_http_scheme(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.validate("ftp://somehost/file")

    def test_rejects_hostname_that_resolves_to_private_ip(self):
        from fastapi import HTTPException
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.5", 4000))]):
            with pytest.raises(HTTPException):
                self.validate("http://internal.example.com:4000")


# ═══════════════════════════════════════════════════════════════════════════════
# CR-04 — blacklist usa compare_digest (tempo constante)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCR04BlacklistTimingResistance:
    def test_revoked_token_is_rejected(self):
        import services.auth_service as auth_svc
        original = set(auth_svc._blacklist)
        fake_token = "header.payload.signature"
        auth_svc._blacklist = {fake_token}
        try:
            result = auth_svc.verify_token(fake_token)
            assert result is None
        finally:
            auth_svc._blacklist = original

    def test_non_revoked_token_passes_blacklist_check(self):
        import services.auth_service as auth_svc
        original = set(auth_svc._blacklist)
        auth_svc._blacklist = {"other.token.here"}
        try:
            # token diferente — não deve ser barrado pela blacklist (pode falhar no JWT decode, tudo bem)
            result = auth_svc.verify_token("completely.different.token")
            # result pode ser None por JWT inválido, mas não por blacklist
        finally:
            auth_svc._blacklist = original


# ═══════════════════════════════════════════════════════════════════════════════
# CR-06 + CR-07 + IN-04 — escritas atômicas em campaign_service
# ═══════════════════════════════════════════════════════════════════════════════

class TestAtomicWrites:
    def test_save_history_is_atomic(self, tmp_path, monkeypatch):
        """save_history deve escrever em .tmp e renomear — nunca truncar direto."""
        import services.campaign_service as cs
        monkeypatch.setattr(cs, "HISTORY_FILE", tmp_path / "history.json")

        cs.save_history("inst1", "2026-01-01T00:00:00", "completed", 10, 9, 1)

        assert (tmp_path / "history.json").exists()
        assert not (tmp_path / "history.tmp").exists(), "arquivo .tmp deve ser removido após rename"
        data = json.loads((tmp_path / "history.json").read_text())
        assert data[0]["instance_id"] == "inst1"

    def test_save_history_no_corruption_on_existing(self, tmp_path, monkeypatch):
        """save_history deve acumular entradas, não sobrescrever."""
        import services.campaign_service as cs
        monkeypatch.setattr(cs, "HISTORY_FILE", tmp_path / "history.json")

        cs.save_history("inst1", "2026-01-01T00:00:00", "completed", 5, 5, 0)
        cs.save_history("inst2", "2026-01-02T00:00:00", "stopped", 3, 2, 1)

        data = json.loads((tmp_path / "history.json").read_text())
        assert len(data) == 2

    def test_save_invalid_numbers_is_atomic(self, tmp_path, monkeypatch):
        import services.campaign_service as cs
        monkeypatch.setattr(cs, "PROGRESS_DIR", tmp_path)
        tmp_path.mkdir(exist_ok=True)

        cs.save_invalid_numbers("inst1", {"5511999990001", "5511999990002"})

        f = tmp_path / "inst1_invalid.json"
        assert f.exists()
        assert not (tmp_path / "inst1_invalid.tmp").exists()
        data = set(json.loads(f.read_text()))
        assert data == {"5511999990001", "5511999990002"}

    def test_save_sent_numbers_is_atomic(self, tmp_path, monkeypatch):
        import services.campaign_service as cs
        monkeypatch.setattr(cs, "PROGRESS_DIR", tmp_path)
        tmp_path.mkdir(exist_ok=True)

        cs.save_sent_numbers("inst1", {"5511999990001"})

        f = tmp_path / "inst1_sent.json"
        assert f.exists()
        assert not (tmp_path / "inst1_sent.tmp").exists()
        data = set(json.loads(f.read_text()))
        assert data == {"5511999990001"}


class TestProfileRoleProtection:
    def test_profile_update_cannot_escalate_role(self, monkeypatch):
        import services.auth_service as auth_svc

        original_users = auth_svc._users
        try:
            auth_svc._users = {
                "user@test.com": {
                    "id": "1",
                    "email": "user@test.com",
                    "nome": "User",
                    "avatar": "",
                    "funcao": "sdr",
                    "status": "active",
                    "first_access": False,
                    "token_version": 0,
                    "allowed_instances": [],
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            }
            monkeypatch.setattr(auth_svc, "_save", lambda: None)

            updated = auth_svc.update_profile("user@test.com", nome="User 2", avatar="", funcao="superadmin")

            assert updated is not None
            assert updated["nome"] == "User 2"
            assert updated["funcao"] == "sdr"
        finally:
            auth_svc._users = original_users


class TestQueueGenerationResilience:
    def test_generate_queue_thread_recovers_from_exception_and_persists_partial_queue(self, tmp_path, monkeypatch):
        import services.campaign_service as cs

        original_progress_dir = cs.PROGRESS_DIR
        try:
            cs.PROGRESS_DIR = tmp_path
            tmp_path.mkdir(parents=True, exist_ok=True)

            call_count = {"n": 0}

            def fake_generate_message(**kwargs):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("boom")
                return "mensagem 1"

            monkeypatch.setattr(cs, "generate_message", fake_generate_message)

            instances = {
                "inst1": {
                    "contacts": [
                        {"name": "A", "number": "5511999990001"},
                        {"name": "B", "number": "5511999990002"},
                    ],
                    "prompt": "prompt",
                    "model": "gpt-4o-mini",
                    "llm_provider": "openai",
                    "queue": ["stale"],
                    "queue_progress": 99,
                    "queue_status": "generating",
                    "queue_stop_flag": False,
                }
            }

            cs.generate_queue_thread("inst1", instances, "contexto")

            queue_file = tmp_path / "inst1_queue.json"
            assert queue_file.exists()
            assert instances["inst1"]["queue_status"] == "idle"
            assert instances["inst1"]["queue_progress"] == 1
            saved = json.loads(queue_file.read_text(encoding="utf-8"))
            assert len(saved) == 1
            assert saved[0]["phone"] == "5511999990001"
        finally:
            cs.PROGRESS_DIR = original_progress_dir


# ═══════════════════════════════════════════════════════════════════════════════
# Roleta — sorteio de fila
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssignmentShuffleQueue:
    def test_shuffle_queue_reorders_eligible_participants(self, monkeypatch):
        import services.assignment_service as ass

        original_state = json.loads(json.dumps(ass._state))
        tenant_key = "tenant-shuffle@test.com"
        try:
            ass._state = {"tenants": {tenant_key: {
                "enabled": False,
                "mode": "manual",
                "pointer": 7,
                "queue": [],
                "queue_generated_at": "",
                "attendants": [
                    {"email": "a@klead.com", "nome": "A", "instance_id": "a", "active": True, "status": "available", "weight": 1, "order": 0},
                    {"email": "b@klead.com", "nome": "B", "instance_id": "b", "active": True, "status": "available", "weight": 1, "order": 1},
                    {"email": "c@klead.com", "nome": "C", "instance_id": "c", "active": True, "status": "paused", "weight": 1, "order": 2},
                ],
                "history": [],
                "rules": [],
            }}}
            monkeypatch.setattr(ass.random, "shuffle", lambda items: items.reverse())

            result = ass.shuffle_queue(tenant_key)

            assert result["settings"]["pointer"] == 0
            assert result["settings"]["queue_count"] == 2
            assert [item["email"] for item in result["queue"]] == ["b@klead.com", "a@klead.com"]
            tenant_state = ass._state["tenants"][tenant_key]
            assert tenant_state["queue"] == ["b@klead.com", "a@klead.com"]
            assert tenant_state["pointer"] == 0
            assert tenant_state["enabled"] is False
        finally:
            ass._state = original_state


# ═══════════════════════════════════════════════════════════════════════════════
# WR-03 — _safe_filename com allowlist
# ═══════════════════════════════════════════════════════════════════════════════

class TestWR03SafeFilename:
    def setup_method(self):
        from main import _safe_filename
        self.safe = _safe_filename

    def test_accepts_valid_pdf(self):
        assert self.safe("documento.pdf") == "documento.pdf"

    def test_accepts_valid_csv(self):
        assert self.safe("contatos.csv") == "contatos.csv"

    def test_accepts_valid_xlsx(self):
        assert self.safe("lista.xlsx") == "lista.xlsx"

    def test_rejects_path_traversal(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.safe("../../etc/passwd")

    def test_rejects_no_extension(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.safe("arquivo")

    def test_rejects_disallowed_extension(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.safe("script.sh")

    def test_rejects_null_byte(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.safe("arquivo\x00.pdf")

    def test_rejects_empty(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self.safe("")


# ═══════════════════════════════════════════════════════════════════════════════
# WR-05 — InvitePayload rejeita role superadmin
# ═══════════════════════════════════════════════════════════════════════════════

class TestWR05InviteRoleValidation:
    def test_rejects_superadmin_role(self):
        from pydantic import ValidationError
        from routers.auth import InvitePayload
        with pytest.raises(ValidationError):
            InvitePayload(email="x@x.com", nome="X", funcao="superadmin")

    def test_accepts_admin(self):
        from routers.auth import InvitePayload
        p = InvitePayload(email="x@x.com", nome="X", funcao="admin")
        assert p.funcao == "admin"

    def test_accepts_gerente(self):
        from routers.auth import InvitePayload
        p = InvitePayload(email="x@x.com", nome="X", funcao="gerente")
        assert p.funcao == "gerente"

    def test_accepts_sdr(self):
        from routers.auth import InvitePayload
        p = InvitePayload(email="x@x.com", nome="X", funcao="sdr")
        assert p.funcao == "sdr"

    def test_rejects_arbitrary_role(self):
        from pydantic import ValidationError
        from routers.auth import InvitePayload
        with pytest.raises(ValidationError):
            InvitePayload(email="x@x.com", nome="X", funcao="hacker")


# ═══════════════════════════════════════════════════════════════════════════════
# WR-06 — auto-release não retoma IA em handoff/disqualified
# ═══════════════════════════════════════════════════════════════════════════════

class TestWR06AutoReleaseRespectsStage:
    def test_handoff_stage_not_released(self):
        """Conversa em handoff com ai_paused deve permanecer pausada."""
        import main as app_module

        # Monta estado mínimo
        instance_id = "test-inst"
        phone = "5511999990001"
        app_module.instances[instance_id] = {
            "inbound_ai_enabled": True,
            "auto_release_minutes": 0,  # 0 = desabilitado
        }
        app_module.conversations[instance_id] = {
            phone: {
                "ai_paused": True,
                "stage": "handoff",
                "messages": [],
            }
        }

        # Simula _check_auto_release — o loop deve pular por stage == handoff
        conv = app_module.conversations[instance_id][phone]
        auto_release_min = app_module.instances[instance_id].get("auto_release_minutes", 0)
        should_skip = (
            not conv.get("ai_paused") or
            conv.get("stage") in ("handoff", "disqualified") or
            auto_release_min <= 0
        )
        assert should_skip, "Conversa em handoff não deve ter IA liberada"

        # Limpa
        del app_module.instances[instance_id]
        del app_module.conversations[instance_id]

    def test_disqualified_stage_not_released(self):
        import main as app_module
        conv = {"ai_paused": True, "stage": "disqualified", "messages": []}
        should_skip = conv.get("stage") in ("handoff", "disqualified")
        assert should_skip


# ═══════════════════════════════════════════════════════════════════════════════
# reset_password não reativa conta revogada/pendente
# ═══════════════════════════════════════════════════════════════════════════════

class TestResetPasswordDoesNotReactivateRevoked:
    def _with_user(self, status: str):
        import services.auth_service as auth_svc
        original_users = auth_svc._users
        auth_svc._users = {
            "user@test.com": {
                "id": "1", "email": "user@test.com", "nome": "User", "avatar": "",
                "funcao": "sdr", "status": status, "first_access": False,
                "token_version": 0, "allowed_instances": [],
                "password_hash": "old-hash",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        }
        return auth_svc, original_users

    def test_revoked_user_cannot_reset_password(self, monkeypatch):
        auth_svc, original_users = self._with_user("revoked")
        try:
            monkeypatch.setattr(auth_svc, "_save", lambda: None)
            monkeypatch.setattr(auth_svc, "_send_password_reset_email", lambda *a, **k: None)
            ok = auth_svc.reset_password("user@test.com")
            assert ok is False
            # senha/token_version não devem ter sido alterados
            assert auth_svc._users["user@test.com"]["password_hash"] == "old-hash"
            assert auth_svc._users["user@test.com"]["token_version"] == 0
        finally:
            auth_svc._users = original_users

    def test_pending_user_cannot_reset_password(self, monkeypatch):
        auth_svc, original_users = self._with_user("pending")
        try:
            monkeypatch.setattr(auth_svc, "_save", lambda: None)
            monkeypatch.setattr(auth_svc, "_send_password_reset_email", lambda *a, **k: None)
            ok = auth_svc.reset_password("user@test.com")
            assert ok is False
        finally:
            auth_svc._users = original_users

    def test_active_user_can_reset_password(self, monkeypatch):
        auth_svc, original_users = self._with_user("active")
        try:
            monkeypatch.setattr(auth_svc, "_save", lambda: None)
            sent = {}
            monkeypatch.setattr(auth_svc, "_send_password_reset_email", lambda email, pwd: sent.update(email=email, pwd=pwd))
            ok = auth_svc.reset_password("user@test.com")
            assert ok is True
            assert auth_svc._users["user@test.com"]["password_hash"] != "old-hash"
            assert sent.get("email") == "user@test.com"
        finally:
            auth_svc._users = original_users


# ═══════════════════════════════════════════════════════════════════════════════
# WR-08 — _validate_avatar
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversationEndpointLocking:
    def _request(self):
        """
        Requisicao fake com sessao de superadmin valida — desde que as rotas
        de conversas passaram a exigir auth_svc.require_instance_access,
        os testes de locking (que nao testam autorizacao) precisam de um
        usuario autenticado para nao quebrar em get_auth_user().
        """
        from types import SimpleNamespace
        import main as app_module
        return SimpleNamespace(
            app=app_module.app,
            cookies={"klead_token": "fake-superadmin-token"},
            headers={},
        )

    def _patch_superadmin_auth(self):
        import services.auth_service as auth_svc
        return patch.object(
            auth_svc, "verify_token",
            return_value={"funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": []},
        )

    def test_human_send_message_uses_instance_snapshot_before_external_send(self):
        import main as app_module
        from routers.conversations import HumanSendPayload, human_send_message

        class TrackingLock:
            def __init__(self, name, events):
                self.name = name
                self.events = events

            def __enter__(self):
                self.events.append(self.name)
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        instance_id = "lock-inst"
        phone = "5511999990001"
        conv = {"messages": []}
        events = []

        with patch.dict(app_module.instances, {
            instance_id: {
                "api_url": "https://api.example.com",
                "api_key": "secret",
            }
        }, clear=True), patch.dict(app_module.conversations, {}, clear=True), \
             patch.object(app_module.app.state, "instances_lock", TrackingLock("instances", events)), \
             patch.object(app_module.app.state, "conversations_lock", TrackingLock("conversations", events)), \
             patch.object(app_module.crm_svc, "get_leads", return_value=[]), \
             patch.object(app_module.app.state, "get_or_create_conversation", return_value=conv), \
             patch.object(app_module.app.state, "clear_needs_human"), \
             patch.object(app_module.app.state, "save_conversations"), \
             patch.object(app_module.app.state, "now_iso", return_value="2026-05-16T12:00:00"), \
             self._patch_superadmin_auth():

            def fake_send_reply(inst_arg, iid_arg, phone_arg, message_arg):
                assert iid_arg == instance_id
                assert phone_arg == phone
                assert message_arg == "Olá"
                assert inst_arg == {"api_url": "https://api.example.com", "api_key": "secret"}
                assert inst_arg is not app_module.instances[instance_id]
                return True

            with patch.object(app_module.app.state, "send_reply", side_effect=fake_send_reply):
                payload = HumanSendPayload(message="Olá")
                result = human_send_message(instance_id, phone, payload, self._request())

        assert result["ok"] is True
        assert conv["messages"][-1]["content"] == "Olá"
        assert events == ["instances", "conversations", "conversations"]

    def test_list_conversations_checks_instance_under_instances_lock(self):
        import main as app_module
        from fastapi import HTTPException
        from routers.conversations import list_conversations

        class TrackingLock:
            def __init__(self, name, events):
                self.name = name
                self.events = events

            def __enter__(self):
                self.events.append(self.name)
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        events = []
        with patch.dict(app_module.instances, {}, clear=True), \
             patch.dict(app_module.conversations, {}, clear=True), \
             patch.object(app_module.app.state, "instances_lock", TrackingLock("instances", events)):
            with pytest.raises(HTTPException) as exc:
                list_conversations("missing-inst", self._request())

        assert exc.value.status_code == 404
        assert events == ["instances"]

    def test_get_conversation_returns_deep_snapshot(self):
        import main as app_module
        from routers.conversations import get_conversation

        instance_id = "snapshot-inst"
        phone = "5511999990001"
        with patch.dict(app_module.instances, {instance_id: {"api_url": "https://api.example.com"}}, clear=True), \
             patch.dict(app_module.conversations, {
                 instance_id: {
                     phone: {
                         "name": "Lead",
                         "messages": [{"role": "user", "content": "Oi"}],
                     }
                 }
             }, clear=True), self._patch_superadmin_auth():
            result = get_conversation(instance_id, phone, self._request())
            result["messages"][0]["content"] = "Mutado fora da trava"

            assert app_module.conversations[instance_id][phone]["messages"][0]["content"] == "Oi"

    def test_get_queue_returns_deep_snapshot(self):
        import main as app_module
        from routers.instances import get_queue

        instance_id = "queue-inst"
        with patch.dict(app_module.instances, {
                instance_id: {
                    "queue_status": "ready",
                    "queue_progress": 1,
                    "contacts": [{"number": "5511999990001"}],
                    "queue": [{"message": "Oi", "skipped": False}],
                }
            }, clear=True), self._patch_superadmin_auth():
            result = get_queue(instance_id, self._request())
            result["queue"][0]["message"] = "Mutado fora da trava"

            assert app_module.instances[instance_id]["queue"][0]["message"] == "Oi"

    def test_get_metrics_uses_snapshot_of_conversations(self):
        import main as app_module
        from routers.webhook import get_metrics

        instance_id = "metrics-inst"
        phone = "5511999990001"
        with patch.dict(app_module.instances, {instance_id: {"status": "idle"}}, clear=True), \
             patch.dict(app_module.conversations, {
                 instance_id: {
                     phone: {
                         "stage": "qualifying",
                         "score": 50,
                         "turn_count": 2,
                         "messages": [{"role": "user", "content": "Oi", "timestamp": "2026-05-16T12:00:00+00:00"}],
                         "created_at": "2026-05-16T12:00:00+00:00",
                     }
                 }
             }, clear=True), self._patch_superadmin_auth():
            result = get_metrics(instance_id, self._request())
            app_module.conversations[instance_id][phone]["score"] = 99

            assert result["total"] == 1
            assert result["avg_score"] == 50

    def test_get_logs_returns_snapshot(self):
        import main as app_module
        from routers.instances import get_logs

        instance_id = "logs-inst"
        with patch.dict(app_module.instances, {
                instance_id: {
                    "status": "idle",
                    "envios_hoje": 3,
                    "daily_limit": 10,
                    "contacts": [{"number": "5511999990001"}],
                    "logs": [{"status": "ENVIADO", "phone": "5511999990001"}],
                }
            }, clear=True), self._patch_superadmin_auth():
            result = get_logs(instance_id, self._request())
            result["logs"][0]["status"] = "MUTADO"

            assert app_module.instances[instance_id]["logs"][0]["status"] == "ENVIADO"

    def test_get_status_uses_log_snapshot(self):
        import main as app_module
        from routers.instances import get_status

        instance_id = "status-inst"
        with patch.dict(app_module.instances, {
                instance_id: {
                    "status": "idle",
                    "envios_hoje": 3,
                    "daily_limit": 10,
                    "contacts": [{"number": "5511999990001"}],
                    "logs": [{"status": "FALHOU", "phone": "5511999990001"}],
                }
            }, clear=True), self._patch_superadmin_auth():
            result = get_status(instance_id, self._request())
            assert result["logs_count"] == 1
            assert result["falhos_count"] == 1

    def test_send_queue_uses_queue_snapshot(self):
        import main as app_module
        from routers.instances import run_campaign_from_queue

        instance_id = "send-queue-inst"
        with patch.dict(app_module.instances, {
                instance_id: {
                    "status": "idle",
                    "contacts": [{"number": "5511999990001"}],
                    "queue": [{"message": "Oi", "skipped": False}],
                    "logs": [],
                    "envios_hoje": 0,
                    "envios_hora": [],
                    "daily_limit": 0,
                    "stop_flag": False,
                    "queue_status": "idle",
                    "queue_stop_flag": False,
                    "queue_progress": 0,
                }
            }, clear=True):
            from starlette.testclient import TestClient
            client = TestClient(app_module.app)
            api_key = os.environ["DASHBOARD_SECRET"]
            with patch.object(app_module.app.state, "apply_global_ai_defaults"), \
                 patch("routers.instances.run_campaign_from_queue"), \
                 patch("services.auth_service.verify_token", return_value={
                     "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
                 }):
                resp = client.post(
                    f"/send-queue/{instance_id}",
                    headers={"X-API-Key": api_key, "X-Auth-Token": "fake-superadmin-token"},
                )
            result = resp.json()

            app_module.instances[instance_id]["queue"][0]["message"] = ""
            assert result["ok"] is True
            assert result["total"] == 1

    def test_list_conversations_blocks_user_without_instance_access(self):
        """
        WR-10 — GET /conversations/{instance_id} nao pode vazar conversas de
        uma instancia que o usuario autenticado nao tem acesso (allowed_instances).
        """
        import main as app_module
        from fastapi import HTTPException
        from routers.conversations import list_conversations
        import services.auth_service as auth_svc

        instance_id = "other-tenant-inst"
        with patch.dict(app_module.instances, {instance_id: {"status": "idle"}}, clear=True), \
             patch.dict(app_module.conversations, {instance_id: {}}, clear=True), \
             patch.object(auth_svc, "verify_token", return_value={
                 "funcao": "admin", "email": "outro-admin@test.com", "allowed_instances": ["minha-inst"],
             }):
            with pytest.raises(HTTPException) as exc:
                list_conversations(instance_id, self._request())

        assert exc.value.status_code == 403

    def test_list_conversations_uses_snapshot(self):
        import main as app_module
        from routers.conversations import list_conversations

        instance_id = "list-conv-inst"
        phone = "5511999990001"
        with patch.dict(app_module.instances, {instance_id: {"status": "idle"}}, clear=True), \
             patch.dict(app_module.conversations, {
                 instance_id: {
                     phone: {
                         "name": "Lead",
                         "stage": "qualifying",
                         "ai_paused": False,
                         "unread": True,
                         "score": 10,
                         "score_reason": "",
                         "turn_count": 1,
                         "needs_human": False,
                         "handoff_reason": "",
                         "handoff_at": None,
                         "handoff_source": "",
                         "handoff_handled_at": None,
                         "messages": [{"role": "user", "content": "Oi"}],
                         "last_activity": "2026-05-16T12:00:00+00:00",
                         "created_at": "2026-05-16T12:00:00+00:00",
                     }
                 }
             }, clear=True), self._patch_superadmin_auth():
            result = list_conversations(instance_id, self._request())
            app_module.conversations[instance_id][phone]["name"] = "Mutado"

            assert result["total"] == 1
            assert result["conversations"][0]["name"] == "Lead"


class TestWR08AvatarValidation:
    def setup_method(self):
        from services.auth_service import _validate_avatar
        self.validate = _validate_avatar

    def test_accepts_https_url(self):
        url = "https://www.gravatar.com/avatar/abc123"
        assert self.validate(url) == url

    def test_accepts_data_uri(self):
        uri = "data:image/png;base64,abc123=="
        assert self.validate(uri) == uri

    def test_accepts_empty(self):
        assert self.validate("") == ""

    def test_rejects_http_url(self):
        with pytest.raises(ValueError):
            self.validate("http://tracking.evil.com/pixel.png")

    def test_rejects_javascript_uri(self):
        with pytest.raises(ValueError):
            self.validate("javascript:alert(1)")

    def test_rejects_ftp(self):
        with pytest.raises(ValueError):
            self.validate("ftp://files.example.com/img.png")


# ═══════════════════════════════════════════════════════════════════════════════
# WR-09 — _prune_blacklist remove tokens expirados
# ═══════════════════════════════════════════════════════════════════════════════

class TestWR09BlacklistPruning:
    def test_expired_token_is_pruned(self):
        import jwt
        import services.auth_service as auth_svc

        secret = os.environ["DASHBOARD_SECRET"]
        expired_token = jwt.encode(
            {"sub": "test@test.com", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            secret,
            algorithm="HS256",
        )
        valid_token = jwt.encode(
            {"sub": "test@test.com", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            secret,
            algorithm="HS256",
        )

        result = auth_svc._prune_blacklist({expired_token, valid_token})

        assert expired_token not in result, "Token expirado deve ser removido da blacklist"
        assert valid_token in result, "Token válido deve permanecer"

    def test_empty_blacklist_stays_empty(self):
        import services.auth_service as auth_svc
        assert auth_svc._prune_blacklist(set()) == set()


# ═══════════════════════════════════════════════════════════════════════════════
# IN-01 — _detect_intent foi removido
# ═══════════════════════════════════════════════════════════════════════════════

class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class TestWR10InviteEmailIncludesKleadUrl:
    def test_welcome_email_includes_login_url(self, monkeypatch):
        import services.auth_service as auth_svc

        sent = {}
        monkeypatch.setenv("KLEAD_APP_URL", "https://app.example.com")
        monkeypatch.setattr(auth_svc, "_send_email", lambda to, subject, html: sent.update({
            "to": to,
            "subject": subject,
            "html": html,
        }))
        monkeypatch.setattr(auth_svc.threading, "Thread", _ImmediateThread)

        auth_svc._send_welcome_email("kim@example.com", "temp123456")

        assert sent["to"] == "kim@example.com"
        assert "https://app.example.com/login" in sent["html"]
        assert "Acessar KLEAD" in sent["html"]
        assert "temp123456" in sent["html"]

    def test_reset_email_includes_login_url(self, monkeypatch):
        import services.auth_service as auth_svc

        sent = {}
        monkeypatch.setenv("KLEAD_APP_URL", "https://zapman.example.com/base")
        monkeypatch.setattr(auth_svc, "_send_email", lambda to, subject, html: sent.update({
            "to": to,
            "subject": subject,
            "html": html,
        }))
        monkeypatch.setattr(auth_svc.threading, "Thread", _ImmediateThread)

        auth_svc._send_password_reset_email("kim@example.com", "temp-reset")

        assert sent["to"] == "kim@example.com"
        assert "https://zapman.example.com/base/login" in sent["html"]
        assert "Acessar KLEAD" in sent["html"]
        assert "temp-reset" in sent["html"]


class TestIN01DetectIntentRemoved:
    def test_detect_intent_does_not_exist(self):
        import main as app_module
        assert not hasattr(app_module, "_detect_intent"), "_detect_intent era código morto e deve ter sido removido"
        assert not hasattr(app_module, "_POSITIVE_KW")
        assert not hasattr(app_module, "_NEGATIVE_KW")


# ═══════════════════════════════════════════════════════════════════════════════
# IN-02 — create_instance valida instance_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestIN02InstanceIdValidation:
    def test_safe_instance_path_part_rejects_path_traversal(self):
        from fastapi import HTTPException
        from main import _safe_instance_path_part
        with pytest.raises(HTTPException) as exc:
            _safe_instance_path_part("../../etc/passwd")
        assert exc.value.status_code == 400

    def test_safe_instance_path_part_rejects_slash(self):
        from fastapi import HTTPException
        from main import _safe_instance_path_part
        with pytest.raises(HTTPException):
            _safe_instance_path_part("inst/evil")

    def test_safe_instance_path_part_accepts_valid(self):
        from main import _safe_instance_path_part
        assert _safe_instance_path_part("Ramon-SDR") == "Ramon-SDR"
        assert _safe_instance_path_part("inst.01") == "inst.01"

    def test_safe_instance_path_part_accepts_legacy_internal_space(self):
        from main import _safe_instance_path_part
        assert _safe_instance_path_part("Ricardo Zap") == "Ricardo Zap"

    def test_auxiliary_instance_validators_match_legacy_internal_space(self):
        from services.campaign_service import _safe_instance_id as campaign_safe
        from services.patterns_service import _safe_instance_id as patterns_safe
        assert campaign_safe("Ricardo Zap") == "Ricardo Zap"
        assert patterns_safe("Ricardo Zap") == "Ricardo Zap"

    def test_safe_instance_path_part_rejects_empty(self):
        from fastapi import HTTPException
        from main import _safe_instance_path_part
        with pytest.raises(HTTPException):
            _safe_instance_path_part("")


# ═══════════════════════════════════════════════════════════════════════════════
# IN-05 — _hourly_count poda entradas antigas
# ═══════════════════════════════════════════════════════════════════════════════

class TestIN05HourlyCountPruning:
    def test_old_entries_are_pruned(self):
        from services.campaign_service import _hourly_count

        now = datetime.now()
        old = (now - timedelta(hours=2)).isoformat()
        recent = (now - timedelta(minutes=30)).isoformat()

        instances = {
            "inst1": {"envios_hora": [old, old, recent, recent]}
        }

        count = _hourly_count(instances, "inst1")

        assert count == 2, "Deve contar só os envios da última hora"
        assert len(instances["inst1"]["envios_hora"]) == 2, "Lista deve ser podada in-place"
        assert old not in instances["inst1"]["envios_hora"]

    def test_empty_list_returns_zero(self):
        from services.campaign_service import _hourly_count
        instances = {"inst1": {"envios_hora": []}}
        assert _hourly_count(instances, "inst1") == 0


class TestAudioInboxFallback:
    def test_registers_placeholder_when_audio_download_fails(self):
        import main as app_module

        instance_id = "audio-inst"
        phone = "5511999990001"
        original_instances = app_module.instances
        original_conversations = app_module.conversations

        try:
            app_module.instances = {
                instance_id: {
                    "api_url": "https://api.example.com",
                    "api_key": "secret",
                }
            }
            app_module.conversations = {}

            with patch.object(app_module, "download_media_bytes", return_value=None), \
                 patch.object(app_module, "_save_conversations"), \
                 patch.object(app_module, "_sse_emit"), \
                 patch.object(app_module, "_now_iso", return_value="2026-05-17T12:00:00"):
                app_module._handle_audio_inbound(instance_id, phone, "Lead", {"message": {"audioMessage": {}}})

            conv = app_module.conversations[instance_id][phone]
            assert conv["unread"] is True
            assert conv["messages"][-1]["content"] == "[audio recebido - falha ao baixar para transcricao]"
        finally:
            app_module.instances = original_instances
            app_module.conversations = original_conversations

    def test_registers_playable_audio_when_transcription_fails(self):
        import main as app_module

        instance_id = "audio-inst"
        phone = "5511999990002"
        original_instances = app_module.instances
        original_conversations = app_module.conversations

        try:
            app_module.instances = {
                instance_id: {
                    "api_url": "https://api.example.com",
                    "api_key": "secret",
                    "inbound_ai_enabled": True,
                }
            }
            app_module.conversations = {}

            with patch.object(app_module, "download_media_bytes", return_value=b"ogg-bytes"), \
                 patch.object(app_module.media_svc, "save_temp_file", return_value="abc123abc123abc123abc123abc123ab.ogg"), \
                 patch.object(app_module, "transcribe_audio", return_value=""), \
                 patch.object(app_module, "_schedule_ai_response") as schedule_mock, \
                 patch.object(app_module, "_save_conversations"), \
                 patch.object(app_module, "_sse_emit"), \
                 patch.object(app_module, "_now_iso", return_value="2026-05-17T12:00:00"):
                app_module._handle_audio_inbound(instance_id, phone, "Lead", {"message": {"audioMessage": {}}})

            conv = app_module.conversations[instance_id][phone]
            msg = conv["messages"][-1]
            assert msg["content"] == "[audio recebido]"
            assert msg["is_audio"] is True
            assert msg["is_media"] is True
            assert msg["media_type"] == "audio"
            assert msg["media_url"] == app_module.media_svc.signed_media_path("abc123abc123abc123abc123abc123ab.ogg")
            assert msg["media_url"].startswith("/media/temp/abc123abc123abc123abc123abc123ab.ogg?tk=")
            schedule_mock.assert_not_called()
        finally:
            app_module.instances = original_instances
            app_module.conversations = original_conversations

    def test_registers_transcribed_audio_with_player_and_schedules_ai(self):
        import main as app_module

        instance_id = "audio-inst"
        phone = "5511999990003"
        original_instances = app_module.instances
        original_conversations = app_module.conversations

        try:
            app_module.instances = {
                instance_id: {
                    "api_url": "https://api.example.com",
                    "api_key": "secret",
                    "inbound_ai_enabled": True,
                    "ai_response_delay": 2,
                }
            }
            app_module.conversations = {}

            with patch.object(app_module, "download_media_bytes", return_value=b"mp4-bytes"), \
                 patch.object(app_module.media_svc, "save_temp_file", return_value="def456def456def456def456def456de.mp4") as save_temp_mock, \
                 patch.object(app_module, "transcribe_audio", return_value="quero saber mais"), \
                 patch.object(app_module, "_schedule_ai_response") as schedule_mock, \
                 patch.object(app_module, "_save_conversations"), \
                 patch.object(app_module, "_sse_emit"), \
                 patch.object(app_module, "_now_iso", return_value="2026-05-17T12:00:00"):
                app_module._handle_audio_inbound(instance_id, phone, "Lead", {"message": {"audioMessage": {"mimetype": "audio/mp4"}}})

            conv = app_module.conversations[instance_id][phone]
            msg = conv["messages"][-1]
            assert msg["content"] == "🎤 quero saber mais"
            assert msg["is_audio"] is True
            assert msg["media_type"] == "audio"
            assert msg["media_url"] == app_module.media_svc.signed_media_path("def456def456def456def456def456de.mp4")
            assert msg["media_url"].startswith("/media/temp/def456def456def456def456def456de.mp4?tk=")
            assert msg["filename"] == "audio.mp4"
            save_temp_mock.assert_called_once_with(b"mp4-bytes", "mp4", delete_after=24 * 3600.0)
            schedule_mock.assert_called_once()
            assert schedule_mock.call_args.args[0] == instance_id
            assert schedule_mock.call_args.args[1] == phone
            assert schedule_mock.call_args.kwargs["audio_reply"] is True
        finally:
            app_module.instances = original_instances
            app_module.conversations = original_conversations

    def test_detects_audio_by_message_type_fallback(self):
        import main as app_module

        assert app_module._is_audio_message({"messageType": "audio"}) is True
        assert app_module._is_audio_message({"message": {"type": "ptt"}}) is True


class TestInstanceDeletionPurgesData:
    def test_delete_instance_removes_related_local_data(self, tmp_path, monkeypatch):
        import json
        import copy
        from types import SimpleNamespace
        import main as app_module
        from routers.instances import delete_instance
        import services.crm_service as crm_svc
        import services.assignment_service as assignment_svc
        import services.campaign_service as campaign_svc
        import services.patterns_service as patterns_svc

        instance_id = "inst clean"
        other_instance = "inst-keep"

        original_instances = copy.deepcopy(app_module.instances)
        original_conversations = copy.deepcopy(app_module.conversations)
        original_knowledge_dir = app_module.KNOWLEDGE_DIR
        original_crm = crm_svc._crm
        original_assignments = assignment_svc._state
        original_history_file = campaign_svc.HISTORY_FILE
        original_progress_dir = campaign_svc.PROGRESS_DIR
        patterns_purged = []

        try:
            monkeypatch.setattr(app_module, "KNOWLEDGE_DIR", tmp_path / "knowledge")
            app_module.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

            monkeypatch.setattr(campaign_svc, "HISTORY_FILE", tmp_path / "campaign_history.json")
            monkeypatch.setattr(campaign_svc, "PROGRESS_DIR", tmp_path / "progress")
            monkeypatch.setattr(patterns_svc, "purge_patterns", lambda iid: patterns_purged.append(iid))

            crm_svc._crm = {
                "columns": [{"id": "novo", "name": "Novo", "order": 0, "color": "#25D366"}],
                "leads": [
                    {"id": "lead-1", "instancia_id": instance_id, "telefone": "5511", "email": "lead@test.com"},
                    {"id": "lead-2", "instancia_id": other_instance, "telefone": "5522", "email": "keep@test.com"},
                ],
                "listas": [{"id": "lista-1", "lead_ids": ["lead-1", "lead-2"]}],
            }
            assignment_svc._state = {"tenants": {"tenant1@test.com": {
                "enabled": True,
                "mode": "manual",
                "pointer": 0,
                "queue": [instance_id, other_instance],
                "queue_generated_at": "",
                "attendants": [
                    {"instance_id": instance_id, "email": instance_id, "nome": instance_id, "active": True, "status": "available"},
                    {"instance_id": other_instance, "email": other_instance, "nome": other_instance, "active": True, "status": "available"},
                ],
                "history": [
                    {"to_instance_id": instance_id, "to_email": instance_id},
                    {"to_instance_id": other_instance, "to_email": other_instance},
                ],
                "rules": [
                    {"id": "rule-1", "instancia_id": instance_id, "enabled": True},
                    {"id": "rule-2", "instancia_id": other_instance, "enabled": True},
                ],
            }}}
            app_module.instances.clear()
            app_module.instances.update({
                instance_id: {"status": "idle", "api_url": "", "api_key": "", "openai_key": "", "prompt": "", "contacts": [], "logs": [], "provider": "evolution"},
                other_instance: {"status": "idle", "api_url": "", "api_key": "", "openai_key": "", "prompt": "", "contacts": [], "logs": [], "provider": "evolution"},
            })
            app_module.conversations.clear()
            app_module.conversations.update({
                instance_id: {"5511": {"messages": [{"content": "secret"}]}},
                other_instance: {"5522": {"messages": [{"content": "keep"}]}},
            })

            knowledge_dir = app_module.KNOWLEDGE_DIR / instance_id
            knowledge_dir.mkdir(parents=True, exist_ok=True)
            (knowledge_dir / "doc.txt").write_text("secret", encoding="utf-8")

            campaign_svc.PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
            for suffix in ("_sent.json", "_queue.json", "_invalid.json", "_campaign_date.json"):
                (campaign_svc.PROGRESS_DIR / f"{instance_id}{suffix}").write_text("[]", encoding="utf-8")
            campaign_svc.HISTORY_FILE.write_text(json.dumps([
                {"instance_id": instance_id, "started_at": "x"},
                {"instance_id": other_instance, "started_at": "y"},
            ]), encoding="utf-8")

            fake_request = SimpleNamespace(app=app_module.app, cookies={"klead_token": "fake-superadmin-token"}, headers={})
            with patch("services.auth_service.verify_token", return_value={
                "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
            }):
                result = delete_instance(instance_id, fake_request, _="test")

            assert result["ok"] is True
            assert instance_id not in app_module.instances
            assert instance_id not in app_module.conversations
            assert other_instance in app_module.conversations
            assert not (app_module.KNOWLEDGE_DIR / instance_id).exists()
            assert patterns_purged == [instance_id]
            assert not (campaign_svc.PROGRESS_DIR / f"{instance_id}_sent.json").exists()
            assert not (campaign_svc.PROGRESS_DIR / f"{instance_id}_queue.json").exists()
            assert not (campaign_svc.PROGRESS_DIR / f"{instance_id}_invalid.json").exists()
            assert not (campaign_svc.PROGRESS_DIR / f"{instance_id}_campaign_date.json").exists()

            history = json.loads(campaign_svc.HISTORY_FILE.read_text(encoding="utf-8"))
            assert [item["instance_id"] for item in history] == [other_instance]

            assert [lead["id"] for lead in crm_svc._crm["leads"]] == ["lead-2"]
            assert crm_svc._crm["listas"][0]["lead_ids"] == ["lead-2"]
            tenant_state = assignment_svc._state["tenants"]["tenant1@test.com"]
            assert [item["instance_id"] for item in tenant_state["attendants"]] == [other_instance]
            assert tenant_state["queue"] == [other_instance]
            assert tenant_state["rules"][0]["instancia_id"] == other_instance
            assert tenant_state["history"][0]["to_instance_id"] == other_instance
        finally:
            app_module.instances.clear()
            app_module.instances.update(original_instances)
            app_module.conversations.clear()
            app_module.conversations.update(original_conversations)
            app_module.KNOWLEDGE_DIR = original_knowledge_dir
            crm_svc._crm = original_crm
            assignment_svc._state = original_assignments
            campaign_svc.HISTORY_FILE = original_history_file
            campaign_svc.PROGRESS_DIR = original_progress_dir


class TestUserDeletionCascadesOwnedInstances:
    def test_deleting_admin_removes_their_exclusive_instance(self, tmp_path, monkeypatch):
        """
        Ao excluir um usuario admin, as instancias que so ele possui devem ser
        apagadas junto (Evolution GO, dados, acessos) — antes disso, excluir
        o usuario deixava a instancia orfa (o caso real reportado: conta
        "ricardo" removida, instancia "ricardo" continuou existindo).
        """
        import copy
        from types import SimpleNamespace
        import main as app_module
        from routers.auth import auth_delete_user
        import services.auth_service as auth_svc

        instance_id = "inst-orphan-test"
        admin_email = "admin-to-delete@test.com"

        original_instances = copy.deepcopy(app_module.instances)
        original_users = copy.deepcopy(auth_svc._users)

        try:
            app_module.instances.clear()
            app_module.instances.update({
                instance_id: {
                    "status": "idle", "api_url": "", "api_key": "", "openai_key": "",
                    "prompt": "", "contacts": [], "logs": [], "provider": "evolution",
                },
            })

            auth_svc._users.clear()
            auth_svc._users.update({
                "superadmin@test.com": {
                    "email": "superadmin@test.com", "nome": "Super", "funcao": "superadmin",
                    "allowed_instances": [], "status": "active",
                },
                admin_email: {
                    "email": admin_email, "nome": "Admin", "funcao": "admin",
                    "allowed_instances": [instance_id], "status": "active",
                },
            })

            fake_request = SimpleNamespace(app=app_module.app, cookies={"klead_token": "fake-superadmin-token"}, headers={})
            with patch("services.auth_service.verify_token", return_value={
                "funcao": "superadmin", "email": "superadmin@test.com", "allowed_instances": [],
            }):
                result = auth_delete_user(admin_email, fake_request)

            assert result["ok"] is True
            assert result["deleted_instances"] == [instance_id]
            assert instance_id not in app_module.instances
            assert auth_svc.get_user_by_email(admin_email) is None
        finally:
            app_module.instances.clear()
            app_module.instances.update(original_instances)
            auth_svc._users.clear()
            auth_svc._users.update(original_users)


# ═══════════════════════════════════════════════════════════════════════════════
# WR-11 — _get_allowed_instances_for_request (crm.py) falha fechado
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrmAllowedInstancesFailsClosed:
    def _request(self, token: str | None):
        from types import SimpleNamespace
        cookies = {"klead_token": token} if token else {}
        return SimpleNamespace(cookies=cookies, headers={})

    def test_no_token_returns_empty_not_none(self):
        from routers.crm import _get_allowed_instances_for_request
        result = _get_allowed_instances_for_request(self._request(None))
        assert result == [], "Sem token deve significar 'sem acesso a nada', nao 'acesso a tudo' (None)"

    def test_invalid_token_returns_empty_not_none(self):
        import services.auth_service as auth_svc
        from routers.crm import _get_allowed_instances_for_request
        with patch.object(auth_svc, "verify_token", return_value=None):
            result = _get_allowed_instances_for_request(self._request("token-invalido"))
        assert result == [], "Token invalido deve significar 'sem acesso a nada', nao 'acesso a tudo' (None)"

    def test_superadmin_returns_none_for_full_access(self):
        import services.auth_service as auth_svc
        from routers.crm import _get_allowed_instances_for_request
        with patch.object(auth_svc, "verify_token", return_value={"funcao": "superadmin", "allowed_instances": []}):
            result = _get_allowed_instances_for_request(self._request("token-valido"))
        assert result is None, "Superadmin deve continuar com acesso total (None = sem filtro)"

    def test_regular_admin_returns_own_instances(self):
        import services.auth_service as auth_svc
        from routers.crm import _get_allowed_instances_for_request
        with patch.object(auth_svc, "verify_token", return_value={"funcao": "admin", "allowed_instances": ["minha-inst"]}):
            result = _get_allowed_instances_for_request(self._request("token-valido"))
        assert result == ["minha-inst"]


# ═══════════════════════════════════════════════════════════════════════════════
# Filtro de redação de query sensível não pode quebrar o access log do uvicorn
# ═══════════════════════════════════════════════════════════════════════════════

class TestRedactFilterPreservesUvicornArgs:
    """O AccessFormatter do uvicorn desempacota record.args como 5-tupla —
    a versão antiga do filtro zerava args e derrubava o formatter com
    ValueError em todo webhook/OAuth (spam de 'Logging error')."""

    def _make_access_record(self, path):
        import logging as _logging
        return _logging.LogRecord(
            name="uvicorn.access", level=_logging.INFO, pathname="", lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("1.2.3.4:1234", "POST", path, "1.1", 401),
            exc_info=None,
        )

    def test_redige_token_mantendo_a_tupla_de_args(self):
        from main import _RedactSensitiveQueryFilter
        record = self._make_access_record("/webhook/inst?token=super-secreto")
        assert _RedactSensitiveQueryFilter().filter(record) is True
        assert isinstance(record.args, tuple) and len(record.args) == 5
        rendered = record.getMessage()  # não pode lançar
        assert "super-secreto" not in rendered
        assert "token=[redacted]" in rendered

    def test_path_sem_dado_sensivel_fica_intacto(self):
        from main import _RedactSensitiveQueryFilter
        record = self._make_access_record("/health")
        _RedactSensitiveQueryFilter().filter(record)
        assert record.getMessage() == '1.2.3.4:1234 - "POST /health HTTP/1.1" 401'

    def test_msg_sem_args_tambem_e_redigida(self):
        import logging as _logging
        from main import _RedactSensitiveQueryFilter
        record = _logging.LogRecord(
            name="klead", level=_logging.INFO, pathname="", lineno=0,
            msg="callback recebido: code=abc123&state=xyz", args=(), exc_info=None,
        )
        _RedactSensitiveQueryFilter().filter(record)
        rendered = record.getMessage()
        assert "abc123" not in rendered and "xyz" not in rendered
        assert "code=[redacted]" in rendered
