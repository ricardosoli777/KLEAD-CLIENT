"""
Router de autenticação — /auth/*
Extraído de main.py em 2026-06-12.
"""

import logging
import os
import re
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Literal

from core.limiter import limiter
import services.audit_log_service as audit_log_svc
import services.auth_service as auth_svc
import services.plans as plans_svc
from services.auth_service import (
    get_auth_user,
    require_superadmin,
    require_admin,
    auth_cookie_options,
)

logger = logging.getLogger("klead.auth")

router = APIRouter(tags=["auth"])

# Seguro por padrão: só desativa se COOKIE_SECURE=false explícito (dev local em HTTP)
_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no", "off")

# Formato mínimo de e-mail — barra lixo óbvio antes de gastar SMTP/registro
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

if not os.getenv("KLEAD_APP_URL", "").strip():
    logger.warning(
        "[AUTH] KLEAD_APP_URL não configurado — links de verificação de e-mail "
        "e magic login vão redirecionar para o domínio errado."
    )


# ── Modelos ───────────────────────────────────────────────────────────────────

class LoginPayload(BaseModel):
    email: str
    password: str


class InvitePayload(BaseModel):
    email: str
    nome: str
    funcao: Literal["admin", "gerente", "sdr"]
    allowed_instances: list[str] = []
    plano: Literal["lite", "user", "pro", "enterprise", "personalizado"] | None = None
    custom_max_instances: int | None = None


class ProfilePayload(BaseModel):
    nome: str | None = None
    avatar: str | None = None
    funcao: str | None = None
    telefone: str | None = None


class LastInstancePayload(BaseModel):
    last_instance_id: str = ""


class ChangePasswordPayload(BaseModel):
    current: str
    new_password: str


class AllowedInstancesPayload(BaseModel):
    allowed_instances: list[str]


class MaxInstancesPayload(BaseModel):
    max_instances: int


class PlanPayload(BaseModel):
    plano: Literal["lite", "user", "pro", "enterprise"]


class RegisterPayload(BaseModel):
    email: str
    nome: str
    plano: Literal["lite", "user", "pro", "enterprise"]


class MagicLinkPayload(BaseModel):
    email: str


class MagicLoginPayload(BaseModel):
    token: str


class TotpConfirmPayload(BaseModel):
    code: str


class TotpDisablePayload(BaseModel):
    password: str
    code: str


class TotpVerifyPayload(BaseModel):
    pending_token: str
    code: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _issue_session(user: dict, response: Response) -> dict:
    """Emite os cookies de sessão e devolve o corpo de sucesso do login.
    Compartilhado por login com senha, magic link e conclusão do 2FA."""
    token = auth_svc.create_token(user["email"])
    response.set_cookie(
        key="klead_token", value=token,
        max_age=86400, **auth_cookie_options(),
    )
    # Cookies não-httpOnly com papel e plano efetivo — apenas para gating de UI
    # (segurança real é no JWT + require_feature no backend). O proxy.ts do Next
    # lê klead_plano pra esconder rotas de features que o plano não tem.
    response.set_cookie(
        key="klead_funcao", value=user.get("funcao", ""),
        max_age=86400, httponly=False, secure=_COOKIE_SECURE, samesite="lax", path="/",
    )
    response.set_cookie(
        key="klead_plano", value=auth_svc.effective_plan_for_user(user),
        max_age=86400, httponly=False, secure=_COOKIE_SECURE, samesite="lax", path="/",
    )
    return {"ok": True, "user": auth_svc._safe_user(user), "first_access": user.get("first_access", False)}


