"""
stripe_service.py — Integração com Stripe Checkout (assinaturas recorrentes).

Usa a API REST do Stripe direto via `requests` (sem SDK) — mesmo padrão já
usado pra Meta Graph API neste projeto.
"""

import hashlib
import hmac
import logging
import os
import time

import requests

logger = logging.getLogger("klead")

STRIPE_API_BASE = "https://api.stripe.com/v1"

PRICE_IDS = {
    # Lite tem quatro formas de cobrança — a chave "lite" pura é a mensal
    # recorrente (padrão); os ciclos alternativos viram sufixo (lite_semestral).
    # mensal_avulso é pagamento ÚNICO (mode=payment, "Experimente por um mês"):
    # libera 30 dias de acesso controlados pelo Klead (acesso_ate no usuário).
    "lite":               os.getenv("STRIPE_PRICE_LITE", ""),
    "lite_mensal_avulso": os.getenv("STRIPE_PRICE_LITE_MENSAL_AVULSO", ""),
    "lite_semestral":     os.getenv("STRIPE_PRICE_LITE_SEMESTRAL", ""),
    "lite_anual":         os.getenv("STRIPE_PRICE_LITE_ANUAL", ""),
    "user":       os.getenv("STRIPE_PRICE_USER", ""),
    "pro":        os.getenv("STRIPE_PRICE_PRO", ""),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
}

# Ciclos que são compra avulsa (não-recorrente) e quantos dias de acesso liberam.
AVULSO_DIAS = {"mensal_avulso": 30}


def _secret_key() -> str:
    return os.getenv("STRIPE_SECRET_KEY", "").strip()


def price_id_for_plan(plano: str, ciclo: str = "") -> str:
    if ciclo:
        return PRICE_IDS.get(f"{plano}_{ciclo}", "")
    return PRICE_IDS.get(plano, "")


def create_checkout_session(email: str, nome: str, plano: str, success_url: str, cancel_url: str, ciclo: str = "") -> dict:
    """Cria uma sessão de Checkout do Stripe.

    Assinatura recorrente (mode=subscription) por padrão; se o `ciclo` for
    avulso (ver AVULSO_DIAS), vira pagamento único (mode=payment) — o webhook
    então libera acesso por tempo limitado em vez de assinatura.
    Retorna {"ok": True, "url": ...} ou {"ok": False, "reason": ...}.
    """
    secret = _secret_key()
    price_id = price_id_for_plan(plano, ciclo)
    if not secret:
        return {"ok": False, "reason": "STRIPE_SECRET_KEY não configurada"}
    if not price_id:
        sufixo = f" (ciclo '{ciclo}')" if ciclo else ""
        return {"ok": False, "reason": f"price_id não configurado para o plano '{plano}'{sufixo}"}

    avulso = ciclo in AVULSO_DIAS
    data = {
        "mode": "payment" if avulso else "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "customer_email": email,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[nome]": nome,
        "metadata[email]": email,
        "metadata[plano]": plano,
    }
    if ciclo:
        data["metadata[ciclo]"] = ciclo
    if avulso:
        # Pagamento único não cria customer por padrão — força a criação pra
        # guardar o id e facilitar renovação/upgrade depois.
        data["customer_creation"] = "always"
    else:
        data["subscription_data[metadata][nome]"] = nome
        data["subscription_data[metadata][email]"] = email
        data["subscription_data[metadata][plano]"] = plano
        if ciclo:
            data["subscription_data[metadata][ciclo]"] = ciclo
    try:
        r = requests.post(
            f"{STRIPE_API_BASE}/checkout/sessions",
            data=data,
            auth=(secret, ""),
            timeout=15,
        )
        body = r.json()
        if r.status_code != 200:
            reason = (body.get("error") or {}).get("message", f"HTTP {r.status_code}")
            logger.warning(f"[Stripe] Falha ao criar checkout session: {reason}")
            return {"ok": False, "reason": reason}
        return {"ok": True, "url": body.get("url", ""), "session_id": body.get("id", "")}
    except requests.RequestException as exc:
        logger.warning(f"[Stripe] Erro de rede ao criar checkout session: {exc}")
        return {"ok": False, "reason": str(exc)}


def verify_webhook_signature(payload: bytes, sig_header: str, webhook_secret: str, tolerance_seconds: int = 300) -> bool:
    """Verifica a assinatura Stripe-Signature manualmente (esquema v1 do Stripe)."""
    if not sig_header or not webhook_secret:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp = parts.get("t", "")
    signature = parts.get("v1", "")
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > tolerance_seconds:
            return False
    except ValueError:
        return False
    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
