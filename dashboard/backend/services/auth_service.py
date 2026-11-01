"""
Auth Service — KLEAD
Gerencia usuários, JWT, convites por email (Gmail SMTP)
"""

import html
import json
import logging
import os
import secrets
import smtplib
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import bcrypt
import hmac as _hmac
import jwt

from services import settings_pg_service
from utils.cleaner import normalize_phone

logger = logging.getLogger("klead.auth")

# RLock (reentrante): funções que seguram o lock chamam helpers que também o
# adquirem (ex: list_users -> _safe_user -> effective_plan_for_user). Com Lock
# simples isso é deadlock da thread contra si mesma — trava TODO o auth da API.
_lock = threading.RLock()

# JWT config
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

# Token blacklist persistente
_blacklist: set[str] = set()

_users: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Persistência assíncrona ───────────────────────────────────────────────────
# _save()/_save_blacklist() são chamados em dezenas de pontos DENTRO de
# `with _lock:`. A escrita no Postgres (rede) dentro do lock segurava o lock
# por segundos quando o PG estava lento — e todo verify_token (toda request
# autenticada) esperava, congelando o app. Aqui o caller só tira um snapshot
# (rápido, em memória) e enfileira; um worker dedicado grava fora do lock.
# Escritas coalescem por chave — vale sempre o snapshot mais recente.
import copy as _copy

_persist_cond = threading.Condition()
_persist_pending: dict[str, object] = {}


def _persist_enqueue(key: str, value) -> None:
    with _persist_cond:
        _persist_pending[key] = value
        _persist_cond.notify()


def _persist_worker():
    while True:
        with _persist_cond:
            while not _persist_pending:
                _persist_cond.wait()
            key, value = _persist_pending.popitem()
        try:
            ok, detail = settings_pg_service.save_setting(key, value)
            if not ok:
                logger.warning(f"[Auth] Falha ao sincronizar '{key}' com Postgres: {detail}")
        except Exception as exc:
            logger.warning(f"[Auth] Erro inesperado ao sincronizar '{key}': {exc}")


threading.Thread(target=_persist_worker, daemon=True, name="auth-persist").start()


def _save():
    """Persiste usuários no Postgres — única fonte de verdade.
    Assíncrono: snapshot em memória agora, escrita no PG pelo worker — nunca
    bloqueia o caller (que quase sempre está segurando o _lock)."""
    if not settings_pg_service.pg_configured():
        logger.warning("[Auth] Postgres não configurado — users NÃO persistidos (modo dev/teste).")
        return
    _persist_enqueue("users", _copy.deepcopy(_users))


def _prune_blacklist(tokens: set) -> set:
    """Remove tokens expirados ou com assinatura inválida da blacklist."""
    alive = set()
    for token in tokens:
        try:
            jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
            alive.add(token)
        except jwt.ExpiredSignatureError:
            pass  # expirado — descartar
        except (jwt.InvalidSignatureError, jwt.DecodeError):
            pass  # corrompido/inválido — descartar
        except Exception:
            alive.add(token)  # erro desconhecido — manter por precaução
    return alive


def _load_blacklist():
    """
    Carrega a blacklist do Postgres — única fonte de verdade.
    Fail-fast: PG configurado mas indisponível aborta o startup — subir com
    blacklist vazia reativaria tokens revogados. Chave ausente = instalação nova.
    """
    global _blacklist
    if not settings_pg_service.pg_configured():
        logger.warning("[Auth] Postgres não configurado — blacklist vazia (modo dev/teste).")
        _blacklist = set()
        return
    ok, data = settings_pg_service.load_setting("revoked_tokens")
    if ok and isinstance(data, list):
        _blacklist = _prune_blacklist(set(str(t) for t in data if t))
    elif not ok and "não encontrada" in str(data):
        _blacklist = set()
    else:
        raise RuntimeError(f"[Auth] Falha ao carregar blacklist do Postgres — abortando: {data}")


def _save_blacklist():
    """Poda e persiste a blacklist. Assíncrono — a escrita no PG sai pelo
    worker; o caller (segurando _lock) não bloqueia em I/O de rede."""
    global _blacklist
    _blacklist = _prune_blacklist(_blacklist)
    if not settings_pg_service.pg_configured():
        logger.warning("[Auth] Postgres não configurado — blacklist NÃO persistida (modo dev/teste).")
        return
    _persist_enqueue("revoked_tokens", sorted(_blacklist))


def _blacklist_cleanup_daemon():
    """Limpa tokens expirados da blacklist a cada 6h — evita crescimento infinito em memória."""
    while True:
        time.sleep(6 * 3600)
        with _lock:
            before = len(_blacklist)
            _save_blacklist()
            after = len(_blacklist)
        if before != after:
            logger.info(f"[Auth] Blacklist pruned: {before} → {after} tokens")


def load_users():
    """
    Carrega usuários do Postgres — única fonte de verdade.
    Fail-fast: PG configurado mas indisponível aborta o startup — subir com
    users vazio recriaria só o superadmin e o próximo _save() apagaria os
    demais usuários do banco. Chave ausente = instalação nova.
    """
    global _users
    if not settings_pg_service.pg_configured():
        logger.warning("[Auth] Postgres não configurado — users vazio (modo dev/teste).")
        _users = {}
    else:
        ok, data = settings_pg_service.load_setting("users")
        if ok and isinstance(data, dict):
            _users = data
        elif not ok and "não encontrada" in str(data):
            _users = {}
        else:
            raise RuntimeError(f"[Auth] Falha ao carregar users do Postgres — abortando: {data}")
    changed = False
    for email, user in list(_users.items()):
        if not isinstance(user, dict):
            _users[email] = {}
            user = _users[email]
            changed = True
        if "last_instance_id" not in user:
            user["last_instance_id"] = ""
            changed = True
    if changed:
        _save()
    _load_blacklist()
    _ensure_superadmin()
    threading.Thread(target=_blacklist_cleanup_daemon, daemon=True, name="blacklist-cleanup").start()


def _ensure_superadmin():
    """Garante que o superadmin existe. Se não existir, cria e envia senha por email."""
    superadmin_email = os.getenv("SUPERADMIN_EMAIL", "")
    if not superadmin_email:
        return
    if superadmin_email in _users:
        return

    password = os.getenv("SUPERADMIN_PASSWORD") or secrets.token_urlsafe(12)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    _users[superadmin_email] = {
        "id":            str(uuid.uuid4()),
        "email":         superadmin_email,
        "password_hash": hashed,
        "nome":          "Superadmin",
        "avatar":        "",
        "funcao":        "superadmin",
        "status":        "active",
        "first_access":  True,
        "last_instance_id": "",
        "created_at":    _now(),
        "updated_at":    _now(),
    }
    _save()
    _send_welcome_email(superadmin_email, password, first=True)
    logger.info(f"[AUTH] Superadmin criado: {superadmin_email}")


# ── Email ──────────────────────────────────────────────────────────────────────

def _smtp_config():
    return {
        "host":     os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port":     int(os.getenv("SMTP_PORT", "587")),
        "user":     os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
    }


