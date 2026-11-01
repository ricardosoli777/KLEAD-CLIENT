"""
routers/meta_oauth.py — Integração Meta via OAuth (login social), em paralelo
ao modo manual de credenciais (meta_token/meta_app_secret direto na instância).

Não remove nem depende do modo manual — ver CredentialsModal no frontend,
que mantém a seção "Configuração manual avançada" sempre disponível.

/meta/connect e /meta/assets/*  → atrás do proxy do frontend, exigem
    X-API-Key + sessão de usuário (mesmo padrão de routers/instances.py).
/meta/callback                      → acessado diretamente pelo navegador
    a partir do redirect da Meta (fora do proxy do Next.js) — não tem X-API-Key
    nem cookie de sessão disponível; a segurança vem só da assinatura/expiração
    do `state` (JWT curto, mesmo secret de auth_service).
"""
import hmac
import logging
import os
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.responses import RedirectResponse

from core.limiter import limiter
from core.security import require_api_key
from core.state import instances, instances_lock
import services.auth_service as auth_svc
import services.meta_oauth_service as meta_oauth_svc
import services.meta_oauth_pg_service as meta_oauth_pg

logger = logging.getLogger("klead")

router = APIRouter(tags=["meta-oauth"])

META_OAUTH_APP_ID = os.getenv("META_OAUTH_APP_ID", "")
META_OAUTH_APP_SECRET = os.getenv("META_OAUTH_APP_SECRET", "")
META_OAUTH_REDIRECT_URI = os.getenv("META_OAUTH_REDIRECT_URI", "")
KLEAD_APP_URL = os.getenv("KLEAD_APP_URL", "").rstrip("/")
PUBLIC_WEBHOOK_BASE = os.getenv("PUBLIC_WEBHOOK_BASE", "").rstrip("/")

STATE_TTL_SECONDS = 300  # 5 minutos — janela curta o suficiente para o usuário completar o login na Meta


def _oauth_configured() -> bool:
    return bool(META_OAUTH_APP_ID and META_OAUTH_APP_SECRET and META_OAUTH_REDIRECT_URI)


def _connection_for_instance(instance_id: str, user_email: str) -> dict | None:
    """Resolve conexão exclusiva e preserva OAuth legado ainda sem vínculo."""
    connection = meta_oauth_pg.get_connection_for_instance(instance_id)
    if connection:
        return connection
    if meta_oauth_pg.claim_unlinked_connection(instance_id, user_email):
        return meta_oauth_pg.get_connection_for_instance(instance_id)
    return None