@router.post("/auth/login")
@limiter.limit("10/minute")
def auth_login(request: Request, payload: LoginPayload, response: Response):
    _, _, ip = audit_log_svc.actor_from_request(request)
    email_tentado = (payload.email or "").strip().lower()
    try:
        user = auth_svc.login(payload.email, payload.password)
    except auth_svc.LoginRateLimitExceeded:
        audit_log_svc.record_event(
            action="login_rate_limited", target_type="user", target_id=email_tentado,
            detail="Login bloqueado por excesso de tentativas", ip=ip,
        )
        raise HTTPException(429, "Muitas tentativas de login para esta conta. Aguarde alguns minutos e tente novamente.")
    if not user:
        # Ator vazio de propósito: ninguém se autenticou. O email tentado vai no
        # alvo — é isso que denuncia um ataque de força bruta contra uma conta.
        audit_log_svc.record_event(
            action="login_failed", target_type="user", target_id=email_tentado,
            detail="Email ou senha incorretos", ip=ip,
        )
        raise HTTPException(401, "Email ou senha incorretos")
    audit_log_svc.record_event(
        actor_email=user.get("email", ""), actor_funcao=user.get("funcao", ""),
        action="login_ok", target_type="user", target_id=user.get("email", ""),
        detail="Login bem-sucedido", ip=ip,
    )
    # Senha correta, mas a conta tem 2FA: não emite sessão ainda. Devolve um
    # token curto que prova o 1º fator; o 2º passo é POST /auth/2fa/verify.
    if auth_svc.is_totp_enabled(user):
        return {"ok": True, "needs_2fa": True, "pending_token": auth_svc.create_totp_pending_token(user["email"])}
    return _issue_session(user, response)


@router.get("/auth/plans-public")
@limiter.limit("30/minute")
def auth_list_plans_public(request: Request):
    """Planos disponíveis para a página pública de preços — sem auth.
    whatsapp_suporte alimenta o CTA do Enterprise sob consulta (o número vem
    do telefone cadastrado no perfil do superadmin)."""
    return {"plans": plans_svc.list_plans(), "whatsapp_suporte": auth_svc.support_whatsapp()}


@router.post("/auth/register")
@limiter.limit("5/minute")
def auth_register(request: Request, payload: RegisterPayload):
    email = payload.email.strip().lower()
    nome = payload.nome.strip()
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(400, "Email inválido")
    if not nome:
        raise HTTPException(400, "Nome é obrigatório")
    user = auth_svc.register_user(email, nome, payload.plano)
    if not user:
        raise HTTPException(409, "Email já cadastrado")
    return {"ok": True, "status": user["status"]}


@router.post("/auth/forgot-password")
@limiter.limit("5/minute")
def auth_forgot_password(request: Request, payload: LoginPayload):
    # Sempre retorna ok para não revelar se o email existe
    auth_svc.reset_password(payload.email)
    return {"ok": True}


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
    if token:
        auth_svc.invalidate_token(token)
    response.delete_cookie("klead_token", **auth_cookie_options())
    response.delete_cookie("klead_funcao", path="/")
    response.delete_cookie("klead_plano", path="/")
    return {"ok": True}


@router.post("/auth/magic-link")
@limiter.limit("5/minute")
def auth_magic_link(request: Request, payload: MagicLinkPayload):
    """
    Solicita login sem senha. A resposta é idêntica exista ou não a conta
    (sem enumeração de e-mails); o cooldown por e-mail e o filtro de conta
    ativa ficam no auth_service.
    """
    auth_svc.request_magic_login(payload.email)
    return {"ok": True}


@router.post("/auth/magic-login")
@limiter.limit("10/minute")
def auth_magic_login(request: Request, payload: MagicLoginPayload, response: Response):
    """
    Troca o token do magic link por uma sessão. Chamado pela página de login
    via proxy — o Set-Cookie chega ao navegador no domínio do app, igual ao
    login com senha.
    """
    user = auth_svc.consume_magic_login_token(payload.token)
    if not user:
        raise HTTPException(401, "Link de acesso inválido ou expirado.")
    # 2FA ativo: o e-mail já é um fator, mas o segundo fator continua exigido —
    # ter acesso à caixa não deve burlar o autenticador (decisão do produto).
    if auth_svc.is_totp_enabled(user):
        return {"ok": True, "needs_2fa": True, "pending_token": auth_svc.create_totp_pending_token(user["email"])}
    logger.info(f"[AUTH] Login via magic link: {user['email']}")
    return _issue_session(user, response)


