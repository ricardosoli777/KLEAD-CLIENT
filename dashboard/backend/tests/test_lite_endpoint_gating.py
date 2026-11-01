"""
Fronteira REAL do plano Lite: os endpoints de disparador e roleta respondem 403
para um usuário lite, e passam para um usuário de plano completo. Não basta
esconder na UI — um lite chamando a API direto tem que ser barrado.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)
sys.path.insert(0, str(Path(__file__).parent.parent))

import main as m
import services.auth_service as auth

API_KEY = os.environ["DASHBOARD_SECRET"]


def _client():
    from fastapi.testclient import TestClient
    return TestClient(m.app)


def _user(plano):
    return {"email": f"{plano}@x.com", "funcao": "admin", "status": "active",
            "plano": plano, "allowed_instances": ["gate-inst"]}


@pytest.fixture(autouse=True)
def _instance_and_users():
    orig_inst = dict(m.instances)
    orig_users = dict(auth._users)
    m.instances.clear()
    m.instances["gate-inst"] = {
        "status": "idle", "api_url": "", "api_key": "", "openai_key": "",
        "prompt": "", "contacts": [], "logs": [], "provider": "evolution",
        "queue_status": "idle", "queue_progress": 0, "queue": [],
    }
    yield
    m.instances.clear(); m.instances.update(orig_inst)
    auth._users.clear(); auth._users.update(orig_users)


class TestRoletaGating:
    def test_lite_bloqueado(self):
        client = _client()
        with patch.object(auth, "verify_token", return_value=_user("lite")):
            r = client.get("/assignments/settings", headers={"X-Auth-Token": "t"})
        assert r.status_code == 403

    def test_full_passa(self):
        client = _client()
        with patch.object(auth, "verify_token", return_value=_user("pro")):
            r = client.get("/assignments/settings", headers={"X-Auth-Token": "t"})
        assert r.status_code != 403


class TestDisparadorGating:
    def test_lite_bloqueado_em_contatos(self):
        client = _client()
        with patch.object(auth, "verify_token", return_value=_user("lite")):
            r = client.get("/contacts/gate-inst", headers={"X-API-Key": API_KEY, "X-Auth-Token": "t"})
        assert r.status_code == 403

    def test_full_passa_em_contatos(self):
        client = _client()
        with patch.object(auth, "verify_token", return_value=_user("pro")):
            r = client.get("/contacts/gate-inst", headers={"X-API-Key": API_KEY, "X-Auth-Token": "t"})
        assert r.status_code != 403

    def test_lite_bloqueado_no_queue(self):
        client = _client()
        with patch.object(auth, "verify_token", return_value=_user("lite")):
            r = client.get("/queue/gate-inst", headers={"X-API-Key": API_KEY, "X-Auth-Token": "t"})
        assert r.status_code == 403


class TestSdrAutonomoGating:
    def test_lite_nao_dispara_ia_automatica(self):
        # instance_plan_allows resolve o dono da instância pelo _users.
        auth._users.clear()
        auth._users["lite@x.com"] = _user("lite")
        assert auth.instance_plan_allows("gate-inst", "sdr_autonomo") is False

    def test_full_dispara_ia_automatica(self):
        auth._users.clear()
        auth._users["pro@x.com"] = _user("pro")
        assert auth.instance_plan_allows("gate-inst", "sdr_autonomo") is True
