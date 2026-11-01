"""Testes da Roleta (distribuição round-robin de leads entre atendentes)."""
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

import services.assignment_service as roleta
import services.auth_service as auth


def _make_attendant(email, order, weight=1, active=True):
    return {
        "email": email, "nome": email.split("@")[0].upper(), "instance_id": "inst1",
        "active": active, "status": "available", "order": order, "weight": weight,
        "total_assigned": 0,
    }


@pytest.fixture(autouse=True)
def _no_persist():
    with patch.object(roleta, "_save", lambda: None):
        yield


def _tenant_state(attendants):
    return {
        "enabled": True, "mode": "auto", "pointer": 0, "queue": [],
        "queue_generated_at": "", "history": [], "rules": [],
        "attendants": attendants,
    }


TENANT = "tenant1@x.com"


class TestAutoAssignTenantKeyForInstance:
    """auto_assign_tenant_key_for_instance — usado pelo webhook do Meta pra decidir
    se o lead recebe a tag ROLETA no InboxSDR (sem criar lead no CRM automaticamente)."""

    def test_retorna_tenant_key_quando_roleta_habilitada(self):
        with patch.object(roleta, "_owner_email_by_instance", return_value={"inst1": TENANT}), \
             patch.object(roleta, "should_auto_assign", return_value=True):
            assert roleta.auto_assign_tenant_key_for_instance("inst1") == TENANT

    def test_retorna_vazio_quando_roleta_desabilitada(self):
        with patch.object(roleta, "_owner_email_by_instance", return_value={"inst1": TENANT}), \
             patch.object(roleta, "should_auto_assign", return_value=False):
            assert roleta.auto_assign_tenant_key_for_instance("inst1") == ""

    def test_retorna_vazio_quando_instancia_sem_dono_conhecido(self):
        with patch.object(roleta, "_owner_email_by_instance", return_value={}):
            assert roleta.auto_assign_tenant_key_for_instance("inst-desconhecida") == ""


def test_round_robin_distributes_equally_with_equal_weight():
    roleta._state = {"tenants": {TENANT: _tenant_state(
        [_make_attendant("a@x.com", 0), _make_attendant("b@x.com", 1), _make_attendant("c@x.com", 2)]
    )}}
    roleta.generate_queue(TENANT)
    picks = [roleta.assign_next(TENANT, lead_id=f"lead{i}")["assigned"]["email"] for i in range(6)]
    counts = {e: picks.count(e) for e in ("a@x.com", "b@x.com", "c@x.com")}
    assert counts == {"a@x.com": 2, "b@x.com": 2, "c@x.com": 2}


def test_inactive_attendant_never_receives_lead():
    roleta._state = {"tenants": {TENANT: _tenant_state(
        [_make_attendant("a@x.com", 0), _make_attendant("b@x.com", 1, active=False), _make_attendant("c@x.com", 2)]
    )}}
    roleta.generate_queue(TENANT)
    picks = [roleta.assign_next(TENANT, lead_id=f"lead{i}")["assigned"]["email"] for i in range(4)]
    assert "b@x.com" not in picks


def test_higher_weight_receives_more_leads():
    roleta._state = {"tenants": {TENANT: _tenant_state(
        [_make_attendant("a@x.com", 0, weight=3), _make_attendant("b@x.com", 1), _make_attendant("c@x.com", 2)]
    )}}
    roleta.generate_queue(TENANT)
    picks = [roleta.assign_next(TENANT, lead_id=f"lead{i}")["assigned"]["email"] for i in range(5)]
    counts = {e: picks.count(e) for e in ("a@x.com", "b@x.com", "c@x.com")}
    assert counts["a@x.com"] > counts["b@x.com"]
    assert counts["a@x.com"] > counts["c@x.com"]


def test_no_available_attendant_returns_none_without_crashing():
    roleta._state = {"tenants": {TENANT: _tenant_state([_make_attendant("a@x.com", 0, active=False)])}}
    roleta.generate_queue(TENANT)
    result = roleta.assign_next(TENANT, lead_id="lead-x")
    assert result["assigned"] is None
    assert result["reason"] == "no_available_attendant"


def test_different_tenants_never_share_queue_or_pointer():
    """Regressao do vazamento cross-tenant: cada tenant tem fila e ponteiro isolados."""
    tenant_a, tenant_b = "admin-a@x.com", "admin-b@x.com"
    roleta._state = {"tenants": {
        tenant_a: _tenant_state([_make_attendant("inst-a@x.com", 0)]),
        tenant_b: _tenant_state([_make_attendant("inst-b@x.com", 0)]),
    }}
    roleta.generate_queue(tenant_a)
    roleta.generate_queue(tenant_b)
    pick_a = roleta.assign_next(tenant_a, lead_id="lead-a")["assigned"]["email"]
    pick_b = roleta.assign_next(tenant_b, lead_id="lead-b")["assigned"]["email"]
    assert pick_a == "inst-a@x.com"
    assert pick_b == "inst-b@x.com"
    # Fila/atendentes de um tenant nunca aparecem no outro
    assert roleta.list_attendants(tenant_a) != roleta.list_attendants(tenant_b)
    assert {a["email"] for a in roleta.list_attendants(tenant_a)} == {"inst-a@x.com"}
    assert {a["email"] for a in roleta.list_attendants(tenant_b)} == {"inst-b@x.com"}


def test_sync_instances_never_leaks_other_owners_instance():
    """sync_instances so deve trazer instancias cujo dono e o proprio tenant_key."""
    with patch.object(roleta, "_owner_email_by_instance", return_value={
        "minha-instancia": "dono-a@x.com",
        "instancia-do-outro": "dono-b@x.com",
    }):
        roleta._state = {"tenants": {}}
        result = roleta.sync_instances("dono-a@x.com", {
            "minha-instancia": {"status": "idle"},
            "instancia-do-outro": {"status": "idle"},
        })
        ids = {a["instance_id"] for a in result}
        assert ids == {"minha-instancia"}


# ── Permissão: Roleta/Supervisão exigem gerente+, nunca SDR ──────────────────

def _fake_request():
    req = MagicMock()
    req.cookies = {"klead_token": "fake"}
    req.headers = {}
    return req


@pytest.mark.parametrize("funcao,deve_passar", [
    ("superadmin", True), ("admin", True), ("gerente", True), ("sdr", False),
])
def test_require_manager_gate_matches_roleta_supervisao_policy(funcao, deve_passar):
    with patch.object(auth, "verify_token", return_value={"funcao": funcao, "email": "x@x.com"}):
        req = _fake_request()
        if deve_passar:
            auth.require_manager(req)  # não deve lançar
        else:
            with pytest.raises(HTTPException):
                auth.require_manager(req)
