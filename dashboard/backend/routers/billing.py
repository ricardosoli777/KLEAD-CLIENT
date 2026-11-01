"""
Router de billing — /billing/* — Checkout e webhook do Stripe.
"""

import logging
import os
import threading
from collections import deque

from fastapi import APIRouter, Request, HTTPException, Response
from pydantic import BaseModel
from typing import Literal

from core.limiter import limiter
import services.auth_service as auth_svc
import services.stripe_service as stripe_svc

logger = logging.getLogger("klead")

router = APIRouter(tags=["billing"])

# Guarda de idempotência: Stripe pode reentregar o mesmo evento (retry por
# timeout, rede, etc). Sem isso, cada reentrega de checkout.session.completed
# reseta a senha do usuário e reenvia o email de boas-vindas.
#
# Camada 1 (memória): rápida, cobre reentregas normais (minutos).
# Camada 2 (Postgres): sobrevive a restart/redeploy do container — o INSERT
# com PRIMARY KEY é atômico, então dois workers processando a mesma reentrega
# ao mesmo tempo não passam os dois. Se o PG estiver fora, degrada para o
# comportamento antigo (só memória) em vez de derrubar o webhook.
_PROCESSED_EVENTS_MAX = 2000
_processed_event_ids: set[str] = set()
_processed_event_order: deque[str] = deque()
_processed_lock = threading.Lock()