@router.get("/meta/connect/{instance_id}")
def meta_connect(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    Gera a URL de autorização da Meta. O frontend faz a navegação do navegador
    para essa URL (window.location.href) — não é um redirect HTTP desta rota,
    porque o proxy server-side do Next.js seguiria o redirect no servidor,
    não no navegador do usuário.
    """
    user = auth_svc.require_instance_access(instance_id, request)
    if not _oauth_configured():
        raise HTTPException(503, "Integração Meta via OAuth não configurada no servidor.")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

    csrf = str(uuid.uuid4())
    payload = {
        "email": user["email"],
        "instance_id": instance_id,
        "csrf": csrf,
        "exp": int(time.time()) + STATE_TTL_SECONDS,
    }
    state = jwt.encode(payload, auth_svc._secret(), algorithm=auth_svc.JWT_ALGORITHM)
    url = meta_oauth_svc.build_authorize_url(META_OAUTH_APP_ID, META_OAUTH_REDIRECT_URI, state)
    # O frontend grava o csrf num cookie do domínio raiz antes de navegar para a
    # Meta; o callback compara cookie x state (double-submit) — garante que quem
    # completa o OAuth é o mesmo navegador que iniciou, bloqueando link forjado.
    return {"ok": True, "authorize_url": url, "csrf": csrf}


@router.get("/meta/callback")
@limiter.limit("10/minute")  # rota pública que troca code por token — sem limite dedicado, herda só o default global
def meta_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """
    Callback público (sem X-API-Key/sessão) — a Meta redireciona o navegador
    do usuário direto para esta rota. Segurança via assinatura/expiração do state.
    """
    def _redirect_error(instance_id: str, reason: str):
        base = KLEAD_APP_URL or ""
        quoted = urllib.parse.quote(reason or "erro_desconhecido")
        target = f"{base}/instances/{instance_id}?meta_error={quoted}" if instance_id else f"{base}/?meta_error={quoted}"
        return RedirectResponse(target, status_code=302)

    if error:
        logger.warning(f"[META-OAUTH] Callback com erro da Meta: {error} — {error_description}")
        return _redirect_error("", error_description or error)

    if not code or not state:
        return _redirect_error("", "parametros_ausentes")

    try:
        payload = jwt.decode(state, auth_svc._secret(), algorithms=[auth_svc.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return _redirect_error("", "state_expirado")
    except Exception:
        return _redirect_error("", "state_invalido")

    email = payload.get("email", "")
    instance_id = payload.get("instance_id", "")
    if not email or not instance_id:
        return _redirect_error(instance_id, "state_incompleto")

    # Double-submit: o csrf do state precisa bater com o cookie gravado pelo
    # navegador que iniciou o fluxo. Sem isso, um state válido de terceiro
    # (obtido legitimamente por outro usuário) poderia ser usado num link
    # forjado para vincular a conta Meta da vítima à conexão do atacante.
    csrf_state = payload.get("csrf", "")
    csrf_cookie = request.cookies.get("klead_meta_csrf", "")
    if not csrf_state or not csrf_cookie or not hmac.compare_digest(csrf_state, csrf_cookie):
        logger.warning("[META-OAUTH] Callback rejeitado — csrf do state não confere com o cookie do navegador.")
        return _redirect_error(instance_id, "csrf_invalido")

    token_result = meta_oauth_svc.exchange_code_for_token(
        META_OAUTH_APP_ID, META_OAUTH_APP_SECRET, META_OAUTH_REDIRECT_URI, code
    )
    if not token_result.get("ok"):
        logger.warning(f"[META-OAUTH] Falha ao trocar code por token: {token_result.get('detail')}")
        return _redirect_error(instance_id, "falha_troca_token")

    short_token = token_result["access_token"]
    long_result = meta_oauth_svc.exchange_for_long_lived_token(META_OAUTH_APP_ID, META_OAUTH_APP_SECRET, short_token)
    access_token = long_result["access_token"] if long_result.get("ok") else short_token
    expires_in = long_result.get("expires_in", 0) if long_result.get("ok") else token_result.get("expires_in", 0)

    me_result = meta_oauth_svc.get_me(access_token)
    meta_user_id = (me_result.get("raw") or {}).get("id", "") if me_result.get("ok") else ""

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)) if expires_in else None

    # Helpers definidos em main.py, expostos via app.state (evita import circular,
    # mesmo padrão usado por routers/webhook.py e routers/instances.py).
    fernet = request.app.state.make_fernet_for_user(email)
    encrypted_token = request.app.state.encrypt_field(fernet, access_token)

    ok, result = meta_oauth_pg.upsert_connection(
        instance_id=instance_id,
        user_email=email,
        meta_user_id=meta_user_id,
        access_token_encrypted=encrypted_token,
        token_expires_at=expires_at,
        scopes=",".join(meta_oauth_svc.REQUIRED_SCOPES),
    )
    if not ok:
        logger.warning(f"[META-OAUTH] Falha ao salvar conexão para {email}: {result}")
        return _redirect_error(instance_id, "falha_ao_salvar")

    base = KLEAD_APP_URL or ""
    return RedirectResponse(f"{base}/instances/{instance_id}?meta_connected=1", status_code=302)


@router.get("/meta/status/{instance_id}")
def meta_status(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Estado atual da integração: conectado (com dados do usuário Meta) e ativos já salvos, se houver."""
    user = auth_svc.require_instance_access(instance_id, request)
    connection = _connection_for_instance(instance_id, user["email"])
    assets = meta_oauth_pg.get_instance_assets(instance_id)
    with instances_lock:
        inst = dict(instances.get(instance_id, {}))
    return {
        "ok": True,
        "connected": bool(connection),
        "connection": {"meta_user_id": connection["meta_user_id"], "updated_at": connection["updated_at"]} if connection else None,
        "assets": assets,
        "lead_ads": {
            "enabled": bool((assets or {}).get("lead_ads_enabled")),
            "default_for_page": bool(
                assets and meta_oauth_pg.get_lead_routes(assets.get("page_id", "")).get("page_instance_id") == instance_id
            ),
        },
        "whatsapp": {
            "enabled": bool((assets or {}).get("whatsapp_enabled")),
            "configured": bool((assets or {}).get("whatsapp_enabled") and connection and assets and assets.get("status") == "active"
                               and assets.get("waba_id") and assets.get("phone_number_id")),
            "waba_id": (assets or {}).get("waba_id", ""),
            "phone_number_id": (assets or {}).get("phone_number_id", ""),
            "webhook_subscribed": bool(inst.get("meta_waba_subscribed")),
            "credential_source": "oauth" if connection and (assets or {}).get("phone_number_id") else "manual",
            "webhook_url": f"{PUBLIC_WEBHOOK_BASE}/webhook/meta-whatsapp",
        },
    }


@router.get("/meta/assets/{instance_id}")
def meta_list_assets(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Busca os ativos Meta disponíveis (Business Managers, contas de anúncio, páginas, Instagram, WABAs, números)."""
    user = auth_svc.require_instance_access(instance_id, request)
    connection = _connection_for_instance(instance_id, user["email"])
    if not connection:
        raise HTTPException(400, "Nenhuma conexão Meta ativa para esta instância. Conecte primeiro.")

    access_token = request.app.state.decrypt_field_for_user(connection["user_email"], connection["access_token_encrypted"])

    businesses = meta_oauth_svc.list_businesses(access_token)
    result_businesses = businesses.get("data", [])

    ad_accounts_raw: list = []
    pages_raw: list = []
    wabas_raw: list = []
    for biz in result_businesses:
        biz_id = biz.get("id", "")
        ad_accounts_raw += meta_oauth_svc.list_ad_accounts(biz_id, access_token).get("data", [])
        # list_pages já inclui me/accounts (páginas pessoais) a cada chamada —
        # com múltiplos Business Managers isso duplicaria a mesma lista de
        # páginas pessoais uma vez por business; dedupe abaixo por "id" resolve.
        pages_raw += meta_oauth_svc.list_pages(biz_id, access_token).get("data", [])
        wabas_raw += meta_oauth_svc.list_whatsapp_business_accounts(biz_id, access_token).get("data", [])

    if not result_businesses:
        # Sem Business Manager listado — ainda tenta páginas pessoais do usuário
        pages_raw += meta_oauth_svc.list_pages("", access_token).get("data", [])

    def _dedupe_by_id(items: list) -> list:
        seen: dict[str, dict] = {}
        for item in items:
            if item.get("id"):
                seen[item["id"]] = item
        return list(seen.values())

    ad_accounts = _dedupe_by_id(ad_accounts_raw)
    pages = _dedupe_by_id(pages_raw)
    wabas = _dedupe_by_id(wabas_raw)

    pages_with_ig = []
    lead_forms = []
    for p in pages:
        ig = meta_oauth_svc.get_instagram_for_page(p.get("id", ""), access_token)
        ig_account = (ig.get("raw") or {}).get("instagram_business_account", {}) if ig.get("ok") else {}
        ig_id = ig_account.get("id", "")
        ig_username = ""
        if ig_id:
            details = meta_oauth_svc.get_instagram_account_details(ig_id, access_token)
            ig_username = (details.get("raw") or {}).get("username", "") if details.get("ok") else ""
        # /{page_id}/leadgen_forms rejeita User Access Token (#190): a Meta
        # exige o Page Access Token, que permanece somente no backend.
        page_token_result = meta_oauth_svc.get_page_access_token(p.get("id", ""), access_token)
        forms_result = (
            meta_oauth_svc.list_lead_forms(p.get("id", ""), page_token_result["access_token"])
            if page_token_result.get("ok")
            else {"ok": False, "data": [], "detail": page_token_result.get("detail", "")}
        )
        pages_with_ig.append({
            **p,
            "instagram_business_account_id": ig_id,
            "instagram_username": ig_username,
            "lead_forms_error": "" if forms_result.get("ok") else forms_result.get("detail", "Erro ao listar formulários"),
        })
        routes = meta_oauth_pg.get_lead_routes(p.get("id", ""))
        for form in forms_result.get("data", []):
            if form.get("id"):
                lead_forms.append({
                    **form,
                    "page_id": p.get("id", ""),
                    "route_instance_id": routes.get("forms", {}).get(str(form["id"]), ""),
                })

    phone_numbers: list = []
    for waba in wabas:
        nums = meta_oauth_svc.list_phone_numbers(waba.get("id", ""), access_token).get("data", [])
        for n in nums:
            phone_numbers.append({**n, "waba_id": waba.get("id", ""), "waba_name": waba.get("name", "")})

    pixels: list = []
    for acc in ad_accounts:
        acc_pixels = meta_oauth_svc.list_pixels(acc.get("id", ""), access_token).get("data", [])
        for px in acc_pixels:
            pixels.append({**px, "ad_account_id": acc.get("id", ""), "ad_account_name": acc.get("name", "")})

    return {
        "ok": True,
        "businesses": result_businesses,
        "ad_accounts": ad_accounts,
        "pages": pages_with_ig,
        "lead_forms": lead_forms,
        "whatsapp_business_accounts": wabas,
        "phone_numbers": phone_numbers,
        "pixels": pixels,
    }


# Cache curto da árvore BM→contas por instância. Montar a árvore custa
# 1 + 2×BMs chamadas Graph — sem cache, os seletores do dashboard esperam
# tudo isso a cada visita; BMs/contas mudam raramente, 10 min é seguro.
_ads_tree_cache: dict[str, tuple[float, list]] = {}
_ads_tree_cache_lock = threading.Lock()
_ADS_TREE_CACHE_TTL = 600


@router.get("/meta/ads/{instance_id}/accounts")
def meta_ads_accounts(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    Árvore BM → contas de anúncio para o explorador Meta Ads do dashboard.
    Mais leve que /meta/assets (não busca páginas, IG, WABAs nem pixels) —
    só o que os seletores da tela precisam.
    """
    user = auth_svc.require_instance_access(instance_id, request)
    connection = _connection_for_instance(instance_id, user["email"])
    if not connection:
        raise HTTPException(400, "Nenhuma conexão Meta ativa para esta instância. Conecte primeiro.")

    with _ads_tree_cache_lock:
        cached = _ads_tree_cache.get(instance_id)
        if cached and time.time() - cached[0] < _ADS_TREE_CACHE_TTL:
            return {"ok": True, "businesses": cached[1], "cached": True}

    access_token = request.app.state.decrypt_field_for_user(connection["user_email"], connection["access_token_encrypted"])

    businesses = meta_oauth_svc.list_businesses(access_token)
    if not businesses.get("ok"):
        raise HTTPException(502, f"Meta API: {businesses.get('detail', 'erro ao listar Business Managers')}")

    def _biz_with_accounts(biz: dict) -> dict:
        return {
            "id": biz.get("id", ""),
            "name": biz.get("name", ""),
            "ad_accounts": meta_oauth_svc.list_ad_accounts(biz.get("id", ""), access_token).get("data", []),
        }

    biz_list = businesses.get("data", [])
    if biz_list:
        # Uma BM não depende da outra — busca as contas em paralelo
        with ThreadPoolExecutor(max_workers=min(8, len(biz_list))) as executor:
            tree = list(executor.map(_biz_with_accounts, biz_list))
    else:
        tree = []

    with _ads_tree_cache_lock:
        _ads_tree_cache[instance_id] = (time.time(), tree)
    return {"ok": True, "businesses": tree}


@router.get("/meta/ads/{instance_id}/campaigns")
def meta_ads_campaigns(instance_id: str, request: Request, ad_account: str = "", _: str = Security(require_api_key)):
    """
    Todas as campanhas de uma conta de anúncio (qualquer status, mesmo sem
    entrega no período). Sem ad_account, usa a conta padrão da instância.
    O token OAuth da instância tem prioridade (enxerga as contas autorizadas);
    o meta_token manual da instância é o fallback.
    """
    user = auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])

    normalize = request.app.state.normalize_meta_ad_account_id
    own_account = normalize(inst.get("meta_ad_account_id", ""))
    requested_account = normalize(ad_account) if ad_account else ""

    access_token = ""
    connection = _connection_for_instance(instance_id, user["email"])
    if connection:
        access_token = request.app.state.decrypt_field_for_user(connection["user_email"], connection["access_token_encrypted"])

    if access_token:
        # OAuth da instância: a própria Meta limita quais contas esse token
        # autorizado pode explorar.
        account = requested_account or own_account
    else:
        # Sem OAuth, sobra o meta_token manual da instância — token de sistema/
        # agência pode enxergar contas de OUTROS clientes no mesmo Business
        # Manager. Sem essa trava, um usuário desta instância poderia pedir o
        # ad_account de outro tenant e puxar as campanhas dele.
        if requested_account and requested_account != own_account:
            raise HTTPException(403, "Sem conexão OAuth própria — só é possível explorar a conta de anúncio desta instância.")
        account = own_account
        access_token = inst.get("meta_token", "").strip()

    if not account:
        raise HTTPException(400, "Nenhuma conta de anúncio informada ou configurada na instância.")
    if not access_token:
        raise HTTPException(400, "Nenhuma credencial Meta disponível — conecte via OAuth ou configure meta_token.")

    result = meta_oauth_svc.list_campaigns(account, access_token)
    if not result.get("ok"):
        raise HTTPException(502, f"Meta API: {result.get('detail', 'erro ao listar campanhas')}")

    campaigns = []
    for c in result.get("data", []):
        raw_budget = c.get("daily_budget")
        campaigns.append({
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "status": c.get("status", ""),
            "effective_status": c.get("effective_status", ""),
            "objective": c.get("objective", ""),
            # Graph API devolve orçamento em centavos (string)
            "daily_budget": round(float(raw_budget) / 100, 2) if raw_budget else None,
        })
    return {"ok": True, "ad_account": account, "campaigns": campaigns}


