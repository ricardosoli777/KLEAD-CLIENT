"""
Testes do webhook do Stripe (/billing/webhook).

Cobre a correção do bug: checkout.session.completed ativava o assinante
sem checar payment_status. Métodos de pagamento assíncronos (boleto, débito)
completam o checkout ANTES da confirmação real do pagamento — sem essa
checagem, alguém que só gerasse o boleto (sem nunca pagar) ganhava acesso.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_" + "c" * 32)

sys.path.insert(0, str(Path(__file__).parent.parent))


def _session_object(payment_status: str, email="cliente@example.com", plano="pro") -> dict:
    return {
        "payment_status": payment_status,
        "customer_email": email,
        "customer": "cus_123",
        "subscription": "sub_123",
        "metadata": {"email": email, "nome": "Cliente Teste", "plano": plano},
    }


def _event(event_type: str, session_obj: dict) -> dict:
    return {"type": event_type, "data": {"object": session_obj}}


class TestBillingWebhookPaymentStatus:
    def _client(self):
        from fastapi.testclient import TestClient
        import main as m
        return TestClient(m.app)

    def test_assinatura_invalida_retorna_403(self):
        client = self._client()
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=False):
            res = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "bad"})
        assert res.status_code == 403

    def test_checkout_completed_com_payment_status_unpaid_nao_ativa(self):
        """Boleto/débito: checkout.session.completed chega com payment_status='unpaid'
        antes da confirmação real — não pode ativar o assinante nesse momento."""
        client = self._client()
        event = _event("checkout.session.completed", _session_object("unpaid"))
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber") as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        assert res.json().get("skipped") == "payment_not_confirmed"
        mock_create.assert_not_called()

    def test_checkout_completed_com_payment_status_paid_ativa(self):
        """Cartão: payment_status já vem 'paid' no completed — ativa direto."""
        client = self._client()
        event = _event("checkout.session.completed", _session_object("paid"))
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber", return_value={"email": "cliente@example.com"}) as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_create.assert_called_once()
        args = mock_create.call_args[0]
        assert args[0] == "cliente@example.com"
        assert args[2] == "pro"

    def test_async_payment_succeeded_com_paid_ativa(self):
        """Confirmação tardia do boleto: async_payment_succeeded com payment_status='paid' ativa."""
        client = self._client()
        event = _event("checkout.session.async_payment_succeeded", _session_object("paid"))
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber", return_value={"email": "cliente@example.com"}) as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_create.assert_called_once()

    def test_async_payment_failed_nao_ativa(self):
        """Boleto expirou/falhou: nunca deve ativar, mesmo que metadata esteja completo."""
        client = self._client()
        event = _event("checkout.session.async_payment_failed", _session_object("unpaid"))
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber") as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_create.assert_not_called()

    def test_checkout_completed_sem_metadata_e_ignorado(self):
        client = self._client()
        session_obj = _session_object("paid")
        session_obj["metadata"] = {}
        session_obj["customer_email"] = ""
        event = _event("checkout.session.completed", session_obj)
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber") as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        assert res.json().get("skipped") is True
        mock_create.assert_not_called()

    def test_evento_desconhecido_nao_chama_create_subscriber(self):
        client = self._client()
        event = _event("some.unhandled.event", {})
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber") as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_create.assert_not_called()

    def test_subscription_deleted_revoga_acesso(self):
        """Cliente cancela no Stripe -> customer.subscription.deleted precisa
        revogar o acesso, senão o cliente continua usando de graça."""
        client = self._client()
        event = {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_123"}}}
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.revoke_by_stripe_subscription", return_value=True) as mock_revoke:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_revoke.assert_called_once_with("sub_123")

    def test_evento_reentregue_e_ignorado(self):
        """Stripe pode reentregar o mesmo evento (retry) — a segunda entrega
        não pode reativar/resetar a senha do assinante de novo."""
        client = self._client()
        event = _event("checkout.session.completed", _session_object("paid"))
        event["id"] = "evt_dedupe_test_123"
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber", return_value={"email": "cliente@example.com"}) as mock_create:
            res1 = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})
            res2 = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res1.status_code == 200 and res2.status_code == 200
        assert res2.json().get("skipped") == "duplicate_event"
        mock_create.assert_called_once()


class TestBillingLiteCiclos:
    """Plano Lite: mensal recorrente (padrão) + ciclos alternativos, incluindo
    a compra avulsa de 30 dias."""

    def _client(self):
        from fastapi.testclient import TestClient
        import main as m
        return TestClient(m.app)

    # ── Checkout ──────────────────────────────────────────────────────────────

    def test_checkout_lite_sem_ciclo_usa_mensal_recorrente(self):
        """Lite sem ciclo é válido: cai no price mensal recorrente padrão."""
        client = self._client()
        with patch("routers.billing.stripe_svc.create_checkout_session",
                   return_value={"ok": True, "url": "https://checkout.stripe.com/x"}) as mock_cs:
            res = client.post("/billing/checkout", json={
                "nome": "Cliente Lite", "email": "lite@example.com", "plano": "lite",
            })
        assert res.status_code == 200
        assert mock_cs.call_args.kwargs["ciclo"] == ""

    def test_checkout_lite_repassa_ciclo_ao_stripe(self):
        client = self._client()
        with patch("routers.billing.stripe_svc.create_checkout_session",
                   return_value={"ok": True, "url": "https://checkout.stripe.com/x"}) as mock_cs:
            res = client.post("/billing/checkout", json={
                "nome": "Cliente Lite", "email": "lite@example.com",
                "plano": "lite", "ciclo": "mensal_avulso",
            })
        assert res.status_code == 200
        assert mock_cs.call_args.kwargs["ciclo"] == "mensal_avulso"

    def test_checkout_plano_sem_ciclo_ignora_ciclo_enviado(self):
        """Ciclo só existe no Corretor — pro/user/enterprise seguem sem ciclo."""
        client = self._client()
        with patch("routers.billing.stripe_svc.create_checkout_session",
                   return_value={"ok": True, "url": "https://checkout.stripe.com/x"}) as mock_cs:
            res = client.post("/billing/checkout", json={
                "nome": "Cliente", "email": "cliente@example.com",
                "plano": "pro", "ciclo": "anual",
            })
        assert res.status_code == 200
        assert mock_cs.call_args.kwargs["ciclo"] == ""

    # ── Webhook ───────────────────────────────────────────────────────────────

    def test_webhook_avulso_ativa_com_30_dias(self):
        """mensal_avulso pago → ativa com acesso_dias=30 (expira no Klead)."""
        client = self._client()
        session_obj = _session_object("paid", email="lite@example.com", plano="lite")
        session_obj["metadata"]["ciclo"] = "mensal_avulso"
        session_obj["subscription"] = None  # pagamento único não gera subscription
        event = _event("checkout.session.completed", session_obj)
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber",
                   return_value={"email": "lite@example.com"}) as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["acesso_dias"] == 30
        assert mock_create.call_args[0][2] == "lite"

    def test_webhook_lite_recorrente_sem_expiracao(self):
        """Ciclo recorrente (semestral) → acesso_dias=None, sem expiração no Klead."""
        client = self._client()
        session_obj = _session_object("paid", email="lite@example.com", plano="lite")
        session_obj["metadata"]["ciclo"] = "semestral"
        event = _event("checkout.session.completed", session_obj)
        with patch("routers.billing.stripe_svc.verify_webhook_signature", return_value=True), \
             patch("routers.billing.auth_svc.create_paid_subscriber",
                   return_value={"email": "lite@example.com"}) as mock_create:
            res = client.post("/billing/webhook", content=json.dumps(event).encode(), headers={"stripe-signature": "ok"})

        assert res.status_code == 200
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["acesso_dias"] is None


class TestStripeCheckoutMode:
    """create_checkout_session: avulso vira mode=payment; recorrente, subscription."""

    def _capture_post(self, price_key: str, ciclo: str):
        import services.stripe_service as stripe_svc
        captured = {}

        def fake_post(url, data=None, auth=None, timeout=None):
            captured.update(data)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"url": "https://checkout.stripe.com/x", "id": "cs_123"}
            return resp

        with patch.dict(stripe_svc.PRICE_IDS, {price_key: "price_test_123"}), \
             patch.object(stripe_svc.os, "environ", {**os.environ, "STRIPE_SECRET_KEY": "sk_test"}), \
             patch("services.stripe_service.requests.post", side_effect=fake_post):
            result = stripe_svc.create_checkout_session(
                "c@example.com", "Cliente", "lite", "https://ok", "https://cancel", ciclo=ciclo,
            )
        return result, captured

    def test_mensal_avulso_usa_mode_payment(self):
        result, data = self._capture_post("lite_mensal_avulso", "mensal_avulso")
        assert result["ok"] is True
        assert data["mode"] == "payment"
        assert data["customer_creation"] == "always"
        assert data["metadata[ciclo]"] == "mensal_avulso"
        assert "subscription_data[metadata][plano]" not in data

    def test_semestral_usa_mode_subscription(self):
        result, data = self._capture_post("lite_semestral", "semestral")
        assert result["ok"] is True
        assert data["mode"] == "subscription"
        assert data["subscription_data[metadata][ciclo]"] == "semestral"
        assert "customer_creation" not in data
