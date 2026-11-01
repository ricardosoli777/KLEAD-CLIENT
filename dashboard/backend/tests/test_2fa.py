"""
Testes do 2FA (TOTP) — setup, confirmação, verificação, backup codes,
desativação e o fluxo de login em dois passos (senha e magic link).
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))

import pyotp
import bcrypt
import services.auth_service as auth_svc


@pytest.fixture()
def user_env():
    """Um usuário ativo isolado, com _save/_send mockados."""
    original = auth_svc._users
    auth_svc._users = {
        "u@test.com": {
            "id": "1", "email": "u@test.com", "nome": "User", "avatar": "",
            "funcao": "admin", "status": "active", "first_access": False,
            "token_version": 0, "allowed_instances": [],
            "password_hash": bcrypt.hashpw(b"senha-correta", bcrypt.gensalt()).decode(),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    }
    with patch.object(auth_svc, "_save", lambda: None):
        yield auth_svc._users["u@test.com"]
    auth_svc._users = original
    auth_svc._clear_login_attempts("u@test.com")


def _enable_2fa(email="u@test.com") -> str:
    """Ativa 2FA e retorna o segredo em texto para gerar códigos nos testes."""
    setup = auth_svc.start_totp_setup(email)
    secret = setup["secret"]
    code = pyotp.TOTP(secret).now()
    auth_svc.confirm_totp_setup(email, code)
    return secret


class TestTotpSetup:
    def test_setup_gera_segredo_e_qr_mas_nao_ativa(self, user_env):
        result = auth_svc.start_totp_setup("u@test.com")
        assert result and result["secret"]
        assert result["otpauth_uri"].startswith("otpauth://totp/")
        assert result["qr_svg"].startswith("data:image/svg+xml;base64,")
        assert auth_svc.is_totp_enabled(auth_svc._users["u@test.com"]) is False

    def test_segredo_e_guardado_criptografado(self, user_env):
        auth_svc.start_totp_setup("u@test.com")
        stored = auth_svc._users["u@test.com"]["totp_secret"]
        # Não pode estar em texto puro (base32 tem 32 chars A-Z2-7)
        assert stored and not stored.isupper()

    def test_confirm_com_codigo_valido_ativa_e_retorna_backup_codes(self, user_env):
        setup = auth_svc.start_totp_setup("u@test.com")
        codes = auth_svc.confirm_totp_setup("u@test.com", pyotp.TOTP(setup["secret"]).now())
        assert codes is not None and len(codes) == 10
        assert auth_svc.is_totp_enabled(auth_svc._users["u@test.com"]) is True

    def test_confirm_com_codigo_invalido_nao_ativa(self, user_env):
        auth_svc.start_totp_setup("u@test.com")
        assert auth_svc.confirm_totp_setup("u@test.com", "000000") is None
        assert auth_svc.is_totp_enabled(auth_svc._users["u@test.com"]) is False


class TestTotpVerify:
    def test_codigo_correto_passa(self, user_env):
        secret = _enable_2fa()
        assert auth_svc.verify_totp("u@test.com", pyotp.TOTP(secret).now()) is True

    def test_codigo_errado_falha(self, user_env):
        _enable_2fa()
        assert auth_svc.verify_totp("u@test.com", "000000") is False

    def test_backup_code_funciona_uma_vez(self, user_env):
        setup = auth_svc.start_totp_setup("u@test.com")
        codes = auth_svc.confirm_totp_setup("u@test.com", pyotp.TOTP(setup["secret"]).now())
        backup = codes[0]
        assert auth_svc.verify_totp("u@test.com", backup) is True
        # Uso único: o mesmo backup code não vale de novo
        assert auth_svc.verify_totp("u@test.com", backup) is False

    def test_secret_nunca_sai_em_safe_user(self, user_env):
        _enable_2fa()
        safe = auth_svc._safe_user(auth_svc._users["u@test.com"])
        assert "totp_secret" not in safe
        assert "totp_backup_codes" not in safe
        assert safe["totp_enabled"] is True


class TestTotpDisable:
    def test_disable_exige_senha_e_codigo(self, user_env):
        secret = _enable_2fa()
        # senha errada
        assert auth_svc.disable_totp("u@test.com", "errada", pyotp.TOTP(secret).now()) is False
        # código errado
        assert auth_svc.disable_totp("u@test.com", "senha-correta", "000000") is False
        # ambos certos
        assert auth_svc.disable_totp("u@test.com", "senha-correta", pyotp.TOTP(secret).now()) is True
        assert auth_svc.is_totp_enabled(auth_svc._users["u@test.com"]) is False


class TestPendingTokenFlow:
    def test_pending_token_so_completa_com_codigo_certo(self, user_env):
        secret = _enable_2fa()
        pending = auth_svc.create_totp_pending_token("u@test.com")
        assert auth_svc.consume_totp_pending_token(pending, "000000") is None
        # novo token (o anterior pode ter sido invalidado); código correto
        pending2 = auth_svc.create_totp_pending_token("u@test.com")
        user = auth_svc.consume_totp_pending_token(pending2, pyotp.TOTP(secret).now())
        assert user is not None and user["email"] == "u@test.com"

    def test_pending_token_e_uso_unico(self, user_env):
        secret = _enable_2fa()
        pending = auth_svc.create_totp_pending_token("u@test.com")
        assert auth_svc.consume_totp_pending_token(pending, pyotp.TOTP(secret).now()) is not None
        # segundo uso do mesmo token falha mesmo com código válido
        assert auth_svc.consume_totp_pending_token(pending, pyotp.TOTP(secret).now()) is None

    def test_pending_token_nao_vale_como_sessao(self, user_env):
        _enable_2fa()
        pending = auth_svc.create_totp_pending_token("u@test.com")
        # verify_token deve rejeitar qualquer token com purpose
        assert auth_svc.verify_token(pending) is None
