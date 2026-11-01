"""
Testes da verificação de e-mail por link e do campo telefone no perfil.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))

import services.auth_service as auth_svc


@pytest.fixture
def user_in_memory():
    """Usuário de teste direto no dicionário em memória, removido ao final."""
    email = "verify-test@test.com"
    auth_svc._users[email] = {
        "id": "test-id",
        "email": email,
        "password_hash": "$2b$12$fakehash",
        "nome": "Teste",
        "avatar": "",
        "telefone": "",
        "email_verified": False,
        "funcao": "admin",
        "status": "active",
        "token_version": 0,
    }
    yield email
    auth_svc._users.pop(email, None)


class TestEmailVerificationToken:
    def test_token_valido_marca_verificado(self, user_in_memory):
        token = auth_svc.create_email_verification_token(user_in_memory)
        with patch.object(auth_svc, "_save"):
            user = auth_svc.confirm_email_verification(token)
        assert user is not None
        assert user["email_verified"] is True

    def test_token_de_sessao_nao_serve_como_verificacao(self, user_in_memory):
        """JWT de login não tem purpose=verify_email — não pode verificar e-mail."""
        session_token = auth_svc.create_token(user_in_memory)
        assert auth_svc.confirm_email_verification(session_token) is None

    def test_token_invalido_rejeitado(self):
        assert auth_svc.confirm_email_verification("lixo.token.invalido") is None
        assert auth_svc.confirm_email_verification("") is None

    def test_usuario_inexistente_rejeitado(self):
        token = auth_svc.create_email_verification_token("nao-existe@test.com")
        assert auth_svc.confirm_email_verification(token) is None

    def test_reenvio_bloqueado_quando_ja_verificado(self, user_in_memory):
        auth_svc._users[user_in_memory]["email_verified"] = True
        assert auth_svc.send_verification_email(user_in_memory) is False


class TestSafeUserDefaults:
    def test_legado_com_senha_e_verificado(self):
        safe = auth_svc._safe_user({"email": "x@x.com", "password_hash": "$2b$12$hash"})
        assert safe["email_verified"] is True
        assert safe["telefone"] == ""
        assert "password_hash" not in safe

    def test_pendente_sem_senha_nao_verificado(self):
        safe = auth_svc._safe_user({"email": "x@x.com", "password_hash": ""})
        assert safe["email_verified"] is False


class TestMagicLogin:
    def test_token_valido_loga_e_verifica_email(self, user_in_memory):
        token = auth_svc.create_magic_login_token(user_in_memory)
        with patch.object(auth_svc, "_save"), patch.object(auth_svc, "_save_blacklist"):
            user = auth_svc.consume_magic_login_token(token)
        assert user is not None
        assert user["email"] == user_in_memory
        assert user["email_verified"] is True  # clicar no link prova posse da caixa

    def test_uso_unico(self, user_in_memory):
        token = auth_svc.create_magic_login_token(user_in_memory)
        with patch.object(auth_svc, "_save"), patch.object(auth_svc, "_save_blacklist"):
            assert auth_svc.consume_magic_login_token(token) is not None
            assert auth_svc.consume_magic_login_token(token) is None, "segundo uso deve ser rejeitado"

    def test_magic_token_nao_vale_como_sessao(self, user_in_memory):
        """Endurecimento: token com purpose não passa no verify_token de sessão."""
        token = auth_svc.create_magic_login_token(user_in_memory)
        assert auth_svc.verify_token(token) is None

    def test_token_de_sessao_nao_vale_como_magic(self, user_in_memory):
        session_token = auth_svc.create_token(user_in_memory)
        assert auth_svc.consume_magic_login_token(session_token) is None

    def test_troca_de_senha_invalida_link_pendente(self, user_in_memory):
        token = auth_svc.create_magic_login_token(user_in_memory)
        auth_svc._users[user_in_memory]["token_version"] += 1  # simula troca/reset de senha
        with patch.object(auth_svc, "_save"), patch.object(auth_svc, "_save_blacklist"):
            assert auth_svc.consume_magic_login_token(token) is None

    def test_conta_inativa_rejeitada(self, user_in_memory):
        token = auth_svc.create_magic_login_token(user_in_memory)
        auth_svc._users[user_in_memory]["status"] = "revoked"
        with patch.object(auth_svc, "_save"), patch.object(auth_svc, "_save_blacklist"):
            assert auth_svc.consume_magic_login_token(token) is None

    def test_solicitacao_nao_envia_para_inexistente_ou_inativo(self):
        assert auth_svc.request_magic_login("nao-existe@test.com") is False

    def test_cooldown_entre_envios(self, user_in_memory):
        with patch.object(auth_svc, "_send_email"), patch.dict(os.environ, {"KLEAD_APP_URL": "https://app.test"}):
            auth_svc._magic_last_send.pop(user_in_memory, None)
            assert auth_svc.request_magic_login(user_in_memory) is True
            assert auth_svc.request_magic_login(user_in_memory) is False, "reenvio em <60s deve ser bloqueado"
        auth_svc._magic_last_send.pop(user_in_memory, None)


class TestProfileTelefone:
    def test_telefone_valido_normalizado(self, user_in_memory):
        with patch.object(auth_svc, "_save"):
            user = auth_svc.update_profile(user_in_memory, telefone="(11) 91234-5678")
        assert user["telefone"] == "5511912345678"

    def test_telefone_invalido_rejeitado(self, user_in_memory):
        with pytest.raises(ValueError):
            auth_svc.update_profile(user_in_memory, telefone="12345")

    def test_telefone_vazio_limpa(self, user_in_memory):
        auth_svc._users[user_in_memory]["telefone"] = "5511912345678"
        with patch.object(auth_svc, "_save"):
            user = auth_svc.update_profile(user_in_memory, telefone="")
        assert user["telefone"] == ""
