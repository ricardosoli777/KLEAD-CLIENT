"""
Gating de features por plano no auth_service: effective_plan_for_user e
require_feature. Cobre a herança de plano (time herda do dono) e o bypass
do superadmin.
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

import services.auth_service as auth


@pytest.fixture
def users_fixture():
    original = auth._users
    auth._users = {
        "master@klead.com":   {"email": "master@klead.com", "funcao": "superadmin", "status": "active", "allowed_instances": ["m1"]},
        "clientefull@x.com":  {"email": "clientefull@x.com", "funcao": "admin", "status": "active", "plano": "pro", "allowed_instances": ["f1", "f2"]},
        "clientelite@x.com":  {"email": "clientelite@x.com", "funcao": "admin", "status": "active", "plano": "lite", "allowed_instances": ["l1"]},
        "sdr_full@x.com":     {"email": "sdr_full@x.com", "funcao": "sdr", "status": "active", "allowed_instances": ["f1"]},
        "sdr_lite@x.com":     {"email": "sdr_lite@x.com", "funcao": "sdr", "status": "active", "allowed_instances": ["l1"]},
    }
    yield
    auth._users = original


class TestEffectivePlan:
    def test_superadmin_sem_restricao(self, users_fixture):
        assert auth.effective_plan_for_user(auth._users["master@klead.com"]) == ""

    def test_admin_usa_proprio_plano(self, users_fixture):
        assert auth.effective_plan_for_user(auth._users["clientefull@x.com"]) == "pro"
        assert auth.effective_plan_for_user(auth._users["clientelite@x.com"]) == "lite"

    def test_sdr_herda_plano_do_dono(self, users_fixture):
        assert auth.effective_plan_for_user(auth._users["sdr_full@x.com"]) == "pro"
        assert auth.effective_plan_for_user(auth._users["sdr_lite@x.com"]) == "lite"


class TestRequireFeature:
    def _req(self, email):
        # get_auth_user é mockado; o request só precisa existir.
        return SimpleNamespace(cookies={}, headers={})

    def test_superadmin_passa_em_tudo(self, users_fixture):
        with patch.object(auth, "get_auth_user", return_value=(auth._users["master@klead.com"], "t")):
            assert auth.require_feature(self._req("m"), "disparador")

    def test_cliente_full_passa(self, users_fixture):
        with patch.object(auth, "get_auth_user", return_value=(auth._users["clientefull@x.com"], "t")):
            for feat in ("disparador", "roleta", "sdr_autonomo", "supervisao"):
                assert auth.require_feature(self._req("f"), feat)

    def test_cliente_lite_bloqueado_nas_features_avancadas(self, users_fixture):
        with patch.object(auth, "get_auth_user", return_value=(auth._users["clientelite@x.com"], "t")):
            for feat in ("disparador", "roleta", "sdr_autonomo", "supervisao"):
                with pytest.raises(HTTPException) as exc:
                    auth.require_feature(self._req("l"), feat)
                assert exc.value.status_code == 403

    def test_sdr_do_lite_tambem_bloqueado(self, users_fixture):
        with patch.object(auth, "get_auth_user", return_value=(auth._users["sdr_lite@x.com"], "t")):
            with pytest.raises(HTTPException) as exc:
                auth.require_feature(self._req("sl"), "disparador")
            assert exc.value.status_code == 403

    def test_lite_passa_no_nucleo(self, users_fixture):
        with patch.object(auth, "get_auth_user", return_value=(auth._users["clientelite@x.com"], "t")):
            # feature de núcleo (não gated) nunca bloqueia, mesmo no lite
            assert auth.require_feature(self._req("l"), "inbox")