@router.post("/meta/assets/{instance_id}")
def meta_save_assets(instance_id: str, payload: dict, request: Request, _: str = Security(require_api_key)):
    """
    Salva a seleção de ativos do usuário para a instância e espelha os campos
    já existentes em `instances` (meta_page_id, phone_number_id, meta_ad_account_id)
    — é isso que faz KPIs/webhooks (services/meta_service.py, meta_capi_service.py)
    passarem a usar os ativos escolhidos, sem precisar alterar esses serviços.

    Se um pixel foi selecionado e a instância ainda não tem CAPI configurado
    manualmente, habilita o CAPI automaticamente usando o pixel escolhido —
    o token em si continua vindo da conexão OAuth (meta_capi_service resolve
    isso sozinho), sem precisar colar token manual. Se já existir configuração
    manual de CAPI, ela não é sobrescrita — o preenchimento manual continua
    tendo prioridade.
    """
    user = auth_svc.require_instance_access(instance_id, request)
    connection = _connection_for_instance(instance_id, user["email"])
    if not connection:
        raise HTTPException(400, "Nenhuma conexão Meta ativa para esta instância.")

    with instances_lock:
        current_inst = dict(instances.get(instance_id, {}))
    if not current_inst:
        raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    # Compatibilidade com clientes anteriores aos seletores de modo: quando a
    # flag não vier, inferir a intenção pelos ativos enviados.
    whatsapp_enabled = bool(payload.get("whatsapp_enabled")) if "whatsapp_enabled" in payload else bool(
        payload.get("waba_id") and payload.get("phone_number_id")
    )
    lead_ads_enabled = bool(payload.get("lead_ads_enabled")) if "lead_ads_enabled" in payload else bool(payload.get("page_id"))
    if whatsapp_enabled and (
        not payload.get("waba_id") or not payload.get("phone_number_id")
    ):
        raise HTTPException(400, "Para ativar WhatsApp Oficial, selecione o WABA e o número do WhatsApp.")
    if lead_ads_enabled and not payload.get("page_id"):
        raise HTTPException(400, "Para ativar Lead Ads, selecione uma Página.")

    access_token = request.app.state.decrypt_field_for_user(
        connection["user_email"], connection["access_token_encrypted"]
    )
    waba_subscription = {"ok": False, "detail": "WABA não selecionado"}
    if whatsapp_enabled and payload.get("waba_id"):
        waba_subscription = meta_oauth_svc.subscribe_whatsapp_business_account(
            payload["waba_id"], access_token
        )

    page_subscription = {"ok": False, "detail": "Página não selecionada"}
    page_token = ""
    if lead_ads_enabled and payload.get("page_id"):
        page_token_result = meta_oauth_svc.get_page_access_token(payload["page_id"], access_token)
        if page_token_result.get("ok"):
            page_token = page_token_result["access_token"]
            page_subscription = request.app.state.subscribe_meta_page_leadgen(
                page_token, payload["page_id"]
            )
        else:
            page_subscription = page_token_result

    selected_forms = payload.get("lead_forms") if isinstance(payload.get("lead_forms"), list) else []
    allowed_forms = {
        str(f.get("id")): f for f in meta_oauth_svc.list_lead_forms(payload.get("page_id", ""), page_token).get("data", [])
    } if lead_ads_enabled else {}
    normalized_forms = [
        {"id": fid, "name": allowed_forms[fid].get("name", "")}
        for fid in (str(item.get("id", "")) for item in selected_forms if isinstance(item, dict))
        if fid in allowed_forms
    ]
    if lead_ads_enabled:
        routes_ok, routes_detail = meta_oauth_pg.save_lead_routes(
            instance_id,
            payload.get("page_id", ""),
            bool(payload.get("lead_ads_default")),
            normalized_forms,
            bool(payload.get("confirm_route_transfer")),
        )
        if not routes_ok:
            status = 409 if routes_detail.startswith("transfer_confirmation_required:") else 500
            raise HTTPException(status, routes_detail)
    else:
        routes_ok, routes_detail = meta_oauth_pg.clear_instance_lead_routes(instance_id)
        if not routes_ok:
            raise HTTPException(500, f"Erro ao limpar roteamento de Lead Ads: {routes_detail}")

    persisted_payload = dict(payload)
    persisted_payload["whatsapp_enabled"] = whatsapp_enabled
    persisted_payload["lead_ads_enabled"] = lead_ads_enabled
    if not whatsapp_enabled:
        for field in ("waba_id", "waba_name", "phone_number_id", "phone_display_number"):
            persisted_payload[field] = ""

    ok, detail = meta_oauth_pg.upsert_instance_assets(instance_id, connection["id"], persisted_payload)
    if not ok:
        raise HTTPException(500, f"Erro ao salvar ativos: {detail}")

    _save_instance_config = request.app.state.save_instance_config
    _normalize_meta_ad_account_id = request.app.state.normalize_meta_ad_account_id
    with instances_lock:
        if instance_id in instances:
            inst = instances[instance_id]
            if payload.get("page_id"):
                inst["meta_page_id"] = payload["page_id"]
                inst["meta_page_subscribed"] = bool(page_subscription.get("ok"))
            if page_token:
                inst["meta_page_access_token"] = page_token
            inst["phone_number_id"] = persisted_payload.get("phone_number_id", "")
            inst["meta_waba_subscribed"] = whatsapp_enabled and bool(waba_subscription.get("ok"))
            if payload.get("ad_account_id"):
                inst["meta_ad_account_id"] = _normalize_meta_ad_account_id(payload["ad_account_id"])
            if payload.get("pixel_id"):
                inst["meta_capi_enabled"] = True
                if not inst.get("meta_capi_event_source_id"):
                    inst["meta_capi_event_source_id"] = payload["pixel_id"]
    _save_instance_config(instance_id)

    return {
        "ok": True,
        "whatsapp_enabled": whatsapp_enabled,
        "whatsapp_configured": bool(whatsapp_enabled and payload.get("waba_id") and payload.get("phone_number_id")),
        "webhook_subscribed": bool(waba_subscription.get("ok")),
        "webhook_detail": "" if waba_subscription.get("ok") else waba_subscription.get("detail", ""),
        "lead_ads_subscribed": bool(page_subscription.get("ok")),
        "lead_ads_detail": "" if page_subscription.get("ok") else page_subscription.get("detail", ""),
        "lead_forms_routed": len(normalized_forms),
    }


@router.delete("/meta/disconnect/{instance_id}")
def meta_disconnect(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Desconecta somente a integração Meta vinculada a esta instância."""
    user = auth_svc.require_instance_access(instance_id, request)
    ok, detail = meta_oauth_pg.disconnect_instance(instance_id)
    if not ok:
        raise HTTPException(500, f"Erro ao desconectar: {detail}")
    routes_ok, routes_detail = meta_oauth_pg.clear_instance_lead_routes(instance_id)
    if not routes_ok:
        logger.warning(f"[META-OAUTH] Falha ao limpar rotas de Lead Ads de {instance_id}: {routes_detail}")
    # Sem OAuth, mantém CAPI somente quando o fallback manual está completo.
    _save_instance_config = request.app.state.save_instance_config
    with instances_lock:
        inst = instances.get(instance_id)
        if inst is not None and not (
            inst.get("meta_capi_access_token") and inst.get("meta_capi_event_source_id")
        ):
            inst["meta_capi_enabled"] = False
            inst["meta_capi_event_source_id"] = ""
        if inst is not None:
            inst["meta_waba_subscribed"] = False
    _save_instance_config(instance_id)
    return {"ok": True}