@router.post("/auth/2fa/verify")
@limiter.limit("10/minute")
def auth_2fa_verify(request: Request, payload: TotpVerifyPayload, response: Response):
    """Segundo passo do login quando a conta tem 2FA: valida o token pendente
    (prova do 1º fator) + o código do autenticador, e então emite a sessão."""
    user = auth_svc.consume_totp_pending_token(payload.pending_token, payload.code)
    if not user:
        raise HTTPException(401, "Código de verificação inválido ou expirado.")
    logger.info(f"[AUTH] Login concluído com 2FA: {user['email']}")
    return _issue_session(user, response)


@router.post("/auth/2fa/setup")
@limiter.limit("10/minute")
def auth_2fa_setup(request: Request):
    """Inicia a configuração do 2FA: gera segredo + QR (ainda não ativa)."""
    user, _ = get_auth_user(request)
    result = auth_svc.start_totp_setup(user["email"])
    if not result:
        raise HTTPException(400, "Não foi possível iniciar a configuração do 2FA.")
    return {"ok": True, **result}


@router.post("/auth/2fa/confirm")
@limiter.limit("10/minute")
def auth_2fa_confirm(request: Request, payload: TotpConfirmPayload):
    """Confirma o setup com o primeiro código e ativa o 2FA. Retorna os backup
    codes uma única vez (só o hash é guardado no servidor)."""
    user, _ = get_auth_user(request)
    backup_codes = auth_svc.confirm_totp_setup(user["email"], payload.code)
    if backup_codes is None:
        raise HTTPException(400, "Código inválido. Verifique o app autenticador e tente novamente.")
    _, _, ip = audit_log_svc.actor_from_request(request)
    audit_log_svc.record_event(
        actor_email=user.get("email", ""), actor_funcao=user.get("funcao", ""),
        action="2fa_enabled", target_type="user", target_id=user.get("email", ""),
        detail="2FA ativado", ip=ip,
    )
    return {"ok": True, "backup_codes": backup_codes}


@router.post("/auth/2fa/disable")
@limiter.limit("10/minute")
def auth_2fa_disable(request: Request, payload: TotpDisablePayload):
    """Desativa o 2FA — exige senha atual E um código válido (TOTP ou backup)."""
    user, _ = get_auth_user(request)
    if not auth_svc.disable_totp(user["email"], payload.password, payload.code):
        raise HTTPException(400, "Senha ou código incorretos.")
    _, _, ip = audit_log_svc.actor_from_request(request)
    audit_log_svc.record_event(
        actor_email=user.get("email", ""), actor_funcao=user.get("funcao", ""),
        action="2fa_disabled", target_type="user", target_id=user.get("email", ""),
        detail="2FA desativado", ip=ip,
    )
    return {"ok": True}


@router.get("/auth/me")
def auth_me(request: Request, response: Response):
    user, _ = get_auth_user(request)
    # Backfill: sessões criadas antes dos cookies klead_funcao/klead_plano
    # (ou com valores desatualizados) são corrigidas aqui — /auth/me roda em
    # todo carregamento de página, então a sessão antiga se conserta sozinha
    # sem exigir novo login. Cookies não-httpOnly, só para gating de UI.
    funcao_atual = user.get("funcao", "")
    plano_atual = auth_svc.effective_plan_for_user(user)
    if request.cookies.get("klead_funcao") != funcao_atual:
        response.set_cookie(
            key="klead_funcao", value=funcao_atual,
            max_age=86400, httponly=False, secure=_COOKIE_SECURE, samesite="lax", path="/",
        )
    if request.cookies.get("klead_plano") != plano_atual:
        response.set_cookie(
            key="klead_plano", value=plano_atual,
            max_age=86400, httponly=False, secure=_COOKIE_SECURE, samesite="lax", path="/",
        )
    return auth_svc._safe_user(user)


@router.patch("/auth/last-instance")
def auth_set_last_instance(payload: LastInstancePayload, request: Request):
    user, _ = get_auth_user(request)
    last_instance_id = (payload.last_instance_id or "").strip()
    if last_instance_id and not auth_svc.can_access_instance(user, last_instance_id):
        raise HTTPException(403, "Acesso negado a esta instância")
    updated = auth_svc.set_last_instance(user["email"], last_instance_id)
    if not updated:
        raise HTTPException(404, "Usuário não encontrado")
    return auth_svc._safe_user(updated)