_STRIPE_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id varchar(255) PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now()
);
"""
_stripe_schema_ensured = False
_stripe_schema_lock = threading.Lock()


def _ensure_stripe_events_schema() -> bool:
    global _stripe_schema_ensured
    if _stripe_schema_ensured:
        return True
    with _stripe_schema_lock:
        if _stripe_schema_ensured:
            return True
        try:
            from services import pg_pool
            from services import settings_pg_service as _svc_pg
            cfg = _svc_pg._pg_settings()
            with pg_pool.pooled_connection(cfg) as conn:
                cur = conn.cursor()
                cur.execute(_STRIPE_EVENTS_SCHEMA)
                conn.commit()
                cur.close()
            _stripe_schema_ensured = True
            return True
        except Exception as exc:
            logger.warning(f"[Stripe] Falha ao garantir schema stripe_events: {exc}")
            return False


def _pg_claim_event(event_id: str) -> bool | None:
    """Tenta reivindicar o evento no PG. True = já processado; False = reivindicado
    agora (primeira vez); None = PG indisponível (caller decide fallback)."""
    if not _ensure_stripe_events_schema():
        return None
    try:
        from services import pg_pool
        from services import settings_pg_service as _svc_pg
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO stripe_events (event_id) VALUES (%s) "
                "ON CONFLICT (event_id) DO NOTHING",
                (event_id,),
            )
            claimed = cur.rowcount == 1
            conn.commit()
            cur.close()
        return not claimed
    except Exception as exc:
        logger.warning(f"[Stripe] Idempotência PG indisponível ({exc}) — usando memória.")
        return None


def _already_processed(event_id: str) -> bool:
    if not event_id:
        return False
    # Camada rápida em memória (também evita ida ao PG em reentrega imediata)
    with _processed_lock:
        if event_id in _processed_event_ids:
            return True
        _processed_event_ids.add(event_id)
        _processed_event_order.append(event_id)
        if len(_processed_event_order) > _PROCESSED_EVENTS_MAX:
            oldest = _processed_event_order.popleft()
            _processed_event_ids.discard(oldest)
    # Camada persistente
    pg_result = _pg_claim_event(event_id)
    if pg_result is True:
        return True
    return False


class CheckoutPayload(BaseModel):
    nome: str
    email: str
    plano: Literal["lite", "user", "pro", "enterprise"]
    # Só o Lite tem ciclos alternativos. Sem ciclo = mensal recorrente (padrão);
    # mensal_avulso = pagamento único de 30 dias ("Experimente por um mês");
    # semestral/anual = assinaturas recorrentes mais baratas por mês.
    ciclo: Literal["mensal_avulso", "semestral", "anual"] | None = None


def _app_base_url() -> str:
    raw = (os.getenv("KLEAD_APP_URL", "") or "").strip().rstrip("/")
    return raw or "https://example.com"


@router.post("/billing/checkout")
@limiter.limit("10/minute")
def billing_checkout(request: Request, payload: CheckoutPayload):
    email = payload.email.strip().lower()
    nome = payload.nome.strip()
    if not email or "@" not in email:
        raise HTTPException(400, "Email inválido")
    if not nome:
        raise HTTPException(400, "Nome é obrigatório")

    ciclo = payload.ciclo or ""
    if payload.plano != "lite":
        ciclo = ""  # ciclos alternativos só existem no Lite

    base = _app_base_url()
    result = stripe_svc.create_checkout_session(
        email=email,
        nome=nome,
        plano=payload.plano,
        success_url=f"{base}/plans?checkout=sucesso",
        cancel_url=f"{base}/plans?checkout=cancelado",
        ciclo=ciclo,
    )
    if not result.get("ok"):
        raise HTTPException(502, result.get("reason", "Falha ao criar checkout"))
    return {"ok": True, "url": result["url"]}


@router.post("/billing/webhook")
async def billing_webhook(request: Request):
    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.error("[Stripe] STRIPE_WEBHOOK_SECRET não configurado — webhook rejeitado.")
        return Response(status_code=403)

    if not stripe_svc.verify_webhook_signature(raw_body, sig_header, webhook_secret):
        logger.warning("[Stripe] Webhook com assinatura inválida ou ausente.")
        return Response(status_code=403)

    import json
    try:
        event = json.loads(raw_body)
    except Exception:
        return {"ok": False, "reason": "invalid json"}

    event_id = event.get("id", "")
    if _already_processed(event_id):
        logger.info(f"[Stripe] Evento {event_id} já processado — ignorando reentrega.")
        return {"ok": True, "skipped": "duplicate_event"}

    event_type = event.get("type", "")
    # checkout.session.completed dispara assim que o cliente termina o formulário
    # de checkout — mas para métodos de pagamento assíncronos (boleto, débito),
    # isso acontece ANTES da confirmação real do pagamento (payment_status vem
    # "unpaid"; só confirma dias depois). Ativar direto nesse evento sem checar
    # payment_status dava acesso a quem só gerou o boleto, sem nunca pagar.
    # checkout.session.async_payment_succeeded é o evento que confirma o
    # pagamento assíncrono depois — carrega o mesmo objeto de sessão, então
    # reusa a mesma lógica de ativação.
    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        obj = event.get("data", {}).get("object", {})
        payment_status = obj.get("payment_status", "")
        if payment_status != "paid":
            logger.info(
                f"[Stripe] {event_type}: payment_status={payment_status!r} — "
                "pagamento ainda não confirmado, aguardando."
            )
            return {"ok": True, "skipped": "payment_not_confirmed"}

        metadata = obj.get("metadata", {}) or {}
        email = (metadata.get("email") or obj.get("customer_email") or "").strip().lower()
        nome = metadata.get("nome", "")
        plano = metadata.get("plano", "")
        ciclo = metadata.get("ciclo", "")
        customer_id = obj.get("customer", "")
        subscription_id = obj.get("subscription", "") or ""

        if not email or not plano:
            logger.warning(f"[Stripe] {event_type} sem metadata suficiente: email={email} plano={plano}")
            return {"ok": True, "skipped": True}

        # Compra avulsa (ex: lite mensal_avulso) não gera subscription no
        # Stripe — o acesso expira no Klead após N dias (acesso_ate).
        acesso_dias = stripe_svc.AVULSO_DIAS.get(ciclo)
        user = auth_svc.create_paid_subscriber(
            email, nome, plano, customer_id, subscription_id, acesso_dias=acesso_dias
        )
        if user:
            logger.info(f"[Stripe] Assinante ativado: {email} ({plano}{f'/{ciclo}' if ciclo else ''}) via {event_type}")
        else:
            logger.warning(f"[Stripe] Falha ao ativar assinante: {email} ({plano}) — plano inválido?")
    elif event_type == "checkout.session.async_payment_failed":
        obj = event.get("data", {}).get("object", {})
        metadata = obj.get("metadata", {}) or {}
        email = (metadata.get("email") or obj.get("customer_email") or "").strip().lower()
        logger.info(f"[Stripe] Pagamento assíncrono falhou/expirou para {email or 'email desconhecido'} — assinante não ativado.")
    elif event_type == "customer.subscription.deleted":
        # Cliente cancelou (ou a cobrança falhou até esgotar as tentativas e o
        # Stripe encerrou a assinatura). Sem isso, o acesso continuava ativo
        # pra sempre mesmo sem pagamento.
        obj = event.get("data", {}).get("object", {})
        subscription_id = obj.get("id", "")
        revoked = auth_svc.revoke_by_stripe_subscription(subscription_id)
        if revoked:
            logger.info(f"[Stripe] Assinatura {subscription_id} cancelada — acesso revogado.")
        else:
            logger.warning(f"[Stripe] customer.subscription.deleted para {subscription_id} — nenhum usuário encontrado com essa subscription.")

    return {"ok": True}
