"""
Chamadas à Graph API para o fluxo OAuth Meta (login social) — troca de
code por access_token e descoberta de ativos (Business Manager, contas de
anúncio, páginas, Instagram, WhatsApp Business Accounts e números).

Paralelo ao services/meta_service.py (que lida com envio de mensagem via
Cloud API usando credenciais manuais) — este módulo descobre/valida ativos e
assina o WABA selecionado no aplicativo; o envio de mensagens continua no
meta_service.py.
"""

import requests

META_API_VERSION = "v25.0"


def _safe_exc(exc: Exception) -> str:
    """Descrição de erro de rede sem repetir a URL — a query string dessas
    chamadas carrega client_secret/code/access_token e não pode ir pra log."""
    return f"erro de rede ({exc.__class__.__name__})"


META_BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"
OAUTH_DIALOG_URL = f"https://www.facebook.com/{META_API_VERSION}/dialog/oauth"

REQUIRED_SCOPES = (
    "business_management",
    "ads_read",
    "ads_management",  # necessária para enviar eventos via Conversions API (POST /{pixel_id}/events)
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_metadata",  # assinar a Página no webhook de Lead Ads
    "leads_retrieval",  # consultar os campos do formulário pelo leadgen_id
    "instagram_basic",
    "whatsapp_business_management",
    "whatsapp_business_messaging",
)


def build_authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(REQUIRED_SCOPES),
        "state": state,
        "response_type": "code",
    }
    query = "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items())
    return f"{OAUTH_DIALOG_URL}?{query}"


def exchange_code_for_token(app_id: str, app_secret: str, redirect_uri: str, code: str) -> dict:
    """Troca o code recebido no callback por um access_token de curta duração."""
    try:
        r = requests.get(
            f"{META_BASE_URL}/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=15,
        )
        data = r.json()
        if r.status_code != 200 or "access_token" not in data:
            return {"ok": False, "detail": (data.get("error") or {}).get("message", f"HTTP {r.status_code}")}
        return {"ok": True, "access_token": data["access_token"], "expires_in": data.get("expires_in", 0)}
    except requests.RequestException as exc:
        return {"ok": False, "detail": _safe_exc(exc)}


def exchange_for_long_lived_token(app_id: str, app_secret: str, short_token: str) -> dict:
    """Troca o token curto por um de longa duração (~60 dias) — reduz reconexões frequentes."""
    try:
        r = requests.get(
            f"{META_BASE_URL}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=15,
        )
        data = r.json()
        if r.status_code != 200 or "access_token" not in data:
            return {"ok": False, "detail": (data.get("error") or {}).get("message", f"HTTP {r.status_code}")}
        return {"ok": True, "access_token": data["access_token"], "expires_in": data.get("expires_in", 0)}
    except requests.RequestException as exc:
        return {"ok": False, "detail": _safe_exc(exc)}


def _get(path: str, token: str, params: dict | None = None) -> dict:
    try:
        r = requests.get(
            f"{META_BASE_URL}/{path}",
            params={**(params or {}), "access_token": token},
            timeout=15,
        )
        data = r.json()
        if r.status_code != 200:
            return {"ok": False, "detail": (data.get("error") or {}).get("message", f"HTTP {r.status_code}"), "data": []}
        return {"ok": True, "data": data.get("data", []), "raw": data}
    except requests.RequestException as exc:
        return {"ok": False, "detail": _safe_exc(exc), "data": []}


def _post(path: str, token: str, data: dict | None = None) -> dict:
    try:
        r = requests.post(
            f"{META_BASE_URL}/{path}",
            params={"access_token": token},
            data=data or {},
            timeout=15,
        )
        payload = r.json()
        if r.status_code not in (200, 201) or payload.get("success") is False:
            return {"ok": False, "detail": (payload.get("error") or {}).get("message", f"HTTP {r.status_code}")}
        return {"ok": True, "raw": payload}
    except requests.RequestException as exc:
        return {"ok": False, "detail": _safe_exc(exc)}


def get_me(token: str) -> dict:
    return _get("me", token, {"fields": "id,name"})


def list_businesses(token: str) -> dict:
    return _get("me/businesses", token, {"fields": "id,name"})