@router.put("/auth/profile")
def auth_update_profile(payload: ProfilePayload, request: Request):
    user, _ = get_auth_user(request)
    try:
        updated = auth_svc.update_profile(user["email"], payload.nome, payload.avatar, payload.funcao, payload.telefone)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "Usuário não encontrado")
    return auth_svc._safe_user(updated)


@router.get("/auth/verify-email")
@limiter.limit("10/minute")
def auth_verify_email(request: Request, token: str = ""):
    """
    Link clicado no e-mail de verificação — acessado direto pelo navegador
    (fora do proxy do Next, sem X-API-Key nem sessão, como o /meta/callback).
    A segurança vem da assinatura/expiração do token.
    """
    app_url = os.getenv("KLEAD_APP_URL", "").rstrip("/")
    user = auth_svc.confirm_email_verification(token) if token else None
    if not user:
        return RedirectResponse(f"{app_url}/login?verified=0", status_code=302)
    logger.info(f"[AUTH] E-mail verificado: {user['email']}")
    return RedirectResponse(f"{app_url}/login?verified=1", status_code=302)


@router.post("/auth/send-verification")
@limiter.limit("3/minute")
def auth_send_verification(request: Request):
    """Reenvia o link de verificação para o usuário logado."""
    user, _ = get_auth_user(request)
    if auth_svc._safe_user(user)["email_verified"]:
        return {"ok": True, "already_verified": True}
    ok = auth_svc.send_verification_email(user["email"])
    if not ok:
        raise HTTPException(500, "Não foi possível enviar o e-mail de verificação.")
    return {"ok": True}


@router.post("/auth/change-password")
def auth_change_password(payload: ChangePasswordPayload, request: Request, response: Response):
    user, token = get_auth_user(request)
    if len(payload.new_password) < 8:
        raise HTTPException(400, "A nova senha deve ter pelo menos 8 caracteres")
    ok = auth_svc.change_password(user["email"], payload.current, payload.new_password)
    if not ok:
        raise HTTPException(400, "Senha atual incorreta")
    auth_svc.invalidate_token(token)
    response.delete_cookie("klead_token", **auth_cookie_options())
    _, _, ip = audit_log_svc.actor_from_request(request)
    audit_log_svc.record_event(
        actor_email=user.get("email", ""), actor_funcao=user.get("funcao", ""),
        action="password_changed", target_type="user", target_id=user.get("email", ""),
        detail="Senha alterada pelo próprio usuário", ip=ip,
    )
    return {"ok": True}


def _audit_actor(request: Request, actor: dict, action: str, target_email: str,
                 detail: str = "", meta=None) -> None:
    """Atalho pros eventos de gestão de conta — o ator é sempre quem admin(ou super) fez."""
    _, _, ip = audit_log_svc.actor_from_request(request)
    audit_log_svc.record_event(
        actor_email=actor.get("email", ""), actor_funcao=actor.get("funcao", ""),
        action=action, target_type="user", target_id=target_email,
        detail=detail, meta=meta, ip=ip,
    )


@router.get("/auth/users")
def auth_list_users(request: Request):
    actor = require_admin(request)
    return auth_svc.list_users_for(actor)


@router.post("/auth/users/invite")
def auth_invite_user(payload: InvitePayload, request: Request):
    actor = require_admin(request)
    if actor.get("funcao") == "admin":
        if actor.get("max_instances", 1) <= 2:
            raise HTTPException(403, "Seu plano não permite convidar outros usuários. Fale com o suporte para aumentar seu plano.")
        if payload.funcao not in ("gerente", "sdr"):
            raise HTTPException(403, "Admin só pode convidar gerente ou sdr.")
        # Convite não exige instância pré-existente — gerente/sdr conecta o
        # próprio WhatsApp depois do login (ver create_instance/create_channel).
        # Se o admin passar allowed_instances mesmo assim, ainda precisa ser
        # subconjunto do que ele possui.
        if payload.allowed_instances:
            actor_instances = set(actor.get("allowed_instances") or [])
            if not set(payload.allowed_instances).issubset(actor_instances):
                raise HTTPException(403, "Você só pode dar acesso às suas próprias instâncias.")
    user = auth_svc.invite_user(
        payload.email, payload.nome, payload.funcao, payload.allowed_instances,
        plano=payload.plano, custom_max_instances=payload.custom_max_instances,
        invited_by=actor.get("email", ""),
    )
    if not user:
        raise HTTPException(409, "Email já cadastrado")
    _audit_actor(
        request, actor, "user_invited", payload.email,
        f"Convidou {payload.email} como {payload.funcao}",
        meta={"funcao": payload.funcao, "plano": payload.plano,
              "allowed_instances": payload.allowed_instances},
    )
    return user