def _send_email(to: str, subject: str, html: str):
    cfg = _smtp_config()
    if not cfg["user"] or not cfg["password"]:
        logger.warning("[AUTH] SMTP não configurado — email não enviado")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"KLEAD <{cfg['user']}>"
        msg["To"]      = to
        msg.attach(MIMEText(html, "html", "utf-8"))

        # timeout obrigatório: sem ele, um SMTP mudo segura a thread para sempre
        # (e quem estiver esperando o _lock do auth junto) — já congelou o app.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["user"], to, msg.as_string())
        logger.info(f"[AUTH] Email enviado para {to}")
    except Exception as e:
        logger.warning(f"[AUTH] Falha ao enviar email para {to}: {e}")


def _email_template(title: str, body: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#111B21;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px;">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#1F2C34;border-radius:16px;overflow:hidden;">
        <tr><td style="background:#25D366;padding:24px 32px;">
          <h1 style="margin:0;color:#fff;font-size:22px;font-weight:bold;">⚡ KLEAD</h1>
        </td></tr>
        <tr><td style="padding:32px;">
          <h2 style="color:#E9EDEF;margin:0 0 16px;">{title}</h2>
          {body}
          <p style="color:#8696A0;font-size:12px;margin-top:32px;">
            Este é um email automático do KLEAD. Não responda.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _klead_login_url() -> str:
    raw = (
        os.getenv("KLEAD_APP_URL", "")
        or os.getenv("DASHBOARD_PUBLIC_URL", "")
        or os.getenv("APP_BASE_URL", "")
    ).strip()
    if not raw:
        logger.warning("[Auth] KLEAD_APP_URL não configurado — link de acesso omitido dos emails")
        return ""
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        logger.warning("[Auth] KLEAD_APP_URL inválido (%r) — link de acesso omitido dos emails", raw)
        return ""
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/login"):
        final_path = path or "/login"
    else:
        final_path = f"{path}/login" if path else "/login"
    return urllib.parse.urlunparse(parsed._replace(path=final_path, params="", query="", fragment=""))


def _send_welcome_email(email: str, password: str, first: bool = False):
    label = "Sua conta de Superadmin foi criada" if first else "Você foi convidado para o KLEAD"
    login_url = _klead_login_url()
    login_block = (
        f'<p style="margin:24px 0;">'
        f'<a href="{login_url}" style="display:inline-block;background:#25D366;color:#ffffff;text-decoration:none;padding:14px 22px;border-radius:10px;font-weight:bold;">Acessar KLEAD</a>'
        f'</p>'
        f'<p style="color:#8696A0;margin:0 0 8px;">URL de acesso: <a href="{login_url}" style="color:#25D366;">{login_url}</a></p>'
        if login_url else
        '<p style="color:#8696A0;margin-top:16px;">Configure <code>KLEAD_APP_URL</code> no servidor para incluir o link de acesso neste email.</p>'
    )
    body = f"""
      <p style="color:#8696A0;margin:0 0 24px;">
        {"Bem-vindo! Esta é sua senha de acesso inicial." if first else "Você recebeu um convite de acesso."}
        Acesse o KLEAD, entre com a senha temporária e defina sua nova senha no primeiro login.
      </p>
      <div style="background:#111B21;border-radius:12px;padding:20px;margin-bottom:24px;text-align:center;">
        <p style="color:#8696A0;margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:1px;">Senha temporária</p>
        <p style="color:#25D366;font-size:28px;font-weight:bold;margin:0;letter-spacing:4px;font-family:monospace;">{password}</p>
      </div>
      <p style="color:#8696A0;margin:0 0 8px;">Login: <span style="color:#E9EDEF;">{html.escape(email)}</span></p>
      {login_block}
    """
    threading.Thread(
        target=_send_email,
        args=(email, f"KLEAD — {label}", _email_template(label, body)),
        daemon=True,
    ).start()


def _send_password_reset_email(email: str, password: str):
    label = "Sua senha foi redefinida"
    login_url = _klead_login_url()
    login_block = (
        f'<p style="margin:24px 0;">'
        f'<a href="{login_url}" style="display:inline-block;background:#25D366;color:#ffffff;text-decoration:none;padding:14px 22px;border-radius:10px;font-weight:bold;">Acessar KLEAD</a>'
        f'</p>'
        f'<p style="color:#8696A0;margin:0 0 8px;">URL de acesso: <a href="{login_url}" style="color:#25D366;">{login_url}</a></p>'
        if login_url else
        '<p style="color:#8696A0;margin-top:16px;">Configure <code>KLEAD_APP_URL</code> no servidor para incluir o link de acesso neste email.</p>'
    )
    body = f"""
      <p style="color:#8696A0;margin:0 0 24px;">Uma nova senha temporária foi gerada para sua conta. Faça login com ela e defina sua nova senha.</p>
      <div style="background:#111B21;border-radius:12px;padding:20px;margin-bottom:24px;text-align:center;">
        <p style="color:#8696A0;margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:1px;">Nova senha temporária</p>
        <p style="color:#F0B429;font-size:28px;font-weight:bold;margin:0;letter-spacing:4px;font-family:monospace;">{password}</p>
      </div>
      <p style="color:#8696A0;margin:0 0 8px;">Login: <span style="color:#E9EDEF;">{html.escape(email)}</span></p>
      {login_block}
    """
    threading.Thread(
        target=_send_email,
        args=(email, f"KLEAD — {label}", _email_template(label, body)),
        daemon=True,
    ).start()


# ── Verificação de e-mail ──────────────────────────────────────────────────────

_VERIFY_EMAIL_EXPIRE_HOURS = 48


def create_email_verification_token(email: str) -> str:
    """Token assinado de uso no link de verificação — mesmo secret do JWT de sessão,
    diferenciado pelo campo purpose para não ser aceito como token de login."""
    payload = {
        "sub": email,
        "purpose": "verify_email",
        "exp": datetime.now(timezone.utc) + timedelta(hours=_VERIFY_EMAIL_EXPIRE_HOURS),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def confirm_email_verification(token: str) -> dict | None:
    """Valida o token do link e marca o e-mail do usuário como verificado."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except Exception:
        return None
    if payload.get("purpose") != "verify_email":
        return None
    email = payload.get("sub", "")
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        if not user.get("email_verified"):
            user["email_verified"] = True
            user["updated_at"] = _now()
            _save()
        return user


def send_verification_email(email: str) -> bool:
    """Envia (ou reenvia) o link de verificação de e-mail. False se o usuário
    não existe ou já está verificado."""
    with _lock:
        user = _users.get(email)
        if not user or user.get("email_verified"):
            return False
        nome = user.get("nome", "")
    # O link aponta direto para o backend (fora do proxy do Next), igual ao
    # callback do Meta OAuth — o endpoint valida e redireciona para o app.
    api_base = os.getenv("PUBLIC_WEBHOOK_BASE", "").rstrip("/")
    token = create_email_verification_token(email)
    verify_url = f"{api_base}/auth/verify-email?token={urllib.parse.quote(token, safe='')}"
    label = "Confirme seu e-mail"
    body = f"""
      <p style="color:#8696A0;margin:0 0 24px;">Olá{f", <span style='color:#E9EDEF;'>{html.escape(nome)}</span>" if nome else ""}! Clique no botão abaixo para confirmar que este e-mail é seu. O link vale por {_VERIFY_EMAIL_EXPIRE_HOURS} horas.</p>
      <p style="margin:24px 0;">
        <a href="{verify_url}" style="display:inline-block;background:#25D366;color:#ffffff;text-decoration:none;padding:14px 22px;border-radius:10px;font-weight:bold;">Confirmar e-mail</a>
      </p>
      <p style="color:#8696A0;margin:0 0 8px;font-size:12px;">Se o botão não funcionar, copie e cole este link no navegador:</p>
      <p style="color:#25D366;margin:0;font-size:12px;word-break:break-all;">{verify_url}</p>
      <p style="color:#8696A0;margin-top:16px;font-size:12px;">Se você não criou uma conta no KLEAD, ignore este e-mail.</p>
    """
    threading.Thread(
        target=_send_email,
        args=(email, f"KLEAD — {label}", _email_template(label, body)),
        daemon=True,
    ).start()
    return True


# ── Magic link (login sem senha) ───────────────────────────────────────────────

_MAGIC_LOGIN_EXPIRE_MINUTES = 15
_MAGIC_SEND_COOLDOWN_SECONDS = 60
# Último envio por e-mail — contém spam de caixa de entrada mesmo dentro do
# rate limit por IP do slowapi (ataques distribuídos contra o mesmo e-mail).
_magic_last_send: dict[str, float] = {}
_magic_send_lock = threading.Lock()


def create_magic_login_token(email: str) -> str:
    """Token de login sem senha: uso único, 15 minutos, amarrado ao
    token_version (troca/reset de senha invalida links pendentes)."""
    with _lock:
        user = _users.get(email)
        token_version = int(user.get("token_version", 0)) if user else 0
    payload = {
        "sub": email,
        "purpose": "magic_login",
        "ver": token_version,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_MAGIC_LOGIN_EXPIRE_MINUTES),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def request_magic_login(email: str) -> bool:
    """
    Envia o link de acesso sem senha. Silencioso por design: o router responde
    "ok" sempre, exista a conta ou não — o retorno aqui é só para log/teste.
    Só contas ativas recebem link; pendentes/revogadas não reganham acesso.
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    with _lock:
        user = _users.get(email)
        if not user or user.get("status") != "active":
            return False
        nome = user.get("nome", "")
    now = time.time()
    with _magic_send_lock:
        if now - _magic_last_send.get(email, 0) < _MAGIC_SEND_COOLDOWN_SECONDS:
            return False
        _magic_last_send[email] = now
    app_url = os.getenv("KLEAD_APP_URL", "").rstrip("/")
    if not app_url:
        logger.warning("[AUTH] KLEAD_APP_URL não configurado — magic link não enviado")
        return False
    # O link abre o app (não a API): a página de login troca o token pela
    # sessão via proxy, garantindo o cookie no domínio certo do frontend.
    token = create_magic_login_token(email)
    login_url = f"{app_url}/login?magic_token={urllib.parse.quote(token, safe='')}"
    label = "Seu link de acesso"
    body = f"""
      <p style="color:#8696A0;margin:0 0 24px;">Olá{f", <span style='color:#E9EDEF;'>{html.escape(nome)}</span>" if nome else ""}! Clique no botão abaixo para entrar no KLEAD sem senha. O link vale por {_MAGIC_LOGIN_EXPIRE_MINUTES} minutos e só pode ser usado uma vez.</p>
      <p style="margin:24px 0;">
        <a href="{login_url}" style="display:inline-block;background:#25D366;color:#ffffff;text-decoration:none;padding:14px 22px;border-radius:10px;font-weight:bold;">Entrar no KLEAD</a>
      </p>
      <p style="color:#8696A0;margin:0 0 8px;font-size:12px;">Se o botão não funcionar, copie e cole este link no navegador:</p>
      <p style="color:#25D366;margin:0;font-size:12px;word-break:break-all;">{login_url}</p>
      <p style="color:#8696A0;margin-top:16px;font-size:12px;">Se você não solicitou este acesso, ignore este e-mail — ninguém entra sem ele.</p>
    """
    threading.Thread(
        target=_send_email,
        args=(email, f"KLEAD — {label}", _email_template(label, body)),
        daemon=True,
    ).start()
    logger.info(f"[AUTH] Magic link solicitado para {email}")
    return True


def consume_magic_login_token(token: str) -> dict | None:
    """Valida e consome (uso único) um token de magic link. Retorna o usuário ou None."""
    if not token:
        return None
    # Uso único: token já consumido vive na blacklist persistida (some dela
    # sozinho ao expirar, via _prune_blacklist)
    if any(_hmac.compare_digest(token, revoked) for revoked in _blacklist):
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except Exception:
        return None
    if payload.get("purpose") != "magic_login":
        return None
    email = payload.get("sub", "")
    with _lock:
        user = _users.get(email)
        if not user or user.get("status") != "active":
            return None
        if int(user.get("token_version", 0)) != int(payload.get("ver", -1)):
            return None
        # Clicar no link prova a posse da caixa — vale como verificação de e-mail
        if not user.get("email_verified"):
            user["email_verified"] = True
            user["updated_at"] = _now()
            _save()
    invalidate_token(token)
    _clear_login_attempts(email)
    return user


# ── 2FA (TOTP) ─────────────────────────────────────────────────────────────────

_TOTP_ISSUER = "KLEAD"
_TOTP_BACKUP_CODES = 10
_TOTP_PENDING_EXPIRE_MINUTES = 5


def is_totp_enabled(user: dict | None) -> bool:
    return bool(user and user.get("totp_enabled"))


def _decrypt_totp_secret(email: str, user: dict) -> str:
    from services import crypto_utils
    enc = user.get("totp_secret", "")
    if not enc:
        return ""
    return crypto_utils.decrypt_field_for_user(email, enc)


def start_totp_setup(email: str) -> dict | None:
    """
    Gera um novo segredo TOTP (ainda NÃO ativado) e devolve o segredo em texto
    e a URI otpauth:// para o QR. Sobrescreve qualquer setup pendente. Só
    ativa de fato depois que o usuário confirma um código válido em
    confirm_totp_setup — evita travar a conta com um segredo que o usuário
    não conseguiu escanear.
    """
    import pyotp
    from services import crypto_utils
    with _lock:
        user = _users.get(email)
        if not user or user.get("status") != "active":
            return None
    secret = pyotp.random_base32()
    fernet = crypto_utils.make_fernet_for_user(email)
    encrypted = crypto_utils.encrypt_field(fernet, secret)  # fail-closed se cripto indisponível
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        user["totp_secret"] = encrypted
        user["totp_enabled"] = False
        user.pop("totp_backup_codes", None)
        user["updated_at"] = _now()
        _save()
    otpauth_uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_TOTP_ISSUER)
    return {"secret": secret, "otpauth_uri": otpauth_uri, "qr_svg": _otpauth_qr_svg(otpauth_uri)}


def _otpauth_qr_svg(otpauth_uri: str) -> str:
    """QR em SVG como data URI — renderizado server-side (segno, puro Python),
    sem depender de biblioteca de QR no frontend."""
    try:
        import io
        import base64
        import segno
        buf = io.BytesIO()
        segno.make(otpauth_uri, error="m").save(buf, kind="svg", scale=4, border=2)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return ""


def confirm_totp_setup(email: str, code: str) -> list[str] | None:
    """
    Confirma o setup: valida o primeiro código contra o segredo pendente. Se
    válido, ativa o 2FA e gera os backup codes (retornados em texto UMA vez —
    só o hash é guardado). Retorna a lista de backup codes, ou None se inválido.
    """
    import pyotp
    code = (code or "").strip().replace(" ", "")
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        secret = _decrypt_totp_secret(email, user)
    if not secret:
        return None
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return None
    plain_codes = [secrets.token_hex(5) for _ in range(_TOTP_BACKUP_CODES)]  # 10 hex chars cada
    hashed = [bcrypt.hashpw(c.encode(), bcrypt.gensalt()).decode() for c in plain_codes]
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        user["totp_enabled"] = True
        user["totp_backup_codes"] = hashed
        user["updated_at"] = _now()
        _save()
    return plain_codes


def verify_totp(email: str, code: str) -> bool:
    """
    Valida um código no login: primeiro tenta o TOTP; se falhar, tenta consumir
    um backup code (uso único). Protegido pelo mesmo lockout do login por conta.
    """
    import pyotp
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code:
        return False
    _raise_if_locked_out(email)
    with _lock:
        user = _users.get(email)
        if not user or not user.get("totp_enabled"):
            return False
        secret = _decrypt_totp_secret(email, user)
    # TOTP normal (6 dígitos)
    if secret and pyotp.TOTP(secret).verify(code, valid_window=1):
        _clear_login_attempts(email)
        return True
    # Backup code (uso único) — remove o hash consumido
    with _lock:
        user = _users.get(email)
        if not user:
            return False
        backup = list(user.get("totp_backup_codes", []) or [])
        for i, h in enumerate(backup):
            try:
                if bcrypt.checkpw(code.encode(), h.encode()):
                    backup.pop(i)
                    user["totp_backup_codes"] = backup
                    user["updated_at"] = _now()
                    _save()
                    _clear_login_attempts(email)
                    return True
            except Exception:
                continue
    _record_login_failure(email)
    return False


def disable_totp(email: str, password: str, code: str) -> bool:
    """Desativa o 2FA — exige a senha atual E um código válido (TOTP ou backup)."""
    with _lock:
        user = _users.get(email)
        if not user or not user.get("totp_enabled"):
            return False
        try:
            if not bcrypt.checkpw(password.encode()[:72], user["password_hash"].encode()):
                return False
        except Exception:
            return False
    if not verify_totp(email, code):
        return False
    with _lock:
        user = _users.get(email)
        if not user:
            return False
        user["totp_enabled"] = False
        user.pop("totp_secret", None)
        user.pop("totp_backup_codes", None)
        user["updated_at"] = _now()
        _save()
    return True


def create_totp_pending_token(email: str) -> str:
    """
    Token curto (5 min) emitido após senha/magic link corretos quando a conta
    tem 2FA — prova que o primeiro fator passou, sem reenviar a senha. purpose
    dedicado: verify_token o rejeita como sessão; uso único via blacklist.
    """
    with _lock:
        user = _users.get(email)
        token_version = int(user.get("token_version", 0)) if user else 0
    payload = {
        "sub": email,
        "purpose": "totp_pending",
        "ver": token_version,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_TOTP_PENDING_EXPIRE_MINUTES),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def consume_totp_pending_token(token: str, code: str) -> dict | None:
    """Valida o token pendente + o código 2FA e devolve o usuário para emitir a sessão."""
    if not token:
        return None
    if any(_hmac.compare_digest(token, revoked) for revoked in _blacklist):
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except Exception:
        return None
    if payload.get("purpose") != "totp_pending":
        return None
    email = payload.get("sub", "")
    with _lock:
        user = _users.get(email)
        if not user or user.get("status") != "active":
            return None
        if int(user.get("token_version", 0)) != int(payload.get("ver", -1)):
            return None
    if not verify_totp(email, code):
        return None
    invalidate_token(token)  # uso único
    with _lock:
        return _users.get(email)


# ── JWT ────────────────────────────────────────────────────────────────────────

def _secret() -> str:
    secret = os.getenv("DASHBOARD_SECRET", "")
    if not secret:
        raise RuntimeError("DASHBOARD_SECRET não configurado no servidor.")
    return secret


def create_token(email: str) -> str:
    with _lock:
        user = _users.get(email)
        token_version = int(user.get("token_version", 0)) if user else 0
    payload = {
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "jti": str(uuid.uuid4()),
        "ver": token_version,
        "funcao": user.get("funcao", "") if user else "",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    if any(_hmac.compare_digest(token, revoked) for revoked in _blacklist):
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        # Tokens de fluxo especial (verificação de e-mail, magic link) carregam
        # "purpose" e NUNCA valem como sessão — sem isso, o token do link de
        # e-mail funcionaria como JWT de login durante a validade dele.
        if payload.get("purpose"):
            return None
        email = payload.get("sub")
        token_version = int(payload.get("ver", 0))
        with _lock:
            user = _users.get(email)
        if not user or user.get("status") != "active":
            return None
        if int(user.get("token_version", 0)) != token_version:
            return None
        return user
    except Exception:
        return None


def invalidate_token(token: str):
    if not token:
        return
    with _lock:
        _blacklist.add(token)
        _save_blacklist()


# ── Usuários ───────────────────────────────────────────────────────────────────

# Rate limit de login por conta — complementa o limite por IP (slowapi), que
# não protege contra brute-force distribuído entre vários IPs contra a mesma
# conta. Estado em memória: aceitável pois o backend roda em processo único.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


class LoginRateLimitExceeded(Exception):
    pass


def _raise_if_locked_out(email: str) -> None:
    now = time.time()
    with _login_attempts_lock:
        attempts = [t for t in _login_attempts.get(email, []) if now - t < _LOGIN_WINDOW_SECONDS]
        _login_attempts[email] = attempts
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise LoginRateLimitExceeded()


# Teto do mapa de tentativas — chaves são criadas para QUALQUER e-mail tentado
# (inclusive inexistentes); sem poda, ataque distribuído cresce o dict sem limite.
_LOGIN_ATTEMPTS_MAX_KEYS = 10_000


def _record_login_failure(email: str) -> None:
    now = time.time()
    with _login_attempts_lock:
        if len(_login_attempts) > _LOGIN_ATTEMPTS_MAX_KEYS:
            stale = [
                k for k, v in _login_attempts.items()
                if not v or now - v[-1] > _LOGIN_WINDOW_SECONDS
            ]
            for k in stale:
                _login_attempts.pop(k, None)
        _login_attempts.setdefault(email, []).append(now)


def _clear_login_attempts(email: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(email, None)


def login(email: str, password: str) -> dict | None:
    _raise_if_locked_out(email)
    with _lock:
        user = _users.get(email)
    if not user or user.get("status") != "active":
        _record_login_failure(email)
        return None
    try:
        if not bcrypt.checkpw(password.encode()[:72], user["password_hash"].encode()):
            _record_login_failure(email)
            return None
    except Exception:
        _record_login_failure(email)
        return None
    _clear_login_attempts(email)
    return user


def get_user(email: str) -> dict | None:
    with _lock:
        return _users.get(email)


# ~300KB de imagem em base64 (~400k chars) — avatar maior infla o registro do
# usuário no Postgres e o payload de todo /auth/me.
_AVATAR_MAX_CHARS = 400_000


def _validate_avatar(url: str) -> str:
    if not url:
        return url
    if url.startswith("data:image/"):
        if len(url) > _AVATAR_MAX_CHARS:
            raise ValueError("avatar muito grande — use uma imagem de até ~300KB")
        return url
    import urllib.parse
    if len(url) > 2000:
        raise ValueError("URL de avatar muito longa")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("avatar deve ser uma URL https ou data URI de imagem")
    return url


def _validate_telefone(raw: str) -> str:
    """Telefone do perfil: vazio limpa; preenchido precisa ser celular BR válido."""
    tel = str(raw or "").strip()
    if not tel:
        return ""
    normalized = normalize_phone(tel)
    if not normalized:
        raise ValueError("Telefone inválido — informe DDD + celular (ex: 11 91234-5678)")
    return normalized


def update_profile(email: str, nome: str | None = None, avatar: str | None = None, funcao: str | None = None, telefone: str | None = None) -> dict | None:
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        if nome     is not None: user["nome"]     = nome
        if avatar   is not None: user["avatar"]   = _validate_avatar(avatar)
        if telefone is not None: user["telefone"] = _validate_telefone(telefone)
        # Papel do usuário não é alterado pelo perfil pessoal.
        # Mudanças de função passam pelos endpoints administrativos.
        user["updated_at"] = _now()
        _save()
        return user


def set_last_instance(email: str, last_instance_id: str) -> dict | None:
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        user["last_instance_id"] = str(last_instance_id or "").strip()
        user["updated_at"] = _now()
        _save()
        return user


def change_password(email: str, current: str, new_password: str) -> bool:
    with _lock:
        user = _users.get(email)
        if not user:
            return False
        if not bcrypt.checkpw(current.encode()[:72], user["password_hash"].encode()):
            return False
        user["password_hash"] = bcrypt.hashpw(new_password.encode()[:72], bcrypt.gensalt()).decode()
        user["first_access"]  = False
        user["token_version"] = int(user.get("token_version", 0)) + 1
        user["updated_at"]    = _now()
        _save()
        return True


from services import plans as _plans

_DEFAULT_MAX_INSTANCES = int(os.getenv("DEFAULT_MAX_INSTANCES", str(_plans.max_instances_for_plan(_plans.DEFAULT_PLAN))))


def invite_user(
    email: str, nome: str, funcao: str, allowed_instances: list | None = None,
    plano: str | None = None, custom_max_instances: int | None = None,
    invited_by: str = "",
) -> dict | None:
    # Plano só se aplica a quem cria/possui instância (admin). Gerente/sdr
    # usam a instância que já foi criada pra eles, sem limite proprio.
    plano_final = plano if (funcao == "admin" and plano and _plans.is_valid_plan(plano)) else _plans.DEFAULT_PLAN
    max_instances_final = (
        _plans.max_instances_for_plan(plano_final, custom_max_instances)
        if funcao == "admin" else _DEFAULT_MAX_INSTANCES
    )
    with _lock:
        if email in _users:
            return None  # já existe
        password = secrets.token_urlsafe(10)
        hashed   = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = {
            "id":              str(uuid.uuid4()),
            "email":           email,
            "password_hash":   hashed,
            "nome":            nome,
            "avatar":          "",
            "telefone":        "",
            # Convidado recebe a senha temporária por e-mail — conseguir logar
            # já prova a posse da caixa, então nasce verificado.
            "email_verified":  True,
            "funcao":          funcao,
            "status":          "active",
            "first_access":    True,
            "token_version":   0,
            "allowed_instances": allowed_instances if allowed_instances is not None else [],
            # Instâncias que o próprio usuário criou (conta pra cota do plano).
            # Separado de allowed_instances porque esse último também recebe
            # instâncias compartilhadas por outra pessoa (visibilidade), que
            # não deveriam consumir a cota de criação de ninguém além de quem
            # de fato criou (ver count_owned_instances/add_owned_instance).
            "owned_instances": [],
            # Quem convidou (só setado pra gerente/sdr) — usado pra achar o
            # tenant dono antes de existir qualquer instancia compartilhada,
            # tanto na listagem/gestao (list_users_for/can_manage_target_user)
            # quanto pra auto-conceder acesso quando o convidado cria a propria
            # instancia (ver routers/instances.py).
            "invited_by":      invited_by if funcao in ("gerente", "sdr") else "",
            # Plano e limite de instâncias — ajustado pelo superadmin no convite;
            # quando o Stripe entrar pra fluxo self-service, o webhook de
            # pagamento aprovado chama set_user_plan() com o plano do price_id pago.
            "plano":           plano_final,
            "max_instances":   max_instances_final,
            "last_instance_id": "",
            "created_at":      _now(),
            "updated_at":      _now(),
        }
        _users[email] = user
        _save()
    _send_welcome_email(email, password)
    return _safe_user(user)


def register_user(email: str, nome: str, plano: str) -> dict | None:
    """
    Lead da landing page de planos. Cria o usuário como dono do próprio
    tenant (funcao=admin) com status "pending" e SEM senha ainda — a senha
    só é gerada e enviada por email quando o cadastro é ativado (hoje na
    mão pelo superadmin após confirmar o pagamento; quando o Stripe
    conectar, o webhook de pagamento aprovado chama activate_signup_user()
    automaticamente em vez de esperar ação manual).
    """
    if not _plans.is_valid_plan(plano):
        return None
    with _lock:
        if email in _users:
            return None  # já existe
        user = {
            "id":              str(uuid.uuid4()),
            "email":           email,
            "password_hash":   "",
            "nome":            nome,
            "avatar":          "",
            "telefone":        "",
            "email_verified":  False,
            "funcao":          "admin",
            "status":          "pending",
            "first_access":    True,
            "token_version":   0,
            "allowed_instances": [],
            "owned_instances": [],
            "plano":           plano,
            "max_instances":   _plans.max_instances_for_plan(plano),
            "last_instance_id": "",
            "created_at":      _now(),
            "updated_at":      _now(),
        }
        _users[email] = user
        _save()
    _notify_superadmin_new_signup(email, nome, plano)
    # Link de verificação já no cadastro — o superadmin vê no painel se o
    # e-mail do lead é real antes de ativar a conta.
    send_verification_email(email)
    return _safe_user(user)


def _acesso_ate_extend(current: str, dias: int) -> str:
    """Data de expiração pra compra avulsa (ex: lite mensal_avulso).
    Renovação antecipada não perde dias: se o vencimento atual ainda está no
    futuro, os novos dias somam a partir dele; senão, contam a partir de agora."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    base = now
    if current:
        try:
            existing = datetime.fromisoformat(current)
            if existing.tzinfo is None:
                existing = existing.replace(tzinfo=timezone.utc)
            if existing > now:
                base = existing
        except ValueError:
            pass
    return (base + timedelta(days=dias)).isoformat()


def access_expired(user: dict) -> bool:
    """True se o usuário tem acesso por tempo limitado (compra avulsa) já vencido.
    Usuários sem acesso_ate (assinaturas recorrentes, convidados, superadmin)
    nunca expiram por aqui."""
    acesso_ate = user.get("acesso_ate") or ""
    if not acesso_ate:
        return False
    from datetime import datetime, timezone
    try:
        exp = datetime.fromisoformat(acesso_ate)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


def create_paid_subscriber(email: str, nome: str, plano: str, stripe_customer_id: str = "", stripe_subscription_id: str = "", acesso_dias: int | None = None) -> dict | None:
    """
    Chamada pelo webhook do Stripe quando um pagamento de assinatura é
    confirmado (checkout.session.completed). Cria a conta já ATIVA (o
    pagamento em si é a aprovação — não precisa mais de ativação manual do
    superadmin) com senha temporária gerada e enviada por email. Se o
    email já existir (ex: já era um cadastro pendente antigo), apenas
    ativa e atualiza o plano em vez de duplicar.
    """
    if not _plans.is_valid_plan(plano):
        return None
    with _lock:
        existing = _users.get(email)
        if existing:
            password = secrets.token_urlsafe(10)
            existing["password_hash"]           = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            existing["status"]                  = "active"
            existing["first_access"]            = True
            existing["plano"]                   = plano
            existing["max_instances"]           = _plans.max_instances_for_plan(plano)
            existing["stripe_customer_id"]      = stripe_customer_id
            existing["stripe_subscription_id"]  = stripe_subscription_id
            # Compra avulsa estende o vencimento; assinatura recorrente limpa
            # (upgrade de avulso pra recorrente não pode manter expiração).
            existing["acesso_ate"] = (
                _acesso_ate_extend(existing.get("acesso_ate", ""), acesso_dias) if acesso_dias else ""
            )
            existing["updated_at"]              = _now()
            user = existing
        else:
            password = secrets.token_urlsafe(10)
            user = {
                "id":              str(uuid.uuid4()),
                "email":           email,
                "password_hash":   bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                "nome":            nome,
                "avatar":          "",
                "telefone":        "",
                # Assinante pago recebe a senha temporária por e-mail — logar
                # já prova a posse da caixa, então nasce verificado.
                "email_verified":  True,
                "funcao":          "admin",
                "status":          "active",
                "first_access":    True,
                "token_version":   0,
                "allowed_instances": [],
                "owned_instances": [],
                "plano":           plano,
                "max_instances":   _plans.max_instances_for_plan(plano),
                "stripe_customer_id":     stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
                # Vazio = sem expiração (assinatura recorrente). Preenchido só
                # em compra avulsa (ex: lite mensal_avulso = +30 dias).
                "acesso_ate":      _acesso_ate_extend("", acesso_dias) if acesso_dias else "",
                "last_instance_id": "",
                "created_at":      _now(),
                "updated_at":      _now(),
            }
            _users[email] = user
        _save()
    _send_welcome_email(email, password)
    return _safe_user(user)


def activate_signup_user(email: str) -> dict | None:
    """
    Ativa um cadastro pendente da landing page: gera a senha temporária e
    dispara o email de boas-vindas com o link de login (mesmo padrão do
    invite_user). Só age sobre status "pending" — para reativar alguém
    revogado, use reactivate_user().
    """
    with _lock:
        user = _users.get(email)
        if not user or user.get("status") != "pending":
            return None
        password = secrets.token_urlsafe(10)
        user["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user["status"]        = "active"
        user["first_access"]  = True
        # Senha temporária vai por e-mail — logar com ela prova a posse da caixa.
        user["email_verified"] = True
        user["updated_at"]    = _now()
        _save()
    _send_welcome_email(email, password)
    return _safe_user(user)


def _notify_superadmin_new_signup(email: str, nome: str, plano: str):
    superadmin_email = os.getenv("SUPERADMIN_EMAIL", "")
    if not superadmin_email:
        return
    # html.escape: nome/email vêm do cadastro público — sem escape, um
    # atacante injeta HTML (phishing) no e-mail que o superadmin recebe.
    body = f"""
      <p style="color:#E9EDEF;">Novo cadastro aguardando ativação:</p>
      <p style="color:#E9EDEF;"><b>Nome:</b> {html.escape(nome)}<br/>
      <b>Email:</b> {html.escape(email)}<br/>
      <b>Plano escolhido:</b> {html.escape(plano)}</p>
      <p style="color:#8696A0;font-size:13px;">Ative em Usuários → Reativar (após confirmar o pagamento).</p>
    """
    _send_email(superadmin_email, "KLEAD — novo cadastro pendente de ativação", _email_template("Novo cadastro", body))


def set_max_instances(email: str, max_instances: int) -> dict | None:
    """Ajuste manual e direto do limite, sem passar por um plano nomeado."""
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        user["max_instances"] = max(0, int(max_instances))
        user["updated_at"] = _now()
        _save()
        return _safe_user(user)


def set_user_plan(email: str, plano: str) -> dict | None:
    """
    Atribui um plano nomeado (user/pro/enterprise) e já ajusta o max_instances
    correspondente. É essa função que o webhook do Stripe vai chamar no futuro,
    passando o plano resolvido a partir do price_id pago.
    """
    if not _plans.is_valid_plan(plano):
        return None
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        user["plano"] = plano
        user["max_instances"] = _plans.max_instances_for_plan(plano)
        user["updated_at"] = _now()
        _save()
        return _safe_user(user)


def count_owned_instances(user: dict) -> int:
    """
    Quantas instâncias esse usuário de fato CRIOU (consome a cota do plano).
    Não usa allowed_instances porque esse campo também acumula instâncias
    compartilhadas por outra pessoa (visibilidade) — contar por ali fazia um
    admin/gerente/sdr perder vaga de criação só por enxergar uma instância
    de outro membro do time (ver add_owned_instance).
    """
    owned = user.get("owned_instances")
    return len(owned) if owned else 0


def add_owned_instance(email: str, instance_id: str) -> None:
    """Registra que `email` foi quem de fato criou `instance_id` — chamado só
    no momento da criação (routers/instances.py), nunca em compartilhamento."""
    with _lock:
        user = _users.get(email)
        if not user:
            return
        owned = list(user.get("owned_instances") or [])
        if instance_id not in owned:
            owned.append(instance_id)
            user["owned_instances"] = owned
            _save()


def register_created_instance(email: str, instance_id: str) -> bool:
    """Registra acesso e propriedade da instância em um único snapshot.

    Falha fechado se o usuário deixou de existir entre a autenticação e a
    criação. Isso evita persistir uma instância sem dono por causa de duas
    atualizações independentes em ``allowed_instances``/``owned_instances``.
    """
    with _lock:
        user = _users.get(email)
        if not user:
            return False
        previous = _copy.deepcopy(user)
        allowed = list(user.get("allowed_instances") or [])
        owned = list(user.get("owned_instances") or [])
        if instance_id not in allowed:
            allowed.append(instance_id)
        if instance_id not in owned:
            owned.append(instance_id)
        user["allowed_instances"] = allowed
        user["owned_instances"] = owned
        user["updated_at"] = _now()
        # Esta escrita específica é síncrona: a instância só será persistida
        # depois que o dono já estiver confirmado na fonte de verdade. As
        # demais mutações de auth continuam usando o worker assíncrono.
        if settings_pg_service.pg_configured():
            ok, detail = settings_pg_service.save_setting(
                "users", _copy.deepcopy(_users),
            )
            if not ok:
                _users[email] = previous
                logger.warning(
                    f"[Auth] Falha ao registrar dono da instância '{instance_id}': {detail}"
                )
                return False
        else:
            _save()
        return True


def can_create_more_instances(user: dict) -> bool:
    """Superadmin nunca tem limite. Demais usuários seguem o max_instances do plano."""
    if user.get("funcao") == "superadmin":
        return True
    limit = user.get("max_instances", _DEFAULT_MAX_INSTANCES)
    return count_owned_instances(user) < limit


def support_whatsapp() -> str:
    """WhatsApp de contato pra CTAs públicos (ex: Enterprise sob consulta na
    página de planos). Vem do telefone cadastrado no PERFIL do superadmin —
    já normalizado (55 + DDD + 9 dígitos) pelo _validate_telefone. Vazio se
    o superadmin ainda não preencheu o telefone."""
    email = os.getenv("SUPERADMIN_EMAIL", "")
    user = _users.get(email) or {}
    return user.get("telefone") or ""


# ── HTTP helpers — usados por main.py e routers ───────────────────────────────
# Importados aqui para manter auth_service como único ponto de verdade sobre
# regras de acesso. main.py e routers importam daqui em vez de duplicar.

from fastapi import Request, HTTPException  # noqa: E402

# Seguro por padrão: só desativa se COOKIE_SECURE=false explícito (dev local em HTTP)
_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no", "off")


def get_auth_user(request: Request):
    """Extrai e valida o usuário autenticado da requisição (cookie ou header)."""
    token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
    user = verify_token(token)
    if not user:
        raise HTTPException(401, "Não autenticado")
    # Compra avulsa vencida (ex: lite mensal_avulso após 30 dias) — o
    # usuário ainda loga, mas a API bloqueia até renovar em /plans.
    if access_expired(user):
        raise HTTPException(403, "Seu período de acesso terminou. Renove seu plano em /plans para continuar.")
    return user, token


def try_get_auth_user(request: Request):
    """Como get_auth_user, mas retorna None em vez de lançar 401 — uso em
    pontos opcionais (ex: log de auditoria) que não devem quebrar a rota
    principal se não houver sessão JWT presente ou se `request` não trouxer
    os atributos esperados (ex: dublês simplificados em testes)."""
    try:
        user, _ = get_auth_user(request)
        return user
    except Exception:
        return None


def require_superadmin(request: Request):
    user, token = get_auth_user(request)
    if user.get("funcao") != "superadmin":
        raise HTTPException(403, "Acesso restrito ao superadmin")
    return user


def require_admin(request: Request):
    user, _ = get_auth_user(request)
    if user.get("funcao") not in ("superadmin", "admin"):
        raise HTTPException(403, "Acesso restrito ao admin")
    return user


def require_manager(request: Request):
    user, _ = get_auth_user(request)
    if user.get("funcao") not in ("superadmin", "admin", "gerente"):
        raise HTTPException(403, "Acesso restrito ao gestor")
    return user


def effective_plan_for_user(user: dict) -> str:
    """
    Plano EFETIVO do usuário para fins de gating de features.
      - superadmin (conta mestre): "" — nunca restrito, tem tudo.
      - admin (cliente dono): o próprio plano.
      - gerente/sdr: herda o plano do dono (admin/superadmin) das instâncias a
        que tem acesso — um time de cliente lite também é lite. Dono superadmin
        => "" (sem restrição). Sem dono resolvível => plano padrão.
    Só o plano "lite" restringe algo hoje; qualquer outro retorno libera tudo.
    """
    funcao = user.get("funcao")
    if funcao == "superadmin":
        return ""
    if funcao == "admin":
        return user.get("plano") or _plans.DEFAULT_PLAN
    member_instances = set(user.get("allowed_instances") or [])
    if member_instances:
        with _lock:
            owners = [
                u for u in _users.values()
                if u.get("funcao") in ("admin", "superadmin")
                and member_instances & set(u.get("allowed_instances") or [])
            ]
        for owner in owners:
            if owner.get("funcao") == "superadmin":
                return ""
            return owner.get("plano") or _plans.DEFAULT_PLAN
    return _plans.DEFAULT_PLAN


def require_feature(request: Request, feature: str) -> dict:
    """
    Bloqueia a rota se o plano efetivo do usuário não inclui a feature (403).
    Superadmin sempre passa. Usar em endpoints de disparador, roleta e
    configuração do SDR autônomo — as features que o plano Lite não tem.
    """
    user, _ = get_auth_user(request)
    if user.get("funcao") == "superadmin":
        return user
    plano = effective_plan_for_user(user)
    if not _plans.plan_has_feature(plano, feature):
        raise HTTPException(403, "Seu plano não inclui este recurso. Faça upgrade para liberá-lo.")
    return user


def plan_for_instance(instance_id: str) -> str:
    """
    Plano efetivo do DONO (admin/superadmin) de uma instância — para gating em
    contexto SEM request/usuário (ex: processamento de webhook em background).
    Dono superadmin ou não resolvível => "" (sem restrição). Só "lite" restringe.
    """
    if not instance_id:
        return ""
    with _lock:
        for u in _users.values():
            if u.get("funcao") in ("admin", "superadmin") and instance_id in (u.get("allowed_instances") or []):
                if u.get("funcao") == "superadmin":
                    return ""
                return u.get("plano") or _plans.DEFAULT_PLAN
    return ""  # dono não encontrado → fail-open (não quebra o fluxo)


def instance_plan_allows(instance_id: str, feature: str) -> bool:
    """True se o plano do dono da instância libera a feature. Usado no fluxo do
    webhook (sem request) para bloquear o SDR autônomo em instâncias do plano Lite."""
    return _plans.plan_has_feature(plan_for_instance(instance_id), feature)


def require_instance_access(instance_id: str, request: Request) -> dict:
    """
    Exige sessao de usuario valida E posse da instancia (via can_access_instance).
    Usar em toda rota que recebe instance_id/instancia_id vindo de cliente
    autenticado (frontend) — nao usar em endpoints internos do sistema
    (webhook do Evolution GO, callbacks sem sessao de usuario), que devem
    continuar protegidos so por require_api_key.
    """
    user, _ = get_auth_user(request)
    if not can_access_instance(user, instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    return user


def auth_cookie_options() -> dict:
    return {
        "httponly": True,
        "secure": _COOKIE_SECURE,
        "samesite": "lax",
        "path": "/",
    }


def set_allowed_instances(email: str, allowed_instances: list) -> dict | None:
    with _lock:
        user = _users.get(email)
        if not user:
            return None
        user["allowed_instances"] = [str(i) for i in allowed_instances]
        user["updated_at"] = _now()
        _save()
        return _safe_user(user)


def owns_instance(user: dict, instance_id: str) -> bool:
    """
    Usado só na listagem da home (dashboard principal) — ninguem tem bypass
    aqui, nem superadmin. Cada um ve so as proprias instancias
    (allowed_instances). Visao cross-tenant completa fica exclusiva do
    painel /admin. Nao usar isso para checagens de permissao de acao
    (edicao, envio, etc) — para isso use can_access_instance.
    """
    allowed = user.get("allowed_instances")
    return bool(allowed) and instance_id in allowed


def can_access_instance(user: dict, instance_id: str) -> bool:
    """
    Superadmin acessa tudo; demais usuarios ficam restritos a allowed_instances
    (lista vazia ou ausente = sem acesso a nenhuma instancia — falha fechado).
    """
    if user.get("funcao") == "superadmin":
        return True
    allowed = user.get("allowed_instances")
    return bool(allowed) and instance_id in allowed


def revoke_by_stripe_subscription(subscription_id: str) -> bool:
    """Chamada pelo webhook do Stripe em customer.subscription.deleted —
    encontra o usuário pela subscription cancelada e revoga o acesso.
    Sem isso, cliente cancela no Stripe e continua usando o Klead de graça."""
    if not subscription_id:
        return False
    with _lock:
        email = next(
            (e for e, u in _users.items() if u.get("stripe_subscription_id") == subscription_id),
            None,
        )
    if not email:
        return False
    return revoke_user(email)


def revoke_user(email: str) -> bool:
    with _lock:
        user = _users.get(email)
        if not user or user.get("funcao") == "superadmin":
            return False
        user["status"]     = "revoked"
        user["token_version"] = int(user.get("token_version", 0)) + 1
        user["updated_at"] = _now()
        _save()
        return True


def reactivate_user(email: str) -> bool:
    with _lock:
        user = _users.get(email)
        if not user:
            return False
        user["status"]     = "active"
        user["updated_at"] = _now()
        _save()
        return True


def reset_password(email: str) -> bool:
    with _lock:
        user = _users.get(email)
        # Conta revogada ou ainda pendente de ativacao nao pode reganhar acesso
        # pelo fluxo de "esqueci minha senha" — precisa de reativacao explicita
        # do admin. Sem essa checagem, "esqueci senha" reativava contas que um
        # admin tinha revogado de proposito.
        if not user or user.get("status") != "active":
            return False
        password = secrets.token_urlsafe(10)
        user["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user["first_access"]  = True
        user["token_version"] = int(user.get("token_version", 0)) + 1
        user["updated_at"]    = _now()
        _save()
    _send_password_reset_email(email, password)
    return True


def delete_user(email: str) -> bool:
    with _lock:
        user = _users.get(email)
        if not user or user.get("funcao") == "superadmin":
            return False
        del _users[email]
        _save()
        return True


def remove_instance_from_all_users(instance_id: str) -> list[str]:
    """
    Chamado quando uma instancia e deletada. Tira o instance_id da lista de
    acesso de todo mundo; quem ficar sem nenhuma instancia restante tem a
    conta removida junto (evita acumular login "zumbi" sem nada pra acessar).
    O superadmin tambem tem as referencias removidas, mas sua conta nunca e
    excluida quando a ultima instancia desaparece. Retorna os emails removidos.
    """
    removed: list[str] = []
    with _lock:
        for email, user in list(_users.items()):
            owned = user.get("owned_instances")
            if owned and instance_id in owned:
                user["owned_instances"] = [i for i in owned if i != instance_id]
            allowed = user.get("allowed_instances")
            if not allowed or instance_id not in allowed:
                continue
            user["allowed_instances"] = [i for i in allowed if i != instance_id]
            if not user["allowed_instances"] and user.get("funcao") != "superadmin":
                del _users[email]
                removed.append(email)
        _save()
    return removed


def list_users() -> list:
    with _lock:
        return [_safe_user(u) for u in _users.values()]


def superadmin_instance_ids() -> set[str]:
    """Todas as instancias que pertencem a algum superadmin (conta mestre)."""
    ids: set[str] = set()
    with _lock:
        for u in _users.values():
            if u.get("funcao") == "superadmin":
                ids.update(u.get("allowed_instances") or [])
    return ids


def team_instance_owners() -> dict:
    """
    Mapa instance_id -> dono de EQUIPE (gerente/sdr que criou a instância via
    owned_instances). Instância nesse mapa é "número de time" — sai da grade
    do dashboard e passa a morar na aba Usuários (ver task #9). Instância de
    admin/superadmin (conta de empresa/cliente) fica fora de propósito: essas
    continuam no dashboard e o dono do sistema não vê as conversas delas.
    """
    owners: dict = {}
    with _lock:
        for u in _users.values():
            if u.get("funcao") not in ("gerente", "sdr"):
                continue
            for iid in (u.get("owned_instances") or []):
                owners[iid] = {
                    "email":  u.get("email", ""),
                    "nome":   u.get("nome", ""),
                    "funcao": u.get("funcao", ""),
                }
    return owners


def list_users_for(actor: dict) -> list:
    """
    Superadmin ve todo mundo. Admin ve so gerente/sdr que tem acesso a alguma
    das instancias dele — nunca outros admins nem usuarios de instancias que
    nao sao suas.
    """
    if actor.get("funcao") == "superadmin":
        return list_users()
    actor_instances = set(actor.get("allowed_instances") or [])
    actor_email = actor.get("email", "")
    with _lock:
        return [
            _safe_user(u) for u in _users.values()
            if u.get("funcao") in ("gerente", "sdr")
            and (
                actor_instances.intersection(u.get("allowed_instances") or [])
                or u.get("invited_by") == actor_email
            )
        ]


def can_manage_target_user(actor: dict, target: dict) -> bool:
    """
    Superadmin gerencia qualquer um (exceto outro superadmin, ja bloqueado
    em cada endpoint). Admin so gerencia gerente/sdr cujas instancias
    permitidas estejam inteiramente dentro das instancias dele.
    """
    if actor.get("funcao") == "superadmin":
        return True
    if actor.get("funcao") != "admin":
        return False
    if target.get("funcao") not in ("gerente", "sdr"):
        return False
    if target.get("invited_by") == actor.get("email", ""):
        return True
    actor_instances = set(actor.get("allowed_instances") or [])
    target_instances = set(target.get("allowed_instances") or [])
    return bool(target_instances) and target_instances.issubset(actor_instances)


def get_tenant_admin(user: dict) -> dict | None:
    """
    Acha o admin/superadmin dono do tenant de um gerente/sdr — usado quando
    ele cria a própria instância, pra replicar o acesso automaticamente pro
    admin (ver create_instance/create_channel em routers/instances.py).
    Prioriza invited_by (funciona mesmo sem nenhuma instancia ainda); cai
    pro lookup por instancia compartilhada pra usuarios convidados antes
    desse campo existir.
    """
    with _lock:
        invited_by = user.get("invited_by")
        if invited_by and invited_by in _users:
            return dict(_users[invited_by])
        allowed = set(user.get("allowed_instances") or [])
        if allowed:
            for u in _users.values():
                if u.get("funcao") in ("admin", "superadmin") and allowed & set(u.get("allowed_instances") or []):
                    return dict(u)
    return None


def get_user_by_email(email: str) -> dict | None:
    with _lock:
        user = _users.get(email)
        return dict(user) if user else None


# Campos sensíveis que nunca podem ir ao frontend: hash de senha, segredo TOTP
# (gera códigos válidos) e os hashes dos backup codes.
_UNSAFE_USER_FIELDS = ("password_hash", "totp_secret", "totp_backup_codes")


def _safe_user(user: dict) -> dict:
    """Remove campos sensíveis (senha, segredo 2FA) antes de retornar ao frontend."""
    safe = {k: v for k, v in user.items() if k not in _UNSAFE_USER_FIELDS}
    safe.setdefault("telefone", "")
    # Só expõe o booleano de estado do 2FA, nunca o segredo em si.
    safe["totp_enabled"] = bool(user.get("totp_enabled"))
    # Plano EFETIVO para o frontend gatar UI (time herda do dono; superadmin "").
    # `plano` continua sendo o plano bruto do próprio usuário (exibição no perfil).
    safe["effective_plan"] = effective_plan_for_user(user)
    if "email_verified" not in safe:
        # Contas anteriores a este campo: quem tem senha a recebeu por e-mail
        # (convite/reset/pagamento), o que já prova a posse da caixa. Cadastros
        # pendentes da landing (sem senha ainda) continuam não verificados.
        safe["email_verified"] = bool(user.get("password_hash"))
    return safe