def list_ad_accounts(business_id: str, token: str) -> dict:
    """Combina contas de anúncio próprias e de clientes do Business Manager."""
    owned = _get(f"{business_id}/owned_ad_accounts", token, {"fields": "id,name,account_id"})
    client = _get(f"{business_id}/client_ad_accounts", token, {"fields": "id,name,account_id"})
    combined = (owned.get("data") or []) + (client.get("data") or [])
    ok = owned.get("ok") or client.get("ok")
    return {"ok": ok, "data": combined, "detail": owned.get("detail") or client.get("detail", "")}


def list_pages(business_id: str, token: str) -> dict:
    """Páginas do usuário + próprias/de clientes do Business Manager, sem duplicar por id."""
    own = _get("me/accounts", token, {"fields": "id,name"})
    owned_biz = _get(f"{business_id}/owned_pages", token, {"fields": "id,name"}) if business_id else {"data": []}
    client_biz = _get(f"{business_id}/client_pages", token, {"fields": "id,name"}) if business_id else {"data": []}
    seen: dict[str, dict] = {}
    for p in (own.get("data") or []) + (owned_biz.get("data") or []) + (client_biz.get("data") or []):
        if p.get("id"):
            seen[p["id"]] = p
    return {"ok": True, "data": list(seen.values())}


def get_instagram_for_page(page_id: str, token: str) -> dict:
    return _get(page_id, token, {"fields": "instagram_business_account"})


def get_page_access_token(page_id: str, user_token: str) -> dict:
    """Obtém o token dedicado da Página sem expô-lo ao frontend."""
    if not page_id or not user_token:
        return {"ok": False, "detail": "Página e token OAuth são obrigatórios"}
    result = _get(page_id, user_token, {"fields": "id,access_token"})
    page_token = (result.get("raw") or {}).get("access_token", "") if result.get("ok") else ""
    if not page_token:
        return {"ok": False, "detail": result.get("detail") or "A Meta não retornou o token da Página"}
    return {"ok": True, "access_token": page_token}


def list_lead_forms(page_id: str, token: str) -> dict:
    """Formulários instantâneos disponíveis para roteamento de Lead Ads."""
    if not page_id:
        return {"ok": True, "data": []}
    return _get(f"{page_id}/leadgen_forms", token, {"fields": "id,name,status", "limit": 200})


def get_instagram_account_details(ig_id: str, token: str) -> dict:
    """Busca username/nome de exibição da conta Instagram vinculada à página."""
    if not ig_id:
        return {"ok": False, "data": []}
    return _get(ig_id, token, {"fields": "username,name"})


def list_campaigns(ad_account_id: str, token: str) -> dict:
    """Todas as campanhas da conta de anúncio (qualquer status) — alimenta o
    seletor do explorador Meta Ads no dashboard. limit=200 cobre contas comuns;
    contas maiores exigiriam paginação (não implementada por ora)."""
    if not ad_account_id:
        return {"ok": False, "data": [], "detail": "conta de anúncio não informada"}
    return _get(f"{ad_account_id}/campaigns", token, {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,start_time,stop_time",
        "limit": 200,
    })


def list_pixels(ad_account_id: str, token: str) -> dict:
    """Pixels/Datasets vinculados à conta de anúncio — usados como event_source_id do CAPI."""
    if not ad_account_id:
        return {"ok": True, "data": []}
    return _get(f"{ad_account_id}/adspixels", token, {"fields": "id,name"})


def list_whatsapp_business_accounts(business_id: str, token: str) -> dict:
    owned = _get(f"{business_id}/owned_whatsapp_business_accounts", token, {"fields": "id,name"})
    client = _get(f"{business_id}/client_whatsapp_business_accounts", token, {"fields": "id,name"})
    combined = (owned.get("data") or []) + (client.get("data") or [])
    ok = owned.get("ok") or client.get("ok")
    return {"ok": ok, "data": combined, "detail": owned.get("detail") or client.get("detail", "")}


def list_phone_numbers(waba_id: str, token: str) -> dict:
    return _get(f"{waba_id}/phone_numbers", token, {"fields": "id,display_phone_number,verified_name"})


def subscribe_whatsapp_business_account(waba_id: str, token: str) -> dict:
    """Assina o app OAuth nos webhooks do WABA selecionado."""
    if not waba_id or not token:
        return {"ok": False, "detail": "WABA e token são obrigatórios"}
    return _post(f"{waba_id}/subscribed_apps", token)