@router.patch("/auth/users/{email}/revoke")
def auth_revoke_user(email: str, request: Request):
    actor = require_admin(request)
    if actor.get("funcao") == "admin":
        target = auth_svc.get_user_by_email(email)
        if not target or not auth_svc.can_manage_target_user(actor, target):
            raise HTTPException(403, "Você não gerencia este usuário.")
    if not auth_svc.revoke_user(email):
        raise HTTPException(400, "Não foi possível revogar este usuário")
    _audit_actor(request, actor, "user_revoked", email, f"Revogou o acesso de {email}")
    return {"ok": True}


@router.patch("/auth/users/{email}/reactivate")
def auth_reactivate_user(email: str, request: Request):
    actor = require_admin(request)
    if actor.get("funcao") == "admin":
        target = auth_svc.get_user_by_email(email)
        if not target or not auth_svc.can_manage_target_user(actor, target):
            raise HTTPException(403, "Você não gerencia este usuário.")
    if not auth_svc.reactivate_user(email):
        raise HTTPException(404, "Usuário não encontrado")
    _audit_actor(request, actor, "user_reactivated", email, f"Reativou o acesso de {email}")
    return {"ok": True}


@router.patch("/auth/users/{email}/activate")
def auth_activate_signup(email: str, request: Request):
    """Ativa um cadastro pendente da landing page — gera senha e envia por email."""
    require_superadmin(request)
    user = auth_svc.activate_signup_user(email)
    if not user:
        raise HTTPException(404, "Usuário não encontrado ou não está pendente")
    return user


@router.post("/auth/users/{email}/reset-password")
def auth_reset_password(email: str, request: Request):
    actor = require_admin(request)
    if actor.get("funcao") == "admin":
        target = auth_svc.get_user_by_email(email)
        if not target or not auth_svc.can_manage_target_user(actor, target):
            raise HTTPException(403, "Você não gerencia este usuário.")
    if not auth_svc.reset_password(email):
        raise HTTPException(404, "Usuário não encontrado")
    _audit_actor(request, actor, "password_reset", email,
                 f"Resetou a senha de {email} (nova senha enviada por email)")
    return {"ok": True, "detail": "Nova senha enviada por email"}


@router.patch("/auth/users/{email}/instances")
def auth_set_allowed_instances(email: str, payload: AllowedInstancesPayload, request: Request):
    actor = require_admin(request)
    if actor.get("funcao") == "admin":
        target = auth_svc.get_user_by_email(email)
        if not target or not auth_svc.can_manage_target_user(actor, target):
            raise HTTPException(403, "Você não gerencia este usuário.")
        actor_instances = set(actor.get("allowed_instances") or [])
        if not set(payload.allowed_instances).issubset(actor_instances):
            raise HTTPException(403, "Você só pode dar acesso às suas próprias instâncias.")

    # Nunca mistura instâncias da conta mestre (superadmin) com instâncias de
    # outro dono num mesmo usuário — cada pessoa pertence a um único tenant.
    sa_instances = auth_svc.superadmin_instance_ids()
    requested = set(payload.allowed_instances)
    if (requested & sa_instances) and (requested - sa_instances):
        raise HTTPException(403, "Não é possível misturar instâncias da conta mestre com instâncias de outro dono.")

    updated = auth_svc.set_allowed_instances(email, payload.allowed_instances)
    if not updated:
        raise HTTPException(404, "Usuário não encontrado")
    return updated


