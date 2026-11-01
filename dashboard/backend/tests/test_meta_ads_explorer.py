"""
Testes do explorador de Meta Ads — _resolve_ads_account_and_token.

Cobre o achado da auditoria: os endpoints do explorador (/metrics/meta-ads/*
/explorer, /ads, /dashboard/*/analyze-meta-ads, /dashboard/*/chat-meta-ads)
aceitavam um ad_account arbitrário do cliente. Sem conexão OAuth própria, o
fallback era o meta_token manual da instância — um token de sistema/agência
pode enxergar contas de OUTROS clientes no mesmo Business Manager. Sem travar
isso, um usuário desta instância poderia pedir o ad_account de outro tenant.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))

import routers.webhook as webhook_module


def _request(decrypted_oauth_token: str = ""):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                decrypt_field_for_user=lambda email, enc: decrypted_oauth_token,
            )
        )
    )


class TestResolveAdsAccountAndToken:
    def test_sem_oauth_pedindo_conta_diferente_da_propria_e_bloqueado(self):
        inst = {"meta_ad_account_id": "act_111", "meta_token": "manual-token-inst-a"}
        with patch.object(webhook_module.meta_oauth_pg, "get_connection_for_instance", return_value=None):
            with pytest.raises(HTTPException) as exc:
                webhook_module._resolve_ads_account_and_token(_request(), "user@a.com", inst, "act_999")
        assert exc.value.status_code == 403

    def test_sem_oauth_pedindo_a_propria_conta_funciona(self):
        inst = {"meta_ad_account_id": "act_111", "meta_token": "manual-token-inst-a"}
        with patch.object(webhook_module.meta_oauth_pg, "get_connection_for_instance", return_value=None):
            account, token = webhook_module._resolve_ads_account_and_token(_request(), "user@a.com", inst, "act_111")
        assert account == "act_111"
        assert token == "manual-token-inst-a"

    def test_sem_oauth_sem_conta_pedida_usa_a_propria(self):
        inst = {"meta_ad_account_id": "act_111", "meta_token": "manual-token-inst-a"}
        with patch.object(webhook_module.meta_oauth_pg, "get_connection_for_instance", return_value=None):
            account, token = webhook_module._resolve_ads_account_and_token(_request(), "user@a.com", inst, "")
        assert account == "act_111"
        assert token == "manual-token-inst-a"

    def test_com_oauth_pode_explorar_conta_diferente(self):
        """Com conexão OAuth própria, a Meta é quem limita — o usuário só enxerga
        contas que o token dele de fato tem acesso."""
        inst = {"meta_ad_account_id": "act_111", "meta_token": "manual-token-inst-a"}
        with patch.object(webhook_module.meta_oauth_pg, "get_connection_for_instance",
                          return_value={"user_email": "user@a.com", "access_token_encrypted": "enc"}):
            account, token = webhook_module._resolve_ads_account_and_token(
                _request(decrypted_oauth_token="oauth-token-do-usuario"), "user@a.com", inst, "act_999"
            )
        assert account == "act_999"
        assert token == "oauth-token-do-usuario"

    def test_com_oauth_sem_conta_pedida_usa_a_propria_da_instancia(self):
        inst = {"meta_ad_account_id": "act_111", "meta_token": "manual-token-inst-a"}
        with patch.object(webhook_module.meta_oauth_pg, "get_connection_for_instance",
                          return_value={"user_email": "user@a.com", "access_token_encrypted": "enc"}):
            account, token = webhook_module._resolve_ads_account_and_token(
                _request(decrypted_oauth_token="oauth-token-do-usuario"), "user@a.com", inst, ""
            )
        assert account == "act_111"
        assert token == "oauth-token-do-usuario"