@router.delete("/auth/users/{email}")
def auth_delete_user(email: str, request: Request):
    actor = require_admin(request)
    target = auth_svc.get_user_by_email(email)
    if not target:
        raise HTTPException(404, "Usuário não encontrado")
    if actor.get("funcao") == "admin" and not auth_svc.can_manage_target_user(actor, target):
        raise HTTPException(403, "Você não gerencia este usuário.")

    # Admin/superadmin sao donos diretos das proprias instancias — ao excluir
    # a conta, as instancias que so ele possui vao junto (Evolution GO, dados,
    # CRM, KB, roleta). remove_instance_from_all_users() ja apaga o login de
    # quem ficar sem nenhuma instancia, entao isso tambem cobre o proprio
    # usuario sendo excluido aqui — delete_user() abaixo vira so um fallback
    # caso ele ainda exista (ex: nao tinha nenhuma instancia propria).
    # Superadmin nunca e excluido por este endpoint (auth_svc.delete_user
    # bloqueia) — nao faz cascade nas instancias dele tambem, senao ficaria
    # sem instancia com o login ainda ativo (estado pior que o atual).
    deleted_instances: list[str] = []
    if target.get("funcao") == "admin":
        from routers.instances import _delete_instance_core
        # Contas atuais registram propriedade separadamente de mero acesso.
        # O fallback preserva contas legadas anteriores a owned_instances.
        owned = (
            target.get("owned_instances")
            if "owned_instances" in target
            else target.get("allowed_instances")
        )
        for iid in list(owned or []):
            try:
                _delete_instance_core(iid, request)
                deleted_instances.append(iid)
            except HTTPException as exc:
                logger.warning(f"[UserDelete] Falha ao apagar instância '{iid}' do usuário '{email}': {exc.detail}")
            except Exception as exc:
                logger.warning(f"[UserDelete] Erro inesperado ao apagar instância '{iid}': {exc}", exc_info=True)

    ok = auth_svc.delete_user(email)
    if not ok and not deleted_instances:
        raise HTTPException(404, "Usuário não encontrado ou é superadmin")
    # _safe_user: o snapshot NUNCA carrega password_hash nem segredo de 2FA.
    _audit_actor(
        request, actor, "user_deleted", email,
        f"Excluiu o usuário {email}"
        + (f" e {len(deleted_instances)} instância(s) em cascata" if deleted_instances else ""),
        meta={"user": auth_svc._safe_user(target), "deleted_instances": deleted_instances},
    )
    return {"ok": True, "deleted_instances": deleted_instances}


@router.patch("/auth/users/{email}/max-instances")
def auth_set_max_instances(email: str, payload: MaxInstancesPayload, request: Request):
    """Ajuste manual e direto do limite (sem passar por um plano nomeado)."""
    require_superadmin(request)
    updated = auth_svc.set_max_instances(email, payload.max_instances)
    if not updated:
        raise HTTPException(404, "Usuário não encontrado")
    return updated


@router.get("/auth/plans")
def auth_list_plans(request: Request):
    """Planos disponíveis (user=1, pro=5, enterprise=20 instâncias) — usado pro seletor no frontend."""
    require_superadmin(request)
    return {"plans": plans_svc.list_plans()}


@router.patch("/auth/users/{email}/plan")
def auth_set_user_plan(email: str, payload: PlanPayload, request: Request):
    """
    Atribui um plano nomeado ao usuário — já ajusta o max_instances certo.
    Hoje é o superadmin que escolhe na mão; quando o Stripe estiver conectado,
    o webhook de pagamento aprovado chama essa mesma lógica automaticamente.
    """
    actor = require_superadmin(request)
    antes = auth_svc.get_user_by_email(email)
    plano_antes = (antes or {}).get("plano", "")
    updated = auth_svc.set_user_plan(email, payload.plano)
    if not updated:
        raise HTTPException(404, "Usuário não encontrado ou plano inválido")
    _audit_actor(
        request, actor, "plan_changed", email,
        f"Plano de {email}: {plano_antes or 'nenhum'} → {payload.plano}",
        meta={"de": plano_antes, "para": payload.plano},
    )
    return updated
