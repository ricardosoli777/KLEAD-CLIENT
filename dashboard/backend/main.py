"""
KLEAD Backend — FastAPI
Todos os dados são isolados por instance_id.
Estado e credenciais vivem no PostgreSQL (banco "klead") — única fonte de
verdade; credenciais sensíveis são criptografadas com chave derivada de
DASHBOARD_SECRET antes de persistir.
"""

import os
import io
import csv
import copy
import json
import time
import base64
import hashlib
import hmac
import random
import secrets
import re
import shutil
import atexit
import threading
import logging
from contextlib import asynccontextmanager
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Security, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse, Response, PlainTextResponse, JSONResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import services.crm_service as crm_svc
import services.auth_service as auth_svc
import services.assignment_service as assignment_svc
import services.patterns_service as patterns_svc
from services import conversation_pg_service, meta_pg_service, instances_pg_service, settings_pg_service
from services import (
    meta_oauth_pg_service,
    meta_oauth_service,
    identity_resolver_service,
    identity_alias_pg_service,
)
from services.meta_capi_service import (
    get_meta_capi_audit,
    preview_meta_capi_event,
    queue_meta_capi_event,
    send_meta_capi_event,
    enqueue_capi_retry,
    load_capi_retry_queue,
    remove_capi_retry_item,
    is_permanent_capi_failure,
    is_meta_capi_enabled,
)
from services.outbound_routing import send_outbound_text, can_send_outbound_audio, has_evolution_outbound
from services.ads_analyzer_service import analyze_meta_ads, chat_meta_ads
from services.campaign_service import (
    run_campaign, stop_campaign,
    run_campaign_from_queue, generate_queue_thread,
    load_sent_numbers, save_history, clear_sent_numbers,
    load_queue, clear_queue,
    purge_instance_data,
    HISTORY_FILE,
)
from services.openai_service import generate_message
from services.evolution_service import send_whatsapp_message, download_media_bytes, check_connection_state
import services.media_service as media_svc
from services.transcription_service import transcribe_audio
from services.meta_service import check_meta_connection
from services.responder_service import qualify_lead
from services.patterns_service import record_pattern, build_knowledge_from_patterns
from utils.file_parser import parse_file
from utils.cleaner import clean_contacts, normalize_phone, normalize_name
from utils.instance_id import is_safe_instance_id
from services.auth_service import (
    get_auth_user as _get_auth_user,
    require_admin as _require_admin,
    require_manager as _require_manager,
    require_superadmin as _require_superadmin,
    auth_cookie_options as _auth_cookie_options,
)
from core.state import instances, conversations, instances_lock, conversations_lock
from core.sse import emit as _sse_emit, _sse_listeners, _sse_lock

load_dotenv()

_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        # Não envia dados pessoais (telefone/nome de lead) junto do erro por padrão.
        send_default_pii=False,
        # Amostra de performance baixa — só queremos erros, não rastrear toda requisição.
        traces_sample_rate=0.05,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("klead")


class _RedactSensitiveQueryFilter(logging.Filter):
    """Redige valores de query string sensíveis (code/state/token OAuth e webhook)
    no access log do uvicorn — sem isso, cada callback OAuth deixa o code/state
    e cada webhook deixa o token gravados em texto claro no log."""

    _PATTERN = re.compile(r"(?i)\b(code|state|token|access_token|hub\.verify_token)=([^&\s\"]+)")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # O AccessFormatter do uvicorn desempacota record.args esperando a
            # 5-tupla (client, method, path, http_version, status) — zerar args
            # quebra o formatter com ValueError. Redige dentro dos args.
            if isinstance(record.args, tuple) and record.args:
                record.args = tuple(
                    self._PATTERN.sub(r"\1=[redacted]", a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.msg, str):
                record.msg = self._PATTERN.sub(r"\1=[redacted]", record.msg)
        except Exception:
            logger.debug("Falha ao aplicar filtro de log", exc_info=True)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactSensitiveQueryFilter())
if _SENTRY_DSN:
    logger.info("[Sentry] Monitoramento de erros ativado.")


def _load_config_from_db() -> dict:
    """Lê todas as chaves de klead_config no postgres e retorna como dict.
    Fallback silencioso: se o banco não estiver acessível, retorna dict vazio."""
    try:
        import psycopg2
        host = os.getenv("EVO_PG_HOST", "postgres")
        user = os.getenv("EVO_PG_USER", "postgres")
        pwd  = os.getenv("EVO_PG_PASSWORD", "")
        if not pwd:
            return {}
        # klead_config vive no banco do app ("klead"), que está no backup diário —
        # o banco "postgres" não é coberto pelo klead-pg-backup.sh.
        conn = psycopg2.connect(host=host, user=user, password=pwd,
                                dbname=os.getenv("INST_PG_DB") or "klead", connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM klead_config")
        cfg = {row[0]: row[1] for row in cur.fetchall()}
        cur.close(); conn.close()
        return cfg
    except Exception:
        return {}


# Carrega configs do banco e sobrescreve env vars em memória
_db_cfg = _load_config_from_db()
for _k, _v in _db_cfg.items():
    os.environ.setdefault(_k, _v)
# Valores do banco têm prioridade sobre .env
for _k, _v in _db_cfg.items():
    os.environ[_k] = _v

DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
if WEBHOOK_SECRET:
    logging.getLogger("uvicorn.error").warning(
        "[DEPRECATED] WEBHOOK_SECRET global está definido. Prefira o webhook_secret "
        "por instância (multi-tenant); o segredo global será removido em versão futura."
    )
META_APP_SECRET  = os.getenv("META_APP_SECRET", "")
ALLOWED_ORIGINS  = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")]
COOKIE_SECURE    = os.getenv("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes", "on")
DATA_DIR         = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_DIR    = DATA_DIR / "knowledge"
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
INBOX_KB_DIR     = DATA_DIR / "inbox_knowledge"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
ALERTS_FILE  = DATA_DIR / "alerts.json"
_alerts_lock = threading.Lock()
_MAX_ALERTS  = 200

if not DASHBOARD_SECRET:
    raise RuntimeError("DASHBOARD_SECRET não configurado no servidor.")
_wh_global = os.getenv("WEBHOOK_SECRET", "")
if _wh_global and _wh_global == DASHBOARD_SECRET:
    logging.getLogger("uvicorn.error").warning(
        "WEBHOOK_SECRET é igual ao DASHBOARD_SECRET — use chaves distintas para cada função."
    )

def _safe_instance_path_part(instance_id: str) -> str:
    if not is_safe_instance_id(instance_id):
        raise HTTPException(400, "instance_id inválido.")
    return instance_id


_SAFE_FILENAME_RE = re.compile(r'^[\w\-. ]{1,200}\.(pdf|txt|md|docx|csv|xlsx)$', re.IGNORECASE)

def _safe_filename(filename: str) -> str:
    name = Path(filename or "").name
    if not name or not _SAFE_FILENAME_RE.fullmatch(name):
        raise HTTPException(400, "Nome de arquivo inválido.")
    return name

def _validate_api_url(url: str) -> str:
    import ipaddress, socket, urllib.parse
    if not url:
        return url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "api_url deve usar http ou https")
    host = (parsed.hostname or "").strip().lower()
    if host == "host.docker.internal":
        return url
    if host == "localhost" or host.endswith(".localhost"):
        raise HTTPException(400, "api_url não pode apontar para localhost")
    # Nomes de serviço Docker (sem ponto no hostname) são endereços internos válidos
    if "." not in host:
        return url

    def _is_internal_address(value: str) -> bool:
        addr = ipaddress.ip_address(value)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )

    try:
        if _is_internal_address(host):
            raise HTTPException(400, "api_url não pode apontar para endereço interno de rede")
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
        except socket.gaierror:
            resolved = []
        for item in resolved:
            resolved_host = item[4][0]
            try:
                if _is_internal_address(resolved_host):
                    raise HTTPException(400, "api_url não pode resolver para endereço interno de rede")
            except ValueError:
                continue
    return url


def _normalize_meta_ad_account_id(ad_account_id: str) -> str:
    """Garante o formato `act_<id>` esperado pela Graph API de Ads."""
    value = (ad_account_id or "").strip()
    if not value:
        return ""
    if value.startswith("act_"):
        return value
    return f"act_{value}"


# -- Criptografia de credenciais em disco ---------------------------------------

_SENSITIVE_FIELDS = ("api_key", "openai_key", "llm_api_key", "meta_token", "meta_app_secret",
                     "meta_capi_access_token", "meta_page_access_token", "global_api_key",
                     "key_openai", "key_anthropic", "key_groq", "evo_global_api_key")

_PROVIDER_KEY_FIELD = {"openai": "key_openai", "anthropic": "key_anthropic", "groq": "key_groq"}


def _make_fernet():
    """
    Deriva chave Fernet global a partir do DASHBOARD_SECRET.
    Mantida apenas como fallback de LEITURA de dados legados (criptografados
    antes da introdução de chave por instância) e para o ai_config global,
    que não tem instance_id associado. Nunca mais usada para gravar
    credenciais de instância — ver _make_fernet_for_instance().
    """
    try:
        from cryptography.fernet import Fernet
        if not DASHBOARD_SECRET:
            return None
        key = base64.urlsafe_b64encode(hashlib.sha256(DASHBOARD_SECRET.encode()).digest())
        return Fernet(key)
    except ImportError:
        return None


def _make_fernet_for_instance(instance_id: str):
    """
    Deriva uma chave Fernet exclusiva da instância via HKDF-SHA256, usando
    DASHBOARD_SECRET como segredo mestre e instance_id como salt/info.
    Isola o raio de exposição: vazar a chave derivada (ou o ciphertext) de
    uma instância não ajuda a decifrar as credenciais de outra instância
    sem o DASHBOARD_SECRET original.
    """
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        if not DASHBOARD_SECRET or not instance_id:
            return None
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=instance_id.encode(),
            info=b"klead-instance-credentials",
        ).derive(DASHBOARD_SECRET.encode())
        return Fernet(base64.urlsafe_b64encode(derived))
    except ImportError:
        return None


def _make_fernet_for_user(email: str):
    """
    Deriva uma chave Fernet exclusiva do usuário (por e-mail) via HKDF-SHA256.
    Mesmo mecanismo de _make_fernet_for_instance, mas com salt/info diferentes
    para isolar o domínio de derivação — usada para credenciais de conexão
    OAuth (meta_connections). O vínculo é exclusivo da instância, enquanto a
    chave continua derivada do e-mail que realizou a autorização.
    """
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        if not DASHBOARD_SECRET or not email:
            return None
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=email.encode(),
            info=b"klead-meta-oauth-credentials",
        ).derive(DASHBOARD_SECRET.encode())
        return Fernet(base64.urlsafe_b64encode(derived))
    except ImportError:
        return None


def _encrypt_field(fernet, value: str) -> str:
    if not value:
        return value
    # Fail-closed: sem Fernet disponível (lib/secret ausente), recusar a
    # gravação em vez de persistir o segredo em texto claro silenciosamente.
    # cryptography é dependência fixa e DASHBOARD_SECRET é obrigatório — isto
    # só dispara em ambiente mal configurado, onde falhar alto é o correto.
    if not fernet:
        raise RuntimeError(
            "Criptografia indisponível (cryptography/DASHBOARD_SECRET) — "
            "recusando gravar credencial em texto claro."
        )
    return fernet.encrypt(value.encode()).decode()


def _decrypt_field(fernet, value: str) -> str:
    if not fernet or not value:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        return value  # já estava em texto puro — migração transparente


def _decrypt_field_for_instance(instance_id: str, value: str, legacy_fernet) -> str:
    """
    Tenta descriptografar com a chave derivada da instância; se falhar
    (dado legado, gravado antes da migração), cai para a chave global antiga;
    se falhar também, assume texto puro (comportamento pré-existente).
    """
    if not value:
        return value
    inst_fernet = _make_fernet_for_instance(instance_id)
    if inst_fernet:
        try:
            return inst_fernet.decrypt(value.encode()).decode()
        except Exception:
            logger.debug("Falha em migração/backup opcional", exc_info=True)
    return _decrypt_field(legacy_fernet, value)


def _decrypt_field_for_user(email: str, value: str) -> str:
    """Descriptografa meta_connections com o e-mail que autorizou a instância."""
    if not value:
        return value
    user_fernet = _make_fernet_for_user(email)
    if not user_fernet:
        return value
    try:
        return user_fernet.decrypt(value.encode()).decode()
    except Exception:
        return value  # texto puro ou chave indisponível


def _resolve_openai_key(inst: dict) -> str:
    """Retorna a API key ativa para geracao de mensagens."""
    return _resolve_ai_settings(inst)["api_key"]


def _resolve_ai_settings(inst: dict) -> dict:
    """Resolve provedor/modelo/chave — chave propria da instancia tem prioridade; IA Global e o fallback da conta mestre."""
    inst_provider = inst.get("llm_provider") or "groq"
    inst_model = inst.get("model") or inst.get("llm_model") or "openai/gpt-oss-120b"
    inst_key_field = _PROVIDER_KEY_FIELD.get(inst_provider, "key_openai")
    inst_key = (
        inst.get(inst_key_field)
        or inst.get("llm_api_key")
        or inst.get("key_openai")
        or inst.get("openai_key")
        or ""
    )
    if inst_key:
        return {"provider": inst_provider, "model": inst_model, "api_key": inst_key, "source": "instance"}

    global_provider = ai_config.get("llm_provider") or "groq"
    global_model = ai_config.get("llm_model") or "openai/gpt-oss-120b"
    global_key_field = _PROVIDER_KEY_FIELD.get(global_provider, "key_openai")
    global_key = ai_config.get(global_key_field) or ai_config.get("llm_api_key") or ai_config.get("key_openai") or ""
    if global_key:
        return {"provider": global_provider, "model": global_model, "api_key": global_key, "source": "global"}

    return {"provider": global_provider, "model": global_model, "api_key": "", "source": "missing"}


from core.limiter import limiter

_write_lock = threading.Lock()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    crm_svc.init_storage()
    init_conversations_storage()
    if identity_alias_pg_service.enabled():
        ok, detail = identity_alias_pg_service.load_cache()
        if ok:
            logger.info("[IDENTITY-ALIASES] Cache carregado com %s aliases persistidos.", detail)
        else:
            logger.warning("[IDENTITY-ALIASES] Persistência indisponível nesta execução: %s", detail)
    try:
        meta_pg_service.ensure_schema()
    except Exception:
        logger.debug("Falha ao resolver webhook da instância", exc_info=True)
    try:
        from services import confirmation_pg_service
        ok, detail = confirmation_pg_service.ensure_schema()
        if not ok and detail != "postgres_not_configured":
            logger.warning("[CONFIRMATIONS] Falha ao garantir schema: %s", detail)
    except Exception:
        logger.exception("[CONFIRMATIONS] Erro ao inicializar schema")
    try:
        from services import inbox_poll_pg_service
        ok, detail = inbox_poll_pg_service.ensure_schema()
        if not ok and detail != "postgres_not_configured":
            logger.warning("[INBOX-POLLS] Falha ao garantir schema: %s", detail)
    except Exception:
        logger.exception("[INBOX-POLLS] Erro ao inicializar schema")
    try:
        import services.kb_embedding_service as kb_emb
        kb_emb.ensure_table()
    except Exception:
        logger.debug("Falha ao sincronizar webhook", exc_info=True)
    auth_svc.load_users()
    assignment_svc.load_assignments()
    _migrate_evolution_url()
    if _should_backup():
        try:
            _do_data_backup("startup")
        except Exception:
            logger.debug("Falha ao sincronizar webhook (extra)", exc_info=True)
    threading.Thread(target=_sync_webhooks_postgres, daemon=True, name="webhook-sync").start()
    from services.scheduler import scheduler
    scheduler.schedule_recurring("auto-save", 60, _auto_save_tick, initial_delay=60)
    scheduler.schedule_recurring("crm-backup", 3600, _crm_periodic_backup_tick, initial_delay=3600)
    scheduler.schedule_recurring("capi-retry", 60, _capi_retry_tick, initial_delay=60)
    scheduler.schedule_recurring("followup-worker", 60, _followup_tick, initial_delay=60)
    scheduler.start()
    from routers.conversations import start_auto_patterns_loop, start_auto_agent_memory_loop
    start_auto_patterns_loop(app)
    start_auto_agent_memory_loop(app)
    yield


app = FastAPI(title="KLEAD API", version="1.0.0", lifespan=_lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RequestValidationError)
async def _log_validation_error(request: Request, exc: RequestValidationError):
    # 422 sem detalhe nenhum no log padrão do uvicorn — loga a estrutura do erro
    # pra não precisar reproduzir às cegas quando um upload falha na validação.
    # O campo "input" (valor enviado) fica de fora do LOG: pode carregar PII de
    # lead ou credencial; a resposta ao chamador continua completa.
    safe_errors = [
        {k: v for k, v in err.items() if k not in ("input", "ctx")}
        for err in exc.errors()
    ]
    logger.warning(f"[422] {request.method} {request.url.path}: {safe_errors}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
)

from routers.auth import router as _auth_router
app.include_router(_auth_router)

from routers.assignments import router as _assignments_router
app.include_router(_assignments_router)

from routers.crm import router as _crm_router
app.include_router(_crm_router)

from routers.conversations import router as _conversations_router
app.include_router(_conversations_router)

from routers.inbox_polls import router as _inbox_polls_router
app.include_router(_inbox_polls_router)

from routers.webhook import router as _webhook_router
app.include_router(_webhook_router)

from routers.instances import router as _instances_router
app.include_router(_instances_router)

from routers.admin import router as _admin_router
app.include_router(_admin_router)

from routers.billing import router as _billing_router
app.include_router(_billing_router)

from routers.media import router as _media_router
app.include_router(_media_router)

from routers.meta_oauth import router as _meta_oauth_router
app.include_router(_meta_oauth_router)

from routers.guide import router as _guide_router
app.include_router(_guide_router)


from starlette.concurrency import run_in_threadpool


@app.get("/health", include_in_schema=False)
async def health():
    # SELECT 1 barato via threadpool — nunca DDL nem I/O síncrono no event loop
    # (DDL sem lock_timeout aqui já congelou o servidor inteiro em produção).
    pg_ok, pg_detail = await run_in_threadpool(instances_pg_service.ping)
    return {
        "status": "ok",
        "postgres": pg_ok,
        "postgres_detail": pg_detail or "connected",
        "instances": len(instances),
    }


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


# -- Autenticação ---------------------------------------------------------------

from core.security import api_key_header, require_api_key


_INSTANCE_ID_RE = re.compile(
    r"^/(?:instance|upload-contacts|contacts|stop-campaign|status|logs|sends"
    r"|conversations|generate-queue|queue|send-queue|knowledge|whatsapp-status"
    r"|reset-instance|import-contacts-text|sdr-config)/([^/?]+)"
)
_WEBHOOK_INSTANCE_RE = re.compile(r"^/webhook/(?:url|setup|check|meta)/([^/?]+)")


@app.middleware("http")
async def instance_access_middleware(request: Request, call_next):
    path = request.url.path
    instance_id = None
    m = _INSTANCE_ID_RE.match(path)
    if m:
        instance_id = m.group(1)
    else:
        m = _WEBHOOK_INSTANCE_RE.match(path)
        if m:
            instance_id = m.group(1)

    if instance_id:
        # Falha fechado: toda rota aqui é ação de dashboard autenticada (nunca
        # webhook publico nem chamada interna sem usuario) — sem token valido
        # ou sem acesso a essa instancia, nega. O proxy do frontend sempre
        # anexa o token de quem esta logado; so falta em uso indevido direto
        # da API com a chave interna, o que nao deve bastar pra ler dado de
        # tenant alheio.
        token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
        # verify_token adquire o _lock do auth — precisa rodar no threadpool.
        # Chamado direto aqui (async), um lock ocupado congelaria o event loop
        # e derrubaria o app inteiro (todas as rotas, todos os usuários).
        user = (await run_in_threadpool(auth_svc.verify_token, token)) if token else None
        if not user or not auth_svc.can_access_instance(user, instance_id):
            return Response(
                content='{"detail":"Acesso negado a esta instância"}',
                status_code=403,
                media_type="application/json",
            )

    return await call_next(request)


# -- Persistência de credenciais ------------------------------------------------

def _atomic_write(path: Path, text: str):
    """Escreve em arquivo temporário e renomeia atomicamente — seguro contra crash e race condition."""
    with _write_lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


app.state.instances_lock = instances_lock
app.state.conversations_lock = conversations_lock


# -- Sistema de alertas ---------------------------------------------------------

def _append_alert(alert_type: str, message: str, instance_id: str = "", detail: str = "") -> None:
    """Registra um alerta estruturado em alerts.json (máximo _MAX_ALERTS entradas)."""
    entry = {
        "id": f"{int(time.time() * 1000)}:{alert_type}:{instance_id}",
        "type": alert_type,
        "instance_id": instance_id,
        "message": message,
        "detail": detail[:500] if detail else "",
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with _alerts_lock:
        try:
            alerts: list = json.loads(ALERTS_FILE.read_text(encoding="utf-8")) if ALERTS_FILE.exists() else []
        except Exception:
            alerts = []
        alerts.append(entry)
        if len(alerts) > _MAX_ALERTS:
            alerts = alerts[-_MAX_ALERTS:]
        _atomic_write(ALERTS_FILE, json.dumps(alerts, ensure_ascii=False, indent=2))


def _load_alerts(instance_id: str = "", limit: int = 50) -> list:
    with _alerts_lock:
        if not ALERTS_FILE.exists():
            return []
        try:
            alerts = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    if instance_id:
        alerts = [a for a in alerts if a.get("instance_id") == instance_id]
    return alerts[-limit:]

# Debounce: timer cancelável por conversa — nova mensagem cancela o timer anterior.
# Garante que o SDR responda apenas uma vez ao contexto completo do burst de mensagens.
_pending_timers: dict[str, threading.Timer] = {}
_pending_timers_lock = threading.Lock()

# Cache de saúde Meta por instância — evita bater na API a cada refresh
_meta_health_cache: dict[str, dict] = {}
_META_HEALTH_TTL = 300  # segundos


def _build_instance_config(iid: str, inst: dict) -> dict:
    """Monta o dict de config persistível (credenciais criptografadas) de UMA instância.
    Compartilhado por _save_configs (sync completo) e _save_instance_config (upsert
    pontual) — nunca deixar divergir."""
    fernet = _make_fernet_for_instance(iid)
    return {
                "api_url":               inst["api_url"],
                "api_key":               _encrypt_field(fernet, inst["api_key"]),
                "openai_key":            _encrypt_field(fernet, inst["openai_key"]),
                "prompt":                inst["prompt"],
                "contacts":              inst["contacts"],
                "provider":              inst.get("provider", "evolution"),
                "meta_token":            _encrypt_field(fernet, inst.get("meta_token", "")),
                "meta_app_secret":       _encrypt_field(fernet, inst.get("meta_app_secret", "")),
                "meta_capi_access_token": _encrypt_field(fernet, inst.get("meta_capi_access_token", "")),
                "phone_number_id":       inst.get("phone_number_id", ""),
                "template_name":         inst.get("template_name", ""),
                "meta_page_id":              inst.get("meta_page_id", ""),
                "meta_page_access_token":    _encrypt_field(fernet, inst.get("meta_page_access_token", "")),
                "meta_capi_enabled":         inst.get("meta_capi_enabled", False),
                "meta_capi_event_source_id": inst.get("meta_capi_event_source_id", ""),
                "meta_capi_test_event_code": inst.get("meta_capi_test_event_code", ""),
                "meta_ad_account_id":    _normalize_meta_ad_account_id(inst.get("meta_ad_account_id", "")),
                "meta_page_subscribed":  inst.get("meta_page_subscribed", False),
                "meta_waba_subscribed":  inst.get("meta_waba_subscribed", False),
                "inbound_ai_enabled":    inst.get("inbound_ai_enabled", True),
                "qualification_prompt":  inst.get("qualification_prompt", ""),
                "max_ai_turns":          inst.get("max_ai_turns", 8),
                "ai_response_delay":     inst.get("ai_response_delay", 3),
                "auto_handoff_keywords": inst.get("auto_handoff_keywords", []),
                "model":                 inst.get("model", inst.get("llm_model", "openai/gpt-oss-120b")),
                "llm_model":             inst.get("llm_model", inst.get("model", "openai/gpt-oss-120b")),
                "llm_provider":          inst.get("llm_provider", "groq"),
                "llm_api_key":           _encrypt_field(fernet, inst.get("llm_api_key", "")),
                "key_openai":            _encrypt_field(fernet, inst.get("key_openai", "")),
                "key_anthropic":         _encrypt_field(fernet, inst.get("key_anthropic", "")),
                "key_groq":              _encrypt_field(fernet, inst.get("key_groq", "")),
                "followup_enabled":      inst.get("followup_enabled", True),
                "followup_delay_hours":  inst.get("followup_delay_hours", 24),
                "followup_max":          inst.get("followup_max", 3),
                "scoring_criteria":      inst.get("scoring_criteria", ""),
                "evo_instance_name":     inst.get("evo_instance_name", ""),
                "auto_release_minutes":  inst.get("auto_release_minutes", 3),
                "tts_enabled":           inst.get("tts_enabled", False),
                "auto_suggest_enabled":  inst.get("auto_suggest_enabled", True),
                # webhook_secret: texto plano por design (token de verificação de
                # webhook, não credencial de acesso — ver plano de por-instância).
                "webhook_secret":        inst.get("webhook_secret", ""),
    }


def _save_configs():
    """Sincroniza TODAS as instâncias com o Postgres (upsert de tudo + delete das
    ausentes). Fora do caminho por-request: usado no boot (re-encrypt), em mudanças
    multi-instância e como reparo de deriva periódico. Para mudança em UMA
    instância, use _save_instance_config(instance_id)."""
    with instances_lock:
        configs = {iid: _build_instance_config(iid, inst) for iid, inst in instances.items()}
    if not instances_pg_service.pg_configured():
        logger.warning("[Instances] Postgres não configurado — config NÃO persistida (modo dev/teste).")
        return
    try:
        ok, detail = instances_pg_service.sync_all(configs)
        if not ok:
            logger.warning(f"[Instances] Falha ao sincronizar com Postgres: {detail}")
    except Exception as exc:
        logger.warning(f"[Instances] Erro inesperado ao sincronizar: {exc}")


# Instâncias cujo upsert incremental falhou — o auto-save loop retenta a cada 60s.
_dirty_instances: set[str] = set()
_dirty_instances_lock = threading.Lock()


def _save_instance_config(instance_id: str) -> bool:
    """Persiste a config de UMA instância no Postgres (upsert pontual) — evita
    re-criptografar e regravar todas as instâncias a cada mudança de config."""
    with instances_lock:
        inst = instances.get(instance_id)
        if inst is None:
            return False  # deletada em paralelo — a deleção explícita cuida do Postgres
        cfg = _build_instance_config(instance_id, inst)
    if not instances_pg_service.pg_configured():
        logger.warning("[Instances] Postgres não configurado — config NÃO persistida (modo dev/teste).")
        return False
    try:
        ok, detail = instances_pg_service.upsert_instance(instance_id, cfg)
    except Exception as exc:
        ok, detail = False, str(exc)
    if not ok:
        with _dirty_instances_lock:
            _dirty_instances.add(instance_id)
        logger.warning(f"[Instances] Upsert falhou para {instance_id} (retry agendado): {detail}")
    return ok


def _flush_dirty_instances():
    """Retenta upserts de instância que falharam (chamado pelo auto-save loop)."""
    with _dirty_instances_lock:
        pending = list(_dirty_instances)
        _dirty_instances.clear()
    for iid in pending:
        _save_instance_config(iid)


def _delete_instance_config_from_pg(instance_id: str):
    """Remove a linha da instância no Postgres após removê-la da memória."""
    if not instances_pg_service.pg_configured():
        return
    ok, detail = instances_pg_service.delete_instance(instance_id)
    if not ok:
        logger.warning(f"[Instances] Falha ao deletar {instance_id} no Postgres: {detail}")


def _default_ai_config() -> dict:
    return {
        "llm_provider": "groq",
        "llm_model": "openai/gpt-oss-120b",
        "llm_api_key": "",
        "key_openai": "",
        "key_anthropic": "",
        "key_groq": "",
        "evo_global_api_key": "",
    }


def _load_ai_config() -> dict:
    """
    Carrega o ai_config do Postgres — única fonte de verdade.
    Chave ausente = instalação nova (usa defaults). Qualquer outra falha com PG
    configurado aborta o startup: subir com defaults e salvar depois
    sobrescreveria a config real no banco.
    """
    cfg = _default_ai_config()
    if settings_pg_service.pg_configured():
        ok, raw_pg = settings_pg_service.load_setting("ai_config")
        if ok and isinstance(raw_pg, dict):
            fernet = _make_fernet()
            for field in _SENSITIVE_FIELDS:
                if field in raw_pg:
                    raw_pg[field] = _decrypt_field(fernet, raw_pg[field])
            cfg.update(raw_pg)
        elif "não encontrada" not in str(raw_pg):
            raise RuntimeError(f"[AI Config] Falha ao carregar do Postgres — abortando: {raw_pg}")
    else:
        logger.warning("[AI Config] Postgres não configurado — usando defaults (modo dev/teste).")
    # Env vars têm prioridade — fonte única de verdade
    env_evo_key = os.getenv("EVO_GLOBAL_API_KEY", "").strip()
    if env_evo_key:
        cfg["evo_global_api_key"] = env_evo_key
    for variable, field in (("OPENAI_API_KEY", "key_openai"),
                            ("ANTHROPIC_API_KEY", "key_anthropic"),
                            ("GROQ_API_KEY", "key_groq")):
        value = os.getenv(variable, "").strip()
        if value:
            cfg[field] = value
    provider = os.getenv("KLEAD_LLM_PROVIDER", "").strip()
    if provider in {"openai", "anthropic", "groq"}:
        cfg["llm_provider"] = provider
        cfg["llm_api_key"] = cfg.get(f"key_{provider}", "")
        cfg["llm_model"] = os.getenv("KLEAD_LLM_MODEL", "").strip() or {
            "openai": "gpt-4o-mini", "anthropic": "claude-sonnet-4-20250514",
            "groq": "openai/gpt-oss-120b",
        }[provider]
    return cfg


def _save_ai_config():
    fernet = _make_fernet()
    raw = dict(ai_config)
    for field in _SENSITIVE_FIELDS:
        if field in raw:
            raw[field] = _encrypt_field(fernet, raw[field])
    if not settings_pg_service.pg_configured():
        logger.warning("[AI Config] Postgres não configurado — config NÃO persistida (modo dev/teste).")
        return
    try:
        ok, detail = settings_pg_service.save_setting("ai_config", raw)
        if not ok:
            logger.warning(f"[AI Config] Falha ao sincronizar com Postgres: {detail}")
    except Exception as exc:
        logger.warning(f"[AI Config] Erro inesperado ao sincronizar: {exc}")


def _apply_global_ai_defaults_to_instance(instance_id: str):
    """Injeta fallback global em memoria quando a instancia nao tem chave propria."""
    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            return
    settings = _resolve_ai_settings(inst)
    if settings["source"] != "global":
        return
    provider = settings["provider"]
    key_field = _PROVIDER_KEY_FIELD.get(provider, "key_openai")
    inst["llm_provider"] = provider
    inst["model"] = settings["model"]
    inst["llm_model"] = settings["model"]
    inst["llm_api_key"] = settings["api_key"]
    inst[key_field] = settings["api_key"]


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) > 4 else value
    return f"********{tail}"


def _is_masked_secret(value: str) -> bool:
    return bool(value) and value.startswith("********")


def _ai_config_response() -> dict:
    return {
        "llm_provider": ai_config.get("llm_provider", "groq"),
        "llm_model": ai_config.get("llm_model", "openai/gpt-oss-120b"),
        "llm_api_key": _mask_secret(ai_config.get("llm_api_key", "")),
        "key_openai": _mask_secret(ai_config.get("key_openai", "")),
        "key_anthropic": _mask_secret(ai_config.get("key_anthropic", "")),
        "key_groq": _mask_secret(ai_config.get("key_groq", "")),
        "evo_global_api_key": _mask_secret(ai_config.get("evo_global_api_key", "")),
    }


def _instance_public_response(inst: dict, request: Request | None = None) -> dict:
    can_manage_credentials = False
    if request is not None:
        try:
            user, _ = _get_auth_user(request)
            can_manage_credentials = user.get("funcao") in ("superadmin", "admin")
        except Exception:
            can_manage_credentials = False

    return {
        "api_url":               inst["api_url"],
        "prompt":                inst["prompt"],
        "provider":              inst.get("provider", "evolution"),
        "inbound_ai_enabled":    inst.get("inbound_ai_enabled", True),
        "qualification_prompt":  inst.get("qualification_prompt", ""),
        "max_ai_turns":          inst.get("max_ai_turns", 8),
        "ai_response_delay":     inst.get("ai_response_delay", 3),
        "auto_handoff_keywords": inst.get("auto_handoff_keywords", []),
        "model":                 inst.get("model", inst.get("llm_model", "openai/gpt-oss-120b")),
        "llm_model":             inst.get("llm_model", inst.get("model", "openai/gpt-oss-120b")),
        "llm_provider":          inst.get("llm_provider", "groq"),
        "followup_enabled":      inst.get("followup_enabled", True),
        "followup_delay_hours":  inst.get("followup_delay_hours", 24),
        "followup_max":          inst.get("followup_max", 3),
        "scoring_criteria":      inst.get("scoring_criteria", ""),
        "evo_instance_name":     inst.get("evo_instance_name", ""),
        "auto_release_minutes":  inst.get("auto_release_minutes", 3),
        "tts_enabled":           inst.get("tts_enabled", False),
        "auto_suggest_enabled":  inst.get("auto_suggest_enabled", True),
        "phone_number_id":       inst.get("phone_number_id", ""),
        "template_name":         inst.get("template_name", ""),
        "can_manage_credentials": can_manage_credentials,
        "campaign_context":      inst.get("campaign_context", ""),
        "campaign_timing":       inst.get("campaign_timing", {}),
    }


# -- Persistência de conversas --------------------------------------------------

def _save_conversations():
    """Persiste o snapshot COMPLETO de conversas no Postgres (delete + regrava).
    Fora do caminho quente: usado como reparo de deriva periódico. Por evento,
    use _save_conversation(instance_id, phone) — incremental."""
    with conversations_lock:
        snapshot = copy.deepcopy(conversations)
    if not conversation_pg_service.pg_configured():
        logger.warning("[Conversations] Postgres não configurado — snapshot NÃO persistido (modo dev/teste).")
        return
    ok, detail = conversation_pg_service.sync_snapshot(snapshot)
    if not ok:
        logger.warning(f"[Conversations] AVISO: sync Postgres falhou — {detail}.")


# Conversas cujo upsert incremental falhou — o auto-save loop tenta de novo a cada 60s.
_dirty_convs: set[tuple[str, str]] = set()
_dirty_convs_lock = threading.Lock()
# Falhas consecutivas por conversa — depois de N tentativas (~N minutos) sem
# sucesso, dispara um alerta visível em vez de retentar pra sempre em silêncio.
# Foi assim que o lead Sergio sumiu: upsert falhava (lead_campanha estourava
# varchar(255)) e só existia um log de warning, nunca um alerta.
_dirty_convs_fail_count: dict[tuple[str, str], int] = {}
_DIRTY_CONV_ALERT_THRESHOLD = 5


def _save_conversation(instance_id: str, phone: str):
    """
    Persiste UMA conversa no Postgres (upsert incremental) — caminho quente.
    Substitui o sync_snapshot completo que reescrevia todas as conversas de
    todas as instâncias a cada mensagem. Em falha, agenda retry via _dirty_convs.
    """
    if not conversation_pg_service.pg_configured():
        logger.warning("[Conversations] Postgres não configurado — conversa NÃO persistida (modo dev/teste).")
        return
    with conversations_lock:
        conv_key = _resolve_conversation_key(instance_id, phone)
        conv = conversations.get(instance_id, {}).get(conv_key)
        if conv is None:
            return  # deletada em paralelo — a deleção explícita cuida do Postgres
        snapshot = copy.deepcopy(conv)
    ok, detail = conversation_pg_service.upsert_conversation(instance_id, conv_key, snapshot)
    key = (instance_id, conv_key)
    if not ok:
        with _dirty_convs_lock:
            _dirty_convs.add(key)
            fail_count = _dirty_convs_fail_count.get(key, 0) + 1
            _dirty_convs_fail_count[key] = fail_count
        logger.warning(f"[Conversations] Upsert falhou para {instance_id}/{conv_key} (tentativa {fail_count}, retry agendado): {detail}")
        if fail_count == _DIRTY_CONV_ALERT_THRESHOLD:
            _append_alert(
                "conversation_persist_failure",
                f"Conversa {instance_id}/{conv_key} falhando ao salvar no Postgres há {fail_count} tentativas — "
                "pode estar existindo só na memória e ser perdida se a conversa for apagada ou o backend reiniciar.",
                instance_id=instance_id,
                detail=detail[:300],
            )
    else:
        with _dirty_convs_lock:
            _dirty_convs_fail_count.pop(key, None)


def _flush_dirty_conversations():
    """Retenta upserts de conversa que falharam (chamado pelo auto-save loop)."""
    with _dirty_convs_lock:
        pending = list(_dirty_convs)
        _dirty_convs.clear()
    for iid, key in pending:
        _save_conversation(iid, key)


def _delete_conversation_from_pg(instance_id: str, conv_key: str):
    """Remove uma conversa do Postgres após removê-la da memória."""
    if not conversation_pg_service.pg_configured():
        return
    ok, detail = conversation_pg_service.delete_conversation(instance_id, conv_key)
    if not ok:
        logger.warning(f"[Conversations] Falha ao deletar {instance_id}/{conv_key} no Postgres: {detail}")


def _delete_instance_conversations_from_pg(instance_id: str):
    """Remove todas as conversas de uma instância do Postgres."""
    if not conversation_pg_service.pg_configured():
        return
    ok, detail = conversation_pg_service.delete_instance_conversations(instance_id)
    if not ok:
        logger.warning(f"[Conversations] Falha ao deletar conversas de {instance_id} no Postgres: {detail}")


def _restore_conversation_from_pg(instance_id: str, conv_key: str) -> tuple[bool, str]:
    """
    Reverte o soft delete no Postgres e reinsere a conversa na memória do
    processo — some da lixeira e volta a aparecer no InboxSDR sem precisar
    reiniciar o backend.
    """
    if not conversation_pg_service.pg_configured():
        return False, "postgres_not_configured"
    ok, result = conversation_pg_service.restore_conversation(instance_id, conv_key)
    if not ok:
        return False, str(result)
    with conversations_lock:
        conversations.setdefault(instance_id, {})[conv_key] = result
    return True, ""


def _list_deleted_conversations_pg(instance_id: str = "") -> list[dict]:
    if not conversation_pg_service.pg_configured():
        return []
    return conversation_pg_service.list_deleted_conversations(instance_id)


_CONV_PURGE_RETENTION_DAYS = int(os.getenv("CONV_TRASH_RETENTION_DAYS", "30"))


def _purge_deleted_conversations_tick():
    """Remove definitivamente conversas apagadas há mais de N dias — chamado 1x/dia pelo autosave loop."""
    if not conversation_pg_service.pg_configured():
        return
    ok, purged, detail = conversation_pg_service.purge_deleted_conversations(_CONV_PURGE_RETENTION_DAYS)
    if not ok:
        logger.warning(f"[Conversations] Falha ao purgar lixeira: {detail}")
    elif purged:
        logger.info(f"[Conversations] Lixeira: {purged} conversa(s) removida(s) definitivamente (>{_CONV_PURGE_RETENTION_DAYS}d).")


# -- Renovação proativa do token OAuth da Meta ----------------------------------

_META_OAUTH_APP_ID = os.getenv("META_OAUTH_APP_ID", "")
_META_OAUTH_APP_SECRET = os.getenv("META_OAUTH_APP_SECRET", "")
_META_TOKEN_REFRESH_WINDOW_DAYS = int(os.getenv("META_TOKEN_REFRESH_WINDOW_DAYS", "7"))


def _refresh_meta_oauth_tokens_tick():
    """
    Verifica tokens vinculados na cadência do full-sync. A troca aceita pela
    Meta não garante extensão da validade: só confirma renovação se o prazo
    aumentar. Tokens vencidos exigem reconexão, sem novas tentativas de troca.
    """
    if not (_META_OAUTH_APP_ID and _META_OAUTH_APP_SECRET):
        return
    try:
        expiring = meta_oauth_pg_service.list_connections_expiring_soon(_META_TOKEN_REFRESH_WINDOW_DAYS)
    except Exception as exc:
        logger.warning(f"[META-OAUTH] Falha ao listar conexões perto de expirar: {exc}")
        return
    for conn in expiring:
        email = conn["user_email"]
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(conn["token_expires_at"]) if conn.get("token_expires_at") else None
        if expires_at and expires_at <= now:
            _append_alert(
                "meta_oauth_token_expired",
                f"Token Meta de {email} já expirou — reconecte a integração Meta.",
                detail=f"Validade: {expires_at.isoformat()}",
            )
            continue
        current_token = _decrypt_field_for_user(email, conn["access_token_encrypted"])
        # expires_in é relativo à chamada, não ao término da requisição.
        exchange_started_at = datetime.now(timezone.utc)
        result = meta_oauth_service.exchange_for_long_lived_token(
            _META_OAUTH_APP_ID, _META_OAUTH_APP_SECRET, current_token
        )
        if not result.get("ok"):
            logger.warning(f"[META-OAUTH] Falha ao renovar token de {email}: {result.get('detail')}")
            _append_alert(
                "meta_oauth_token_expiring",
                f"Token Meta de {email} expira em breve e a renovação automática falhou — "
                "reconecte a integração Meta antes que pare de funcionar.",
                detail=result.get("detail", ""),
            )
            continue
        expires_in = result.get("expires_in", 0)
        new_expires_at = (exchange_started_at + timedelta(seconds=expires_in)) if expires_in and expires_in > 0 else None
        # Segundos de diferença por arredondamento/latência não comprovam
        # que o token recebeu um novo período de validade.
        if new_expires_at is None or (expires_at and new_expires_at <= expires_at + timedelta(seconds=60)):
            _append_alert(
                "meta_oauth_token_expiring",
                f"Token Meta de {email} expira em breve — reconecte a integração Meta; "
                "a troca automática não confirmou extensão da validade.",
                detail="A Meta não informou uma validade maior que a atual.",
            )
            continue
        fernet = _make_fernet_for_user(email)
        encrypted = _encrypt_field(fernet, result["access_token"])
        ok, detail = meta_oauth_pg_service.update_connection_token(conn["id"], encrypted, new_expires_at)
        if ok:
            logger.info(f"[META-OAUTH] Token renovado automaticamente para {email}.")
        else:
            logger.warning(f"[META-OAUTH] Token renovado na Meta mas falhou ao salvar para {email}: {detail}")


def _parse_whatsapp_jid(raw_value: str) -> dict[str, object]:
    """Classifica um endereço WhatsApp sem confundir LID com telefone.

    O identificador antes de ``@lid`` também é numérico, mas não é um número
    telefônico. Por isso o tipo do servidor precisa ser preservado antes de
    qualquer chamada a ``normalize_phone``.
    """
    raw = str(raw_value or "").strip()
    result: dict[str, object] = {
        "raw": raw,
        "user": raw,
        "server": "",
        "type": "plain",
        "device": "",
        "is_phone": False,
    }
    if not raw or "@" not in raw:
        return result

    user_part, server = raw.rsplit("@", 1)
    server = server.strip().lower()
    user_part = user_part.strip()
    user = user_part
    device = ""
    if ":" in user_part:
        base, possible_device = user_part.rsplit(":", 1)
        if possible_device.isdigit():
            user, device = base, possible_device

    jid_type = {
        "s.whatsapp.net": "pn",
        "lid": "lid",
        "g.us": "group",
    }.get(server, "unknown")
    result.update({
        "user": user,
        "server": server,
        "type": jid_type,
        "device": device,
        "is_phone": jid_type == "pn",
    })
    return result


def _conversation_phone_key(raw_phone: str) -> str:
    """
    Chave canônica para conversas.
    Tenta usar a normalização de contatos do KLEAD e cai para dígitos puros
    quando o número não segue o padrão brasileiro esperado.
    """
    raw = str(raw_phone or "").strip()
    if not raw:
        return ""
    jid = _parse_whatsapp_jid(raw)
    jid_type = str(jid["type"])
    if jid_type == "pn":
        # Mantém a chave telefônica legada para PN JID.
        raw = str(jid["user"])
    elif jid_type in ("lid", "group", "unknown"):
        # LID numérico não é telefone. Preserva um identificador tipado e
        # independente de device para não colidir com um número real.
        user = str(jid["user"])
        server = str(jid["server"])
        return f"{user}@{server}" if user and server else str(jid["raw"])
    # Se ainda contém letras após extração do JID, retornar como-é (identificador não-telefônico)
    if re.search(r"[A-Za-z]", raw):
        return raw
    normalized = normalize_phone(raw)
    if normalized:
        return normalized
    digits = re.sub(r"\D", "", raw)
    return digits or raw


def _resolve_conversation_key(instance_id: str, phone: str) -> str:
    """
    Localiza a chave real da conversa usando o telefone canônico.
    Isso evita separar o mesmo lead em conversas diferentes por variação de formato.
    """
    target = _conversation_phone_key(phone)
    inst_convs = conversations.get(instance_id, {})
    if target in inst_convs:
        return target
    for key in inst_convs.keys():
        if _conversation_phone_key(key) == target:
            return key
    return target


def _merge_conversation_records(base: dict, extra: dict) -> dict:
    """Mescla dois registros de conversa preservando histórico e o estado mais recente."""
    merged = copy.deepcopy(base)
    base_msgs = list(merged.get("messages", []))
    seen = {
        (m.get("timestamp", ""), m.get("role", ""), m.get("content", ""))
        for m in base_msgs
    }
    for msg in extra.get("messages", []):
        sig = (msg.get("timestamp", ""), msg.get("role", ""), msg.get("content", ""))
        if sig not in seen:
            base_msgs.append(msg)
            seen.add(sig)
    base_msgs.sort(key=lambda m: m.get("timestamp", ""))
    merged["messages"] = base_msgs

    # Atualiza o estado com a conversa mais recente.
    try:
        if extra.get("last_activity", "") > merged.get("last_activity", ""):
            for field in (
                "name", "stage", "ai_paused", "unread", "score", "score_reason",
                "turn_count", "followup_count", "last_followup_at", "needs_human",
                "handoff_reason", "handoff_at", "handoff_source", "handoff_handled_at",
                "created_at", "last_activity",
            ):
                if field in extra:
                    merged[field] = extra.get(field)
    except Exception:
        logger.debug("Exceção ignorada em rotina auxiliar", exc_info=True)

    merged["last_activity"] = max(merged.get("last_activity", ""), extra.get("last_activity", ""))
    return merged


def _normalize_conversations_store():
    """Agrupa conversas duplicadas por telefone canônico e mantém o histórico completo."""
    with conversations_lock:
        for instance_id, conv_map in list(conversations.items()):
            normalized_map: dict = {}
            for raw_phone, conv in conv_map.items():
                key = _conversation_phone_key(raw_phone)
                if key in normalized_map:
                    normalized_map[key] = _merge_conversation_records(normalized_map[key], conv)
                else:
                    normalized_map[key] = copy.deepcopy(conv)
            conversations[instance_id] = normalized_map


def init_conversations_storage():
    """
    Carrega conversas do Postgres — única fonte de verdade.
    Fail-fast: com PG configurado mas indisponível, aborta o startup. Subir com
    estado vazio seria pior — o próximo _save_conversations faria sync_snapshot
    (delete + regrava) e apagaria as conversas reais do banco.
    Sem PG configurado (dev/teste), inicia vazio com aviso.
    """
    if not conversation_pg_service.pg_configured():
        logger.warning("[Startup] Postgres não configurado — iniciando sem conversas (modo dev/teste).")
        _normalize_conversations_store()
        return
    try:
        ok, snapshot = conversation_pg_service.load_snapshot()
    except Exception as exc:
        raise RuntimeError(f"[Startup] Postgres indisponível ao carregar conversas — abortando: {exc}") from exc
    if not ok or not isinstance(snapshot, dict):
        raise RuntimeError(f"[Startup] Falha ao carregar conversas do Postgres — abortando: {snapshot}")
    conversations.clear()
    conversations.update(snapshot)
    _normalize_conversations_store()


def _delete_instance_local_data(instance_id: str):
    """Remove todo o estado local persistido ligado a uma instancia."""
    # Conversas em memória + Postgres
    with conversations_lock:
        conversations.pop(instance_id, None)
    _delete_instance_conversations_from_pg(instance_id)

    # Timers de debounce pendentes desta instância
    with _pending_timers_lock:
        to_cancel = [k for k in list(_pending_timers) if k.startswith(f"{instance_id}:")]
        for k in to_cancel:
            _pending_timers.pop(k).cancel()

    # Arquivos de progress (sent.json, queue.json)
    progress_dir = DATA_DIR / "progress"
    safe_id = _safe_instance_path_part(instance_id)
    for suffix in ("_sent.json", "_queue.json"):
        p = progress_dir / f"{safe_id}{suffix}"
        p.unlink(missing_ok=True)

    # Base de conhecimento (rmtree cobre subdiretórios; ignore_errors evita
    # que um lock de arquivo aborte a exclusão da instância inteira)
    target = KNOWLEDGE_DIR / safe_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    # Alertas ligados à instância
    with _alerts_lock:
        try:
            if ALERTS_FILE.exists():
                alerts = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
                remaining = [a for a in alerts if a.get("instance_id") != instance_id]
                if len(remaining) != len(alerts):
                    _atomic_write(ALERTS_FILE, json.dumps(remaining, ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.warning(f"[Instances] Falha ao purgar alertas de {instance_id}: {exc}")

    # Purges auxiliares — falha em um não pode impedir os demais
    for purge_fn in (
        purge_instance_data,
        patterns_svc.purge_patterns,
        crm_svc.purge_instance_leads,
        assignment_svc.purge_instance,
        identity_alias_pg_service.purge_instance,
    ):
        try:
            purge_fn(instance_id)
        except Exception as exc:
            logger.warning(f"[Instances] Purge {purge_fn.__name__} falhou para {instance_id}: {exc}")


# -- Store de instâncias (em memória) ------------------------------------------

def _make_instance(
    api_url: str, api_key: str, openai_key: str, prompt: str,
    provider: str = "evolution",
    meta_token: str = "", meta_app_secret: str = "", phone_number_id: str = "", template_name: str = "",
    meta_page_id: str = "",
    meta_page_access_token: str = "",
    meta_capi_enabled: bool = False,
    meta_capi_access_token: str = "",
    meta_capi_event_source_id: str = "",
    meta_capi_test_event_code: str = "",
    meta_ad_account_id: str = "",
    inbound_ai_enabled: bool = True,
    qualification_prompt: str = "",
    max_ai_turns: int = 8,
    ai_response_delay: int = 3,
    auto_handoff_keywords: list = None,
    model: str = "openai/gpt-oss-120b",
    llm_provider: str = "groq",
    llm_api_key: str = "",
    key_openai: str = "",
    key_anthropic: str = "",
    key_groq: str = "",
    followup_enabled: bool = True,
    followup_delay_hours: int = 24,
    followup_max: int = 3,
    scoring_criteria: str = "",
    evo_instance_name: str = "",
    auto_release_minutes: int = 5,
    tts_enabled: bool = False,
    webhook_secret: str = "",
    auto_suggest_enabled: bool = True,
) -> dict:
    return {
        "api_url":               api_url,
        "api_key":               api_key,
        "openai_key":            openai_key,
        "prompt":                prompt,
        "provider":              provider,
        "meta_token":            meta_token,
        "meta_app_secret":       meta_app_secret,
        "phone_number_id":       phone_number_id,
        "template_name":         template_name,
        "meta_page_id":          meta_page_id,
        "meta_page_access_token": meta_page_access_token,
        "meta_capi_enabled":     meta_capi_enabled,
        "meta_capi_access_token": meta_capi_access_token,
        "meta_capi_event_source_id": meta_capi_event_source_id,
        "meta_capi_test_event_code": meta_capi_test_event_code,
        "meta_ad_account_id":    meta_ad_account_id,
        "inbound_ai_enabled":    inbound_ai_enabled,
        "qualification_prompt":  qualification_prompt,
        "max_ai_turns":          max_ai_turns,
        "ai_response_delay":     ai_response_delay,
        "auto_handoff_keywords": auto_handoff_keywords or [],
        "model":                 model,
        "llm_provider":          llm_provider,
        "llm_api_key":           llm_api_key,
        "key_openai":            key_openai,
        "key_anthropic":         key_anthropic,
        "key_groq":              key_groq,
        "followup_enabled":      followup_enabled,
        "followup_delay_hours":  followup_delay_hours,
        "followup_max":          followup_max,
        "scoring_criteria":      scoring_criteria,
        "evo_instance_name":     evo_instance_name,
        "auto_release_minutes":  auto_release_minutes,
        "tts_enabled":           tts_enabled,
        "auto_suggest_enabled":  auto_suggest_enabled,
        # Backfill lazy: instâncias antigas (sem secret persistido) ganham um
        # secret novo aqui mesmo, no boot ou na criação — nunca fica vazio.
        "webhook_secret":        webhook_secret or secrets.token_urlsafe(32),
        "status":                "idle",
        "logs":                  [],
        "contacts":              [],
        "envios_hoje":           0,
        "envios_hora":           [],
        "daily_limit":           0,
        "thread":                None,
        "stop_flag":             False,
        "queue":                 [],
        "queue_status":          "idle",
        "queue_progress":        0,
        "queue_stop_flag":       False,
    }


# Carrega configuracao global de IA no boot
ai_config: dict = _load_ai_config()
_save_ai_config()


def _load_instance_from_cfg(iid: str, cfg: dict):
    instances[iid] = _make_instance(
        cfg["api_url"], cfg["api_key"], cfg["openai_key"], cfg["prompt"],
        provider=cfg.get("provider", "evolution"),
        meta_token=cfg.get("meta_token", ""),
        meta_app_secret=cfg.get("meta_app_secret", ""),
        meta_page_id=cfg.get("meta_page_id", ""),
        meta_page_access_token=cfg.get("meta_page_access_token", ""),
        meta_capi_enabled=cfg.get("meta_capi_enabled", False),
        meta_capi_access_token=cfg.get("meta_capi_access_token", ""),
        meta_capi_event_source_id=cfg.get("meta_capi_event_source_id", ""),
        meta_capi_test_event_code=cfg.get("meta_capi_test_event_code", ""),
        meta_ad_account_id=cfg.get("meta_ad_account_id", ""),
        phone_number_id=cfg.get("phone_number_id", ""),
        template_name=cfg.get("template_name", ""),
        inbound_ai_enabled=cfg.get("inbound_ai_enabled", True),
        qualification_prompt=cfg.get("qualification_prompt", ""),
        max_ai_turns=cfg.get("max_ai_turns", 8),
        ai_response_delay=cfg.get("ai_response_delay", 3),
        auto_handoff_keywords=cfg.get("auto_handoff_keywords", []),
        model=cfg.get("model", "openai/gpt-oss-120b"),
        llm_provider=cfg.get("llm_provider", "groq"),
        llm_api_key=cfg.get("llm_api_key", ""),
        key_openai=cfg.get("key_openai", ""),
        key_anthropic=cfg.get("key_anthropic", ""),
        key_groq=cfg.get("key_groq", ""),
        followup_enabled=cfg.get("followup_enabled", True),
        followup_delay_hours=cfg.get("followup_delay_hours", 24),
        followup_max=cfg.get("followup_max", 3),
        scoring_criteria=cfg.get("scoring_criteria", ""),
        evo_instance_name=cfg.get("evo_instance_name", ""),
        auto_release_minutes=cfg.get("auto_release_minutes", 3),
        tts_enabled=cfg.get("tts_enabled", False),
        webhook_secret=cfg.get("webhook_secret", ""),
    )
    instances[iid]["contacts"] = cfg.get("contacts", [])
    instances[iid]["meta_page_subscribed"] = cfg.get("meta_page_subscribed", False)
    instances[iid]["meta_waba_subscribed"] = cfg.get("meta_waba_subscribed", False)
    saved_queue = load_queue(iid)
    if saved_queue:
        instances[iid]["queue"]        = saved_queue
        instances[iid]["queue_status"] = "ready"
        instances[iid]["queue_progress"] = len(saved_queue)


def init_instances_storage():
    """
    Carrega instâncias do Postgres — única fonte de verdade.
    Fail-fast: com PG configurado mas indisponível, aborta o startup (subir sem
    instâncias faria _save_configs sobrescrever o banco com config vazia).
    Sem PG configurado (dev/teste), inicia vazio com aviso.
    """
    if not instances_pg_service.pg_configured():
        logger.warning("[Instances] Postgres não configurado — iniciando sem instâncias (modo dev/teste).")
        return
    try:
        ok, snapshot = instances_pg_service.load_all()
    except Exception as exc:
        raise RuntimeError(f"[Instances] Postgres indisponível ao carregar instâncias — abortando: {exc}") from exc
    if not ok or not isinstance(snapshot, dict):
        raise RuntimeError(f"[Instances] Falha ao carregar instâncias do Postgres — abortando: {snapshot}")
    legacy_fernet = _make_fernet()
    for iid, cfg in snapshot.items():
        for field in _SENSITIVE_FIELDS:
            if field in cfg:
                cfg[field] = _decrypt_field_for_instance(iid, cfg[field], legacy_fernet)
        _load_instance_from_cfg(iid, cfg)
    logger.info(f"[Instances] {len(instances)} instâncias carregadas do Postgres")


# Carrega instâncias salvas no boot
init_instances_storage()
# Re-criptografa credenciais em disco a cada boot (migração transparente)
_save_configs()
app.state.instances = instances  # dict populado — agora seguro expor para routers

# Conversas carregadas via core.state (dict compartilhado)
app.state.conversations = conversations


# ------------------------------------------------------------------------------
# Base de Conhecimento
# ------------------------------------------------------------------------------

# Cache da base de conhecimento por instância — a extração de PDF/DOCX rodava
# a CADA resposta da IA. A assinatura (nome, mtime, tamanho) dos arquivos
# invalida o cache automaticamente quando algo muda (upload/remoção).
_knowledge_cache: dict[str, tuple[tuple, str]] = {}
_knowledge_cache_lock = threading.Lock()


def _kb_signature(kb_dir: Path) -> tuple:
    try:
        return tuple(sorted(
            (f.name, f.stat().st_mtime_ns, f.stat().st_size)
            for f in kb_dir.iterdir() if f.is_file()
        ))
    except OSError:
        return ()


def _load_knowledge(instance_id: str) -> str:
    """Extrai e concatena o texto de todos os arquivos da base de conhecimento.
    Cacheado por assinatura de diretório — só re-extrai quando os arquivos mudam."""
    safe_instance_id = _safe_instance_path_part(instance_id)
    kb_dir = KNOWLEDGE_DIR / safe_instance_id
    if not kb_dir.exists():
        with _knowledge_cache_lock:
            _knowledge_cache.pop(instance_id, None)
        return ""

    signature = _kb_signature(kb_dir)
    with _knowledge_cache_lock:
        cached = _knowledge_cache.get(instance_id)
    if cached and cached[0] == signature:
        return cached[1]

    texts = []
    for f in sorted(kb_dir.iterdir()):
        try:
            suffix = f.suffix.lower()
            if suffix == ".pdf":
                import fitz  # PyMuPDF
                doc = fitz.open(str(f))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
            elif suffix in (".txt", ".md"):
                text = f.read_text(encoding="utf-8", errors="ignore")
            elif suffix == ".docx":
                import docx
                d = docx.Document(str(f))
                text = "\n".join(p.text for p in d.paragraphs if p.text.strip())
            else:
                continue
            if text.strip():
                texts.append(f"[{f.name}]\n{text.strip()}")
        except Exception as e:
            logger.warning(f"[Knowledge] Erro ao ler {f.name}: {e}")

    combined = "\n\n---\n\n".join(texts)
    # Limite de 20 000 chars para não exceder o contexto
    if len(combined) > 20000:
        combined = combined[:20000] + "\n...[conteúdo truncado]"
    with _knowledge_cache_lock:
        _knowledge_cache[instance_id] = (signature, combined)
    return combined


def _load_agent_memory(instance_id: str) -> str:
    """Carrega a memória operacional que o próprio agente sintetizou (KB + conversas reais)."""
    try:
        safe_instance_id = _safe_instance_path_part(instance_id)
        mem_file = INBOX_KB_DIR / safe_instance_id / "_agent_memory.txt"
        if not mem_file.exists():
            return ""
        return mem_file.read_text(encoding="utf-8", errors="ignore")[:6000]
    except Exception:
        return ""


# ------------------------------------------------------------------------------
# SDR — Inbound AI: Webhook + Conversas
# ------------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_message_text(data: dict) -> str:
    """
    Extrai o texto de mensagem de payloads da Evolution API (padrão) e Evolution GO.
    Evolution GO usa chave 'Message' (maiúscula); padrão usa 'message' (minúscula).
    """
    msg = data.get("Message") or data.get("message") or {}
    if not msg:
        return ""
    if "conversation" in msg:
        return msg["conversation"]
    ext = msg.get("extendedTextMessage", {})
    if ext.get("text"):
        return ext["text"]
    for key in ("imageMessage", "videoMessage", "documentMessage"):
        if key in msg and msg[key].get("caption"):
            return msg[key]["caption"]
    return ""


def _is_audio_message(data: dict) -> bool:
    """Verifica se o payload contém mensagem de áudio (PTT ou audio)."""
    msg = data.get("Message") or data.get("message") or {}
    if "audioMessage" in msg or "pttMessage" in msg:
        return True

    nested = msg.get("message") if isinstance(msg, dict) else None
    if isinstance(nested, dict) and ("audioMessage" in nested or "pttMessage" in nested):
        return True

    msg_type = (
        data.get("messageType")
        or msg.get("messageType")
        or data.get("type")
        or msg.get("type")
        or ""
    )
    return str(msg_type).lower() in ("audio", "ptt", "audiomessage", "pttmessage")


def _audio_mimetype(data: dict) -> str:
    msg = data.get("Message") or data.get("message") or {}
    if not isinstance(msg, dict):
        return ""
    candidates = [msg]
    nested = msg.get("message")
    if isinstance(nested, dict):
        candidates.append(nested)
    for item in candidates:
        for key in ("audioMessage", "pttMessage"):
            node = item.get(key)
            if isinstance(node, dict):
                return node.get("mimetype", "") or node.get("mimeType", "")
    return ""


def _merge_inbound_source_meta(conv: dict, source_meta: dict | None) -> None:
    """Mescla metadados de transporte sem apagar rastreamento já conhecido."""
    if not source_meta:
        return
    existing = copy.deepcopy(conv.get("source_meta") or {})
    incoming = copy.deepcopy(source_meta)
    evo_existing = existing.get("evolution_identity") or {}
    evo_incoming = incoming.get("evolution_identity") or {}
    if evo_existing or evo_incoming:
        aliases = list(dict.fromkeys(
            list(evo_existing.get("jid_aliases") or [])
            + list(evo_incoming.get("jid_aliases") or [])
        ))
        merged_evo = {**evo_existing, **evo_incoming}
        if aliases:
            merged_evo["jid_aliases"] = aliases
        incoming["evolution_identity"] = merged_evo
    existing.update(incoming)
    conv["source_meta"] = existing


def _identity_shadow_enabled() -> bool:
    return os.getenv("IDENTITY_RESOLVER_SHADOW_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _mask_identity(value: str) -> str:
    raw = str(value or "")
    if len(raw) <= 6:
        return "***"
    return f"{raw[:3]}…{raw[-3:]}"


def _apply_identity_resolution_shadow(instance_id: str, conv_key: str, conv: dict) -> None:
    """Registra diagnóstico PN/LID sem alterar resolução nem chave."""
    if not _identity_shadow_enabled():
        return
    diagnosis = identity_resolver_service.resolve_evolution_shadow(
        current_key=conv_key,
        incoming_source_meta=conv.get("source_meta") or {},
        conversations=conversations.get(instance_id, {}),
        persisted_matches=identity_alias_pg_service.find_matches(
            instance_id, conv_key, conv.get("source_meta") or {},
        ) if identity_alias_pg_service.enabled() else None,
    )
    conv.setdefault("source_meta", {})["identity_resolution_shadow"] = diagnosis
    if diagnosis.get("status") in ("match_suggested", "conflict"):
        logger.info(
            "[IDENTITY-SHADOW] instance=%s current=%s status=%s candidates=%s suggested=%s",
            instance_id,
            _mask_identity(conv_key),
            diagnosis.get("status"),
            diagnosis.get("candidate_count", 0),
            _mask_identity(diagnosis.get("suggested_key", "")),
        )


def _resolve_evolution_conversation_key(
    instance_id: str,
    incoming_key: str,
    source_meta: dict | None,
) -> str:
    """Resolve PN/LID antes de criar conversa; conflitos permanecem separados."""
    canonical_incoming = _conversation_phone_key(incoming_key)
    evolution = (source_meta or {}).get("evolution_identity") or {}
    if not evolution:
        return _resolve_conversation_key(instance_id, canonical_incoming)

    pn_jid = str(evolution.get("pn_jid") or "")
    phone_key = _conversation_phone_key(pn_jid)

    persisted = (
        identity_alias_pg_service.find_matches(instance_id, canonical_incoming, source_meta or {})
        if identity_alias_pg_service.enabled()
        else None
    )
    with conversations_lock:
        # Se o telefone já tem conversa, ele vence inclusive quando também
        # existe uma duplicata histórica em @lid. Isso estabiliza novos eventos
        # sem mesclar automaticamente históricos potencialmente ambíguos.
        if phone_key and phone_key in conversations.get(instance_id, {}):
            return phone_key
        diagnosis = identity_resolver_service.resolve_evolution_shadow(
            current_key=canonical_incoming,
            incoming_source_meta=source_meta or {},
            conversations=conversations.get(instance_id, {}),
            persisted_matches=persisted,
        )
        if diagnosis.get("status") == "match_suggested":
            suggested = str(diagnosis.get("suggested_key") or "")
            if suggested:
                return suggested
        if diagnosis.get("status") == "conflict":
            return canonical_incoming

        # Na primeira mensagem, se o payload já traz o par PN/LID, usa o PN
        # como chave estável. O LID continua preservado na tabela de aliases.
        if phone_key:
            return _resolve_conversation_key(instance_id, phone_key)
        return _resolve_conversation_key(instance_id, canonical_incoming)


def _persist_identity_aliases(
    instance_id: str,
    conv_key: str,
    source_meta: dict | None,
) -> None:
    """Faz dual-write aditivo fora do lock de conversas; nunca altera chaves."""
    if not identity_alias_pg_service.enabled() or not source_meta:
        return
    ok, detail = identity_alias_pg_service.record_evolution_observation(
        instance_id, conv_key, source_meta,
    )
    if not ok:
        logger.warning(
            "[IDENTITY-ALIASES] Falha ao persistir instance=%s conversation=%s: %s",
            instance_id,
            _mask_identity(conv_key),
            detail,
        )


def _persist_meta_identity_aliases(
    instance_id: str,
    conv_key: str,
    source_meta: dict | None,
    confidence: int = 90,
) -> None:
    """Persiste aliases Meta sem trocar a chave nem fundir conversas."""
    if not identity_alias_pg_service.enabled() or not source_meta:
        return
    ok, detail = identity_alias_pg_service.record_meta_observation(
        instance_id, conv_key, source_meta, confidence=confidence,
    )
    if not ok:
        logger.warning(
            "[META-IDENTITY] Falha ao persistir instance=%s conversation=%s: %s",
            instance_id,
            _mask_identity(conv_key),
            detail,
        )


def _handle_media_inbound(
    instance_id: str,
    phone: str,
    name: str,
    data: dict,
    media_info: dict,
    source_meta: dict | None = None,
):
    """
    Baixa imagem/vídeo/documento recebido do Evolution GO e registra no Inbox
    com o arquivo de verdade (não só a legenda). Roda em thread separada.
    Antes desta função, apenas a legenda (se houvesse) chegava ao Inbox — mídia
    sem legenda era descartada silenciosamente.
    """
    with instances_lock:
        inst = dict(instances.get(instance_id, {}))
    if not inst:
        return

    media_type = media_info.get("type", "arquivo")
    logger.info(f"[SDR][Media] Baixando {media_type} de {phone} ({instance_id})")
    result = media_svc.download_inbound_media(inst.get("api_url", ""), inst.get("api_key", ""), data, media_info)

    caption = media_info.get("caption", "").strip()
    conv_key = _resolve_conversation_key(instance_id, phone)

    if not result.get("ok"):
        logger.warning(f"[SDR][Media] Falha ao baixar {media_type} de {phone}: {result.get('detail')}")
        placeholder = f"[{media_type} recebido — falha ao baixar]" + (f" {caption}" if caption else "")
        _register_inbound_placeholder(instance_id, phone, name, placeholder, source_meta=source_meta)
        return

    _persist_identity_aliases(instance_id, conv_key, source_meta)
    with conversations_lock:
        conv = _get_or_create_conversation(instance_id, phone, name)
        _merge_inbound_source_meta(conv, source_meta)
        _apply_identity_resolution_shadow(instance_id, conv_key, conv)
        is_new_conversation = len(conv["messages"]) == 0
        conv["messages"].append({
            "role":       "user",
            "content":    caption or f"[{media_type}]",
            "timestamp":  _now_iso(),
            "is_media":   True,
            "media_type": media_type,
            "media_url":  media_svc.signed_media_path(result['filename']),
            "filename":   media_info.get("filename", ""),
        })
        conv["last_activity"] = _now_iso()
        conv["unread"] = True
        ai_paused = conv["ai_paused"]

    if is_new_conversation:
        _auto_assign_to_instance(instance_id, phone, name)
    _sse_emit(instance_id, "new_message", {"phone": conv_key, "role": "user", "content": caption or f"[{media_type}]", "is_media": True, "media_type": media_type})

    # Sem legenda, só registra (mesma escolha já feita pra áudio sem transcrição
    # — não deixa a IA reagir a um "[imagem]" vazio de contexto).
    if ai_paused or not caption or not inst.get("inbound_ai_enabled", True):
        _save_conversation(instance_id, conv_key)
        return
    _save_conversation(instance_id, conv_key)
    _schedule_ai_response(instance_id, phone, inst)


def _register_inbound_placeholder(
    instance_id: str,
    phone: str,
    name: str,
    text: str,
    extra: dict | None = None,
    source_meta: dict | None = None,
):
    """Garante que mensagens sem transcricao tambem aparecam no inbox."""
    conv_key = _resolve_conversation_key(instance_id, phone)
    _persist_identity_aliases(instance_id, conv_key, source_meta)
    with conversations_lock:
        conv = _get_or_create_conversation(instance_id, phone, name)
        _merge_inbound_source_meta(conv, source_meta)
        _apply_identity_resolution_shadow(instance_id, conv_key, conv)
        message = {"role": "user", "content": text, "timestamp": _now_iso()}
        if extra:
            message.update(extra)
        conv["messages"].append(message)
        conv["last_activity"] = _now_iso()
        conv["unread"] = True
    event = {"phone": conv_key, "role": "user", "content": text}
    if extra:
        event.update(extra)
    _sse_emit(instance_id, "new_message", event)
    _save_conversation(instance_id, conv_key)


def _handle_audio_inbound(
    instance_id: str,
    phone: str,
    name: str,
    data: dict,
    source_meta: dict | None = None,
):
    """
    Baixa audio do Evolution GO, transcreve com Whisper e processa como mensagem normal.
    Roda em thread separada.
    """
    with instances_lock:
        inst = dict(instances.get(instance_id, {}))
    if not inst:
        return

    logger.info(f"[SDR][Audio] Baixando audio de {phone} ({instance_id})")
    audio_bytes = download_media_bytes(
        inst.get("api_url", ""),
        inst.get("api_key", ""),
        data,
    )
    if not audio_bytes:
        logger.warning(f"[SDR][Audio] Falha ao baixar audio de {phone}")
        _register_inbound_placeholder(
            instance_id, phone, name,
            "[audio recebido - falha ao baixar para transcricao]",
            source_meta=source_meta,
        )
        return

    mimetype = _audio_mimetype(data) or "audio/ogg"
    audio_ext = media_svc.ext_from_mimetype(mimetype, "audio")
    audio_filename = media_svc.save_temp_file(audio_bytes, audio_ext, delete_after=24 * 3600.0)
    audio_meta = {
        "is_audio": True,
        "is_media": True,
        "media_type": "audio",
        "media_url": media_svc.signed_media_path(audio_filename),
        "filename": f"audio.{audio_ext}",
    }

    ai = _resolve_ai_settings(inst)
    # Transcrição sempre tenta Groq primeiro (Whisper gratuito lá) mesmo que o
    # provider principal da instância seja outro (ex: OpenAI pras conversas) —
    # são configurações independentes por escolha do usuário.
    groq_key = inst.get("key_groq") or ai_config.get("key_groq") or ""
    transcribed = transcribe_audio(
        audio_bytes=audio_bytes,
        filename=f"audio.{audio_ext}",
        llm_provider="groq" if groq_key else ai["provider"],
        llm_api_key=groq_key or ai["api_key"],
        openai_key=inst.get("openai_key", "") or (ai["api_key"] if ai["provider"] == "openai" else ""),
    )
    if not transcribed:
        logger.info(f"[SDR][Audio] Transcricao vazia para {phone}")
        _register_inbound_placeholder(
            instance_id, phone, name, "[audio recebido]", audio_meta,
            source_meta=source_meta,
        )
        return

    logger.info(f"[SDR][Audio] Transcrito: {transcribed[:100]}")
    _register_inbound_placeholder(
        instance_id, phone, name, f"🎤 {transcribed}", audio_meta,
        source_meta=source_meta,
    )
    conv_key = _resolve_conversation_key(instance_id, phone)
    with conversations_lock:
        conv = conversations.get(instance_id, {}).get(conv_key)
        ai_paused = bool(conv and conv.get("ai_paused"))
    if not ai_paused and inst.get("inbound_ai_enabled", True):
        _schedule_ai_response(instance_id, phone, inst, audio_reply=True)


def _get_or_create_conversation(instance_id: str, phone: str, name: str) -> dict:
    """Retorna a conversa existente ou cria uma nova."""
    with conversations_lock:
        if instance_id not in conversations:
            conversations[instance_id] = {}
        key = _resolve_conversation_key(instance_id, phone)
        if key not in conversations[instance_id]:
            conversations[instance_id][key] = {
                "name":               name or phone,
                "stage":              "qualifying",
                "messages":           [],
                "ai_paused":          False,
                "unread":             False,
                "score":              None,
                "score_reason":       "",
                "turn_count":         0,
                "followup_count":     0,
                "last_followup_at":   None,
                "needs_human":        False,
                "handoff_reason":     "",
                "handoff_at":         None,
                "handoff_source":     "",
                "handoff_handled_at": None,
                "created_at":         _now_iso(),
                "last_activity":      _now_iso(),
            }
        return conversations[instance_id][key]


def _mark_needs_human(conv: dict, reason: str, source: str):
    conv["needs_human"] = True
    conv["handoff_reason"] = reason or "IA solicitou atendimento humano"
    conv["handoff_at"] = conv.get("handoff_at") or _now_iso()
    conv["handoff_source"] = source
    conv["handoff_handled_at"] = None


def _clear_needs_human(conv: dict):
    if conv.get("needs_human"):
        conv["handoff_handled_at"] = _now_iso()
    conv["needs_human"] = False


def _generate_tts_audio(text: str, openai_key: str) -> bytes | None:
    """Converte texto em áudio OGG usando OpenAI TTS. Retorna bytes ou None."""
    if not openai_key or not text.strip():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        response = client.audio.speech.create(
            model="tts-1",
            voice="nova",
            input=text,
            response_format="opus",
        )
        audio_bytes = response.read()
        logger.info(f"[TTS] Gerado {len(audio_bytes)} bytes para resposta")
        return audio_bytes
    except Exception as e:
        logger.warning(f"[TTS] Falha ao gerar áudio: {e}")
        return None


def _ai_respond_to_conv(instance_id: str, phone: str, inst: dict, conv: dict, last_text: str, audio_reply: bool = False):
    """
    Chama a IA e envia a resposta para o lead.
    Assume que a mensagem do lead já está registrada em conv["messages"].
    """
    if not inst.get("inbound_ai_enabled", True):
        return
    # SDR autônomo é feature bloqueada no plano Lite. Ponto único de estrangulamento:
    # todos os gatilhos (texto, áudio, mídia) passam por aqui, então basta gatar
    # aqui para o lite nunca responder sozinho — as sugestões (co-piloto) seguem
    # livres pois usam outro endpoint (/suggest).
    if not auth_svc.instance_plan_allows(instance_id, "sdr_autonomo"):
        return

    conv_key = _resolve_conversation_key(instance_id, phone)
    with conversations_lock:
        current_conv = conversations.get(instance_id, {}).get(conv_key, conv)
        conv_live = current_conv
        conv_snapshot = copy.deepcopy(current_conv)

    # Verifica keywords de handoff — só muda o label, IA continua respondendo
    keywords = [k.lower().strip() for k in inst.get("auto_handoff_keywords", []) if k.strip()]
    keyword_handoff = bool(keywords and any(kw in last_text.lower() for kw in keywords))
    if keyword_handoff:
        conv_live["stage"] = "handoff"
        _mark_needs_human(conv_live, "Palavra-chave de handoff detectada", "keyword")

    history = [{"role": m["role"], "content": m["content"]} for m in conv_snapshot["messages"] if m["role"] in ("user", "assistant")]

    # Carrega base de conhecimento estática + padrões aprendidos dinamicamente + memória sintetizada pelo agente
    static_knowledge = _load_knowledge(instance_id)
    learned_patterns = build_knowledge_from_patterns(instance_id)
    agent_memory     = _load_agent_memory(instance_id)
    knowledge = "\n\n".join(filter(None, [static_knowledge, learned_patterns, agent_memory]))
    ai = _resolve_ai_settings(inst)

    result = qualify_lead(
        openai_key=ai["api_key"],
        qualification_prompt=inst.get("qualification_prompt", ""),
        history=history,
        turn_count=conv_snapshot["turn_count"],
        max_ai_turns=inst.get("max_ai_turns", 8),
        model=ai["model"],
        llm_provider=ai["provider"],
        llm_api_key=ai["api_key"],
        knowledge=knowledge,
        scoring_criteria=inst.get("scoring_criteria", ""),
        lead_name=conv_snapshot.get("name", ""),
    )

    reply   = result["message"]
    action  = result["action"]
    score   = result["score"]
    reason  = result["score_reason"]

    # Guardrail: o lead tentou manipular o agente (prompt injection). Marca a
    # conversa e alerta — o score já veio limitado (Camada 4 do responder), então
    # não sobe pro CAPI como lead quente. Quem decide sobre um lead assim é humano.
    if result.get("injection_flagged"):
        _append_alert(
            "prompt_injection",
            f"Tentativa de manipulação do agente detectada na conversa {conv_key}",
            instance_id=instance_id,
            detail="Score limitado e conversa marcada para revisão humana.",
        )

    with conversations_lock:
        # Re-busca o dict live: pode ter sido modificado durante a chamada à IA
        conv_live = conversations.get(instance_id, {}).get(conv_key, conv_live)
        conv_live["turn_count"]   += 1
        conv_live["score"]         = score
        conv_live["score_reason"]  = reason
        if result.get("injection_flagged"):
            conv_live["injection_flagged"] = True
        conv_live["messages"].append({"role": "assistant", "content": reply, "timestamp": _now_iso()})
        conv_live["last_activity"] = _now_iso()
        _sse_emit(instance_id, "ai_reply", {"phone": conv_key, "role": "assistant", "content": reply, "score": score, "stage": conv_live.get("stage", "qualifying")})

        if action == "handoff":
            conv_live["stage"] = "handoff"
            _mark_needs_human(conv_live, reason or "IA solicitou atendimento humano", "ai")
            _sse_emit(instance_id, "handoff", {"phone": conv_key, "name": conv_live.get("name", conv_key), "score": score, "reason": reason})
        elif action == "disqualify":
            conv_live["stage"] = "disqualified"
        elif not keyword_handoff:
            conv_live["stage"] = "qualifying"

    # Registra padrão aprendido (fail-safe — nunca bloqueia o envio)
    if reply and last_text:
        record_pattern(instance_id, last_text, reply, action, score or 0, reason or "")

    outbound_available = (conv_snapshot.get("source_meta") or {}).get("outbound_available", True)
    if reply and outbound_available:
        _send_reply(inst, instance_id, conv_key, reply)

        # Se lead mandou áudio e TTS está habilitado ? responde também em áudio
        if audio_reply and inst.get("tts_enabled", False) and can_send_outbound_audio(inst):
            ai = _resolve_ai_settings(inst)
            tts_key = inst.get("openai_key", "") or (ai["api_key"] if ai["provider"] == "openai" else "")
            audio_bytes = _generate_tts_audio(reply, tts_key)
            if audio_bytes:
                ok, err = media_svc.send_media(
                    api_url=inst.get("api_url", ""),
                    api_key=inst.get("api_key", ""),
                    instance_id=instance_id,
                    phone=conv_key,
                    raw=audio_bytes,
                    mediatype="audio",
                    mimetype="audio/ogg",
                    filename="resposta.ogg",
                )
                if not ok:
                    logger.warning(f"[TTS] Falha ao enviar áudio TTS para {conv_key}: {err}")
    elif reply:
        with conversations_lock:
            conv_live = conversations.get(instance_id, {}).get(conv_key, conv_live)
            if conv_live.get("messages"):
                conv_live["messages"][-1]["delivery_status"] = "blocked_no_supported_destination"
        logger.info("[SDR] Resposta qualificada, mas envio bloqueado para identidade sem telefone.")

    _save_conversation(instance_id, conv_key)


def _schedule_ai_response(instance_id: str, phone: str, inst: dict, audio_reply: bool = False):
    """
    Agenda a resposta da IA com debounce: se uma nova mensagem chegar antes do timer
    disparar, o timer anterior é cancelado e um novo é criado. Assim o SDR responde
    apenas uma vez ao contexto completo do burst de mensagens do lead.
    """
    delay = max(inst.get("ai_response_delay", 3), 2)  # mínimo 2s para o debounce funcionar
    conv_key_full = f"{instance_id}:{_resolve_conversation_key(instance_id, phone)}"

    def _fire():
        with _pending_timers_lock:
            _pending_timers.pop(conv_key_full, None)
        with instances_lock:
            inst_live = dict(instances.get(instance_id, {}))
        if not inst_live:
            return
        with conversations_lock:
            conv_key = _resolve_conversation_key(instance_id, phone)
            conv = conversations.get(instance_id, {}).get(conv_key)
        if not conv:
            return
        last_user = next((m["content"] for m in reversed(conv.get("messages", [])) if m["role"] == "user"), "")
        _ai_respond_to_conv(instance_id, phone, inst_live, conv, last_user, audio_reply=audio_reply)

    with _pending_timers_lock:
        existing = _pending_timers.get(conv_key_full)
        if existing:
            existing.cancel()
            logger.info(f"[SDR] Debounce: timer cancelado para {instance_id}/{phone}, aguardando mais mensagens")
        t = threading.Timer(delay, _fire)
        t.daemon = True
        _pending_timers[conv_key_full] = t
        t.start()


def _auto_assign_to_instance(instance_id: str, phone: str, name: str):
    """Reservado — auto-atribuição desativada: leads só vão ao CRM quando enviados manualmente."""
    pass


def _handle_inbound(
    instance_id: str,
    phone: str,
    name: str,
    text: str,
    audio_reply: bool = False,
    source_meta: dict | None = None,
):
    """
    Processa mensagem recebida de um lead.
    Detecta intenção por palavras-chave e atualiza estágio automaticamente.
    Roda em thread separada para não bloquear o webhook.
    """
    with instances_lock:
        inst = dict(instances.get(instance_id, {}))
    if not inst:
        return

    conv_key = _resolve_evolution_conversation_key(instance_id, phone, source_meta)
    _persist_identity_aliases(instance_id, conv_key, source_meta)
    is_new_conversation = False
    with conversations_lock:
        conv = _get_or_create_conversation(instance_id, conv_key, name)
        _merge_inbound_source_meta(conv, source_meta)
        _apply_identity_resolution_shadow(instance_id, conv_key, conv)
        message_id = str((source_meta or {}).get("message_id") or "")
        if message_id and any(m.get("message_id") == message_id for m in conv.get("messages", [])):
            logger.info("[WEBHOOK] Mensagem duplicada ignorada instance=%s id=%s", instance_id, _mask_identity(message_id))
            return
        is_new_conversation = len(conv["messages"]) == 0
        # Registra mensagem do lead
        message = {"role": "user", "content": text, "timestamp": _now_iso()}
        if message_id:
            message["message_id"] = message_id
        conv["messages"].append(message)
        conv["last_activity"] = _now_iso()
        conv["unread"] = True

        # Extrai campos estruturados da mensagem (formulários Meta CTWA)
        parsed = _extract_meta_message_payload(text)
        lead_fields = parsed.get("lead_fields", {})
        if lead_fields:
            nome_form = lead_fields.get("nome_completo", "")
            email_form = lead_fields.get("email", "")
            # Precisa das parenteses: sem elas, "and" tem precedencia sobre "or" e
            # a condicao vira (nome_form and not conv["name"]) or (nome == phone) —
            # quando o nome atual e so o telefone (placeholder) e nome_form vem
            # vazio do formulario, o "or" ainda era True e sobrescrevia o nome
            # com string vazia.
            if nome_form and (not conv.get("name") or conv.get("name") == phone):
                conv["name"] = nome_form
            if email_form and "@" in email_form:
                conv["lead_email"] = email_form
            form_answers = parsed.get("form_answers", {})
            if form_answers:
                existing = conv.get("form_answers", {})
                existing.update(form_answers)
                conv["form_answers"] = existing

    if is_new_conversation:
        _auto_assign_to_instance(instance_id, conv_key, name)

    _sse_emit(instance_id, "new_message", {"phone": conv_key, "role": "user", "content": text})

    # Confirmações textuais têm precedência sobre a IA somente quando existe
    # exatamente uma pendência e a resposta corresponde integralmente a uma
    # label/sinônimo configurado. A mensagem já foi salva acima em todos os casos.
    confirmation_resolved = None
    try:
        from services import confirmation_pg_service
        confirmation_resolved = confirmation_pg_service.resolve_text_reply(instance_id, conv_key, text)
    except Exception:
        logger.exception("[CONFIRMATIONS] Falha ao resolver resposta textual")
    if confirmation_resolved:
        try:
            crm_svc.add_note(
                str(confirmation_resolved["lead_id"]),
                f"[Confirmação respondida]: {confirmation_resolved['response_option_id']}",
            )
        except Exception:
            logger.exception("[CONFIRMATIONS] Falha ao registrar resposta no CRM")
        _save_conversation(instance_id, conv_key)
        _sse_emit(instance_id, "confirmation_response", {
            "phone": conv_key,
            "confirmation_id": confirmation_resolved["id"],
            "option_id": confirmation_resolved["response_option_id"],
        })
        return

    # Se humano já assumiu ? só salva (mantém unread para sinalizar nova msg)
    if conv["ai_paused"]:
        _save_conversation(instance_id, conv_key)
        return

    # IA decide tudo — reply, handoff ou disqualify
    if inst.get("inbound_ai_enabled", True):
        _save_conversation(instance_id, conv_key)
        _schedule_ai_response(instance_id, conv_key, inst, audio_reply=audio_reply)
        return

    _save_conversation(instance_id, conv_key)


def _send_reply(inst: dict, instance_id: str, phone: str, text: str) -> bool:
    """Envia mensagem de resposta ao lead via Evolution ou Meta. Retorna True se enviou."""
    ok, err, channel = send_outbound_text(inst, instance_id, phone, text)
    if not ok:
        logger.warning(f"[SDR] FALHA ao enviar resposta para {instance_id}/{phone} via {channel}: {err}")
    else:
        logger.info(f"[SDR] Resposta enviada para {instance_id}/{phone} via {channel}")
    return ok


# -- Follow-up automático ------------------------------------------------------

def _send_followup(instance_id: str, phone: str, inst: dict, conv: dict):
    """Gera e envia follow-up para um lead que não respondeu."""
    from services.responder_service import generate_followup
    try:
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in conv.get("messages", [])
            if m["role"] in ("user", "assistant")
        ]
        ai = _resolve_ai_settings(inst)
        msg = generate_followup(
            openai_key=ai["api_key"],
            model=ai["model"],
            llm_provider=ai["provider"],
            llm_api_key=ai["api_key"],
            qualification_prompt=inst.get("qualification_prompt", ""),
            history=history,
        )
        if not msg:
            return
        conv["messages"].append({"role": "assistant", "content": msg, "timestamp": _now_iso()})
        conv["last_activity"]    = _now_iso()
        conv["followup_count"]   = conv.get("followup_count", 0) + 1
        conv["last_followup_at"] = _now_iso()
        _send_reply(inst, instance_id, phone, msg)
        _save_conversation(instance_id, phone)
        logger.info(f"[Followup] {instance_id}/{phone} ? #{conv['followup_count']}")
    except Exception as e:
        logger.warning(f"[Followup] Erro {instance_id}/{phone}: {e}")


def _run_followup(instance_id: str, conv_key: str, inst: dict, conv: dict, fu_key: str):
    """Wrapper de thread do follow-up — garante a saída do set de em-andamento."""
    try:
        _send_followup(instance_id, conv_key, inst, conv)
    finally:
        with _followups_in_flight_lock:
            _followups_in_flight.discard(fu_key)


# Follow-ups em andamento — dedupe barato; substitui a varredura de
# threading.enumerate() por conversa, que era O(threads × conversas).
_followups_in_flight: set[str] = set()
_followups_in_flight_lock = threading.Lock()


def _check_followups():
    """Percorre conversas e agenda follow-ups quando necessário."""
    now = datetime.now(timezone.utc)
    with instances_lock:
        instance_items = [(iid, dict(inst)) for iid, inst in instances.items()]
    for instance_id, inst in instance_items:
        if not inst.get("followup_enabled", True):
            continue
        # Follow-up é IA recontatando o lead sozinha = comportamento autônomo,
        # bloqueado no plano Lite (mesma feature do SDR autônomo).
        if not auth_svc.instance_plan_allows(instance_id, "sdr_autonomo"):
            continue
        delay_secs = inst.get("followup_delay_hours", 24) * 3600
        max_fu     = inst.get("followup_max", 3)

        # Filtra candidatos DENTRO do lock com checagens baratas — copia só quem
        # pode receber follow-up, em vez de copiar todas as conversas da instância.
        with conversations_lock:
            conv_items = []
            for phone, conv in conversations.get(instance_id, {}).items():
                if conv.get("ai_paused") or conv.get("stage") == "disqualified":
                    continue
                if conv.get("followup_count", 0) >= max_fu:
                    continue
                msgs = conv.get("messages", [])
                if not msgs or msgs[-1].get("role") != "assistant":
                    continue
                conv_items.append((phone, dict(conv)))
        for phone, conv in conv_items:
            try:
                msgs = conv.get("messages", [])
                ts_str = msgs[-1].get("timestamp", "") if msgs else ""
                if not ts_str:
                    continue
                last_ts = datetime.fromisoformat(ts_str)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                if (now - last_ts).total_seconds() < delay_secs:
                    continue
                with conversations_lock:
                    conv_key = _resolve_conversation_key(instance_id, phone)
                    live_conv = conversations.get(instance_id, {}).get(conv_key)
                if not live_conv:
                    continue
                fu_key = f"{instance_id}:{conv_key}"
                with _followups_in_flight_lock:
                    if fu_key in _followups_in_flight:
                        continue
                    _followups_in_flight.add(fu_key)
                threading.Thread(
                    target=_run_followup,
                    args=(instance_id, conv_key, inst, live_conv, fu_key),
                    daemon=True,
                    name=f"fu-{instance_id}-{conv_key}",
                ).start()
            except Exception as e:
                logger.warning(f"[Followup] Erro ao checar {phone}: {e}")


def _check_auto_release():
    """
    Retoma IA automaticamente quando o operador fica X minutos sem enviar
    mensagem (ai_paused=True). O gatilho é inatividade do operador, não do lead.
    """
    now = datetime.now(timezone.utc)
    with instances_lock:
        instance_items = [(iid, dict(inst)) for iid, inst in instances.items()]
    for instance_id, inst in instance_items:
        release_min = inst.get("auto_release_minutes", 3)
        if not release_min or release_min <= 0:
            continue
        release_secs = release_min * 60
        # Copia só as conversas pausadas (candidatas) — roda a cada 60s, copiar
        # todas as conversas de todas as instâncias não escala.
        with conversations_lock:
            conv_items = [
                (phone, dict(conv))
                for phone, conv in conversations.get(instance_id, {}).items()
                if conv.get("ai_paused") and conv.get("stage") not in ("handoff", "disqualified")
            ]
        for phone, conv in conv_items:
            try:
                msgs = conv.get("messages", [])
                # Pega a última mensagem do operador humano
                last_human = next((m for m in reversed(msgs) if m["role"] == "human"), None)
                if not last_human:
                    continue
                ts_str = last_human.get("timestamp", "")
                if not ts_str:
                    continue
                last_ts = datetime.fromisoformat(ts_str)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                elapsed = (now - last_ts).total_seconds()
                if elapsed < release_secs:
                    continue
                with conversations_lock:
                    conv_key = _resolve_conversation_key(instance_id, phone)
                    live_conv = conversations.get(instance_id, {}).get(conv_key)
                    if not live_conv:
                        continue
                    # Operador inativo por X min — retoma IA
                    live_conv["ai_paused"] = False
                    live_conv.pop("ai_resume_after", None)
                _save_conversation(instance_id, conv_key)
                logger.info(f"[AutoRelease] {instance_id}/{conv_key} ? IA retomada após {release_min} min de inatividade do operador")
                # Responde à última mensagem do lead (se houver)
                with instances_lock:
                    inst_ref = dict(instances.get(instance_id, {}))
                last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
                if last_user and inst_ref:
                    threading.Thread(
                        target=_ai_respond_to_conv,
                        args=(instance_id, conv_key, inst_ref, live_conv, last_user["content"]),
                        daemon=True,
                        name=f"autorelease-{instance_id}-{conv_key}",
                    ).start()
            except Exception as e:
                logger.info(f"[AutoRelease] Erro {instance_id}/{phone}: {e}")


_followup_tick_count = 0


def _followup_tick():
    """Verifica auto-release a cada disparo (60s) e follow-ups a cada 15 disparos (15min)."""
    global _followup_tick_count
    _followup_tick_count += 1
    try:
        _check_auto_release()
    except Exception as e:
        logger.info(f"[AutoRelease Worker] {e}")
    if _followup_tick_count >= 15:
        _followup_tick_count = 0
        try:
            _check_followups()
        except Exception as e:
            logger.info(f"[Followup Worker] {e}")


# -- Retry worker para eventos CAPI que falharam -------------------------------

_CAPI_RETRY_MAX_ATTEMPTS = 3  # tentativas adicionais após as 3 imediatas


def _capi_retry_tick():
    """Processa a fila persistente de retry de eventos CAPI."""
    try:
        now = time.time()
        queue = load_capi_retry_queue()
        for item in queue:
            if item.get("next_retry_at", 0) > now:
                continue

            instance_id = item.get("instance_id", "")
            with instances_lock:
                inst = dict(instances.get(instance_id, {})) if instance_id else {}

            remove_capi_retry_item(item["id"])

            if not inst:
                logger.info(f"[META-CAPI-RETRY] Instância '{instance_id}' não encontrada — descartando evento.")
                continue

            lead = item.get("lead", {})
            origem = item.get("origem", "crm")
            event_name = item.get("event_name", "Lead")
            attempt = item.get("attempt", 0)

            logger.info(f"[META-CAPI-RETRY] Tentativa adicional {attempt + 1} para '{event_name}' — instância={instance_id}")
            ok, detail = send_meta_capi_event(inst, lead, origem=origem, event_name=event_name)

            if ok:
                logger.info(f"[META-CAPI-RETRY] Sucesso na tentativa adicional {attempt + 1} para '{event_name}'.")
                continue

            if is_permanent_capi_failure(detail):
                logger.info(f"[META-CAPI-RETRY] Falha permanente para '{event_name}': {detail}")
                continue

            if attempt + 1 < _CAPI_RETRY_MAX_ATTEMPTS:
                enqueue_capi_retry(instance_id, lead, origem=origem, event_name=event_name, attempt=attempt + 1)
            else:
                logger.info(f"[META-CAPI-RETRY] Desistindo de '{event_name}' após {attempt + 1} tentativas adicionais.")
                _append_alert(
                    "capi_falha",
                    f"Evento CAPI '{event_name}' falhou após todas as tentativas",
                    instance_id=instance_id,
                    detail=detail[:300],
                )

    except Exception as exc:
        logger.info(f"[META-CAPI-RETRY] Erro no worker: {exc}")


# -- Meta Lead Ads & Forms webhooks (must be before the catch-all /{instance_id}) -

def _meta_webhook_verify(hub_mode: str, hub_verify_token: str, hub_challenge: str):
    """
    Verificação GET exigida pela Meta ao registrar qualquer webhook.
    Aceita o WEBHOOK_SECRET global (legado) OU o webhook_secret de qualquer
    instância — cada cliente cadastra o webhook no painel Meta dele usando
    o token da própria instância (multi-tenant).
    """
    if hub_mode == "subscribe" and hub_verify_token:
        if WEBHOOK_SECRET and hmac.compare_digest(hub_verify_token, WEBHOOK_SECRET):
            logger.warning("[DEPRECATED] Verificação Meta aceita via WEBHOOK_SECRET global; migre para webhook_secret por instância.")
            return PlainTextResponse(hub_challenge)
        with instances_lock:
            for inst in instances.values():
                secret = inst.get("webhook_secret", "")
                if secret and hmac.compare_digest(hub_verify_token, secret):
                    return PlainTextResponse(hub_challenge)
    return PlainTextResponse("forbidden", status_code=403)


def _extract_meta_field_data(field_data: list) -> dict:
    result = {}
    for field in field_data:
        name = field.get("name", "")
        values = field.get("values", [])
        result[name] = values[0] if values else ""
    return result


def _normalize_meta_field_name(name: str) -> str:
    """
    Normaliza chaves de campos da Meta para comparação tolerante a acentos,
    espaços e pontuação.
    """
    normalized = (name or "").strip().lower()
    normalized = re.sub(r"[^\w]+", "_", normalized, flags=re.UNICODE)
    return normalized.strip("_")


def _get_meta_field(fields: dict, *aliases: str) -> str:
    normalized_fields = {
        _normalize_meta_field_name(key): value
        for key, value in fields.items()
    }
    for alias in aliases:
        value = normalized_fields.get(_normalize_meta_field_name(alias), "")
        if value:
            return value
    return ""


def _extract_meta_lead_change_value(entry: dict) -> dict:
    for change in entry.get("changes", []):
        if change.get("field") == "leadgen":
            return change.get("value", {}) or {}
    return {}


def _validate_meta_token(token: str, phone_number_id: str = "") -> dict:
    """Valida token Meta. Tenta /{phone_number_id} (System User / WhatsApp Business),
    depois /me (User Token). Retorna {"ok": True, "name": "..."} ou {"ok": False, "reason": "..."}"""
    if not token:
        return {"ok": False, "reason": "token vazio"}
    try:
        # Tokens de System User do WhatsApp Business funcionam no endpoint do phone_number_id,
        # mas falham em /me. Tentar primeiro com phone_number_id quando disponível.
        if phone_number_id:
            r = requests.get(
                f"https://graph.facebook.com/v25.0/{phone_number_id}",
                params={"access_token": token, "fields": "id,display_phone_number,verified_name"},
                timeout=10,
            )
            data = r.json()
            if r.status_code == 200 and data.get("id"):
                name = data.get("verified_name") or data.get("display_phone_number") or phone_number_id
                return {"ok": True, "name": name}
            # Se falhou por motivo que não seja autenticação, tenta /me como fallback
            err_code = (data.get("error") or {}).get("code", 0)
            if err_code not in (190, 102, 200):
                # Erro de permissão/objeto — token é válido mas sem acesso ao número
                return {"ok": True, "name": "token válido"}

        # Fallback: /me (User Token ou token com permissão ampla)
        r = requests.get(
            "https://graph.facebook.com/v25.0/me",
            params={"access_token": token, "fields": "id,name"},
            timeout=10,
        )
        data = r.json()
        if r.status_code == 200 and data.get("id"):
            return {"ok": True, "name": data.get("name", "")}
        error_msg = (data.get("error") or {}).get("message", f"HTTP {r.status_code}")
        return {"ok": False, "reason": error_msg}
    except requests.RequestException as exc:
        return {"ok": False, "reason": str(exc)}


def _subscribe_meta_page_leadgen(page_token: str, page_id: str) -> dict:
    """Assina a Página para receber webhooks de leadgen.
    Se page_token for um Page Access Token (começa com 'EAA' e contém 'EAAB' ou tem mais de 100 chars),
    usa diretamente. Caso contrário, tenta via /me/accounts (User Token).
    Retorna {"ok": True} ou {"ok": False, "reason": "..."}
    """
    if not page_token or not page_id:
        return {"ok": False, "reason": "meta_page_access_token ou meta_page_id vazio"}
    try:
        # Tentar usar o token diretamente como Page Token
        r2_direct = requests.post(
            f"https://graph.facebook.com/v25.0/{page_id}/subscribed_apps",
            data={"subscribed_fields": "leadgen", "access_token": page_token},
            timeout=15,
        )
        if r2_direct.status_code == 200 and r2_direct.json().get("success"):
            return {"ok": True}
        # Fallback: tentar via /me/accounts (User Token)
        r = requests.get(
            "https://graph.facebook.com/v25.0/me/accounts",
            params={"access_token": page_token, "fields": "id,name,access_token"},
            timeout=15,
        )
        if r.status_code != 200:
            direct_msg = (r2_direct.json().get("error") or {}).get("message", "Token inválido")
            return {"ok": False, "reason": direct_msg}
        pages = r.json().get("data", [])
        resolved_token = next((p["access_token"] for p in pages if p.get("id") == page_id), None)
        if not resolved_token:
            return {"ok": False, "reason": f"Página {page_id} não encontrada nas contas do token"}
        r2 = requests.post(
            f"https://graph.facebook.com/v25.0/{page_id}/subscribed_apps",
            data={"subscribed_fields": "leadgen", "access_token": resolved_token},
            timeout=15,
        )
        if r2.status_code == 200 and r2.json().get("success"):
            return {"ok": True}
        r2_msg = (r2.json().get("error") or {}).get("message", f"HTTP {r2.status_code}")
        return {"ok": False, "reason": r2_msg}
    except requests.RequestException as exc:
        return {"ok": False, "reason": str(exc)}


def _fetch_meta_lead_details(leadgen_id: str, access_token: str) -> dict:
    if not leadgen_id or not access_token:
        return {}
    try:
        response = requests.get(
            f"https://graph.facebook.com/v25.0/{leadgen_id}",
            params={"access_token": access_token},
            timeout=15,
        )
        if response.status_code != 200:
            # Não loga response.text — pode conter detalhes do token da Meta
            logger.info(f"[META-LEADS] Falha ao buscar lead {leadgen_id}: HTTP {response.status_code}")
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}
    except requests.RequestException as exc:
        logger.info(f"[META-LEADS] Erro ao buscar lead {leadgen_id}: {exc}")
        return {}
    except ValueError:
        return {}


def _build_meta_source_meta(value: dict, fields: dict, origem: str) -> dict:
    source_meta = {
        "provider": "meta",
        "origem": origem,
        "leadgen_id": value.get("leadgen_id") or value.get("id", ""),
        "lead_id": value.get("leadgen_id") or value.get("id", ""),
        "form_id": value.get("form_id", ""),
        "page_id": value.get("page_id", ""),
        "created_time": value.get("created_time", ""),
        "ad_id": value.get("ad_id", ""),
        "adgroup_id": value.get("adgroup_id", ""),
        "campaign_name": value.get("campaign_name", ""),
        "form_name": value.get("form_name", ""),
        "field_data": fields,
        "raw_value": value,
    }
    return {k: v for k, v in source_meta.items() if v not in (None, "", [], {})}


def _extract_meta_message_payload(text: str) -> dict:
    """
    Separa a mensagem automática da Meta em blocos organizados.

    Muitos leads CTWA chegam como texto livre seguido de linhas `campo: valor`.
    Aqui preservamos:
    - `message_text`: parte conversacional para inbox/IA
    - `lead_fields`: nome/email/telefone extraídos do texto
    - `form_answers`: respostas do formulário, em chaves normalizadas
    - `form_question_labels`: rótulos originais das perguntas para auditoria
    """
    body = (text or "").strip()
    if not body:
        return {}

    field_pattern = re.compile(r"^\s*([^:\n]{2,200}?)\s*:\s*(.+?)\s*$")
    canonical_field_aliases = {
        "full_name": "nome_completo",
        "nome_completo": "nome_completo",
        "nome": "nome_completo",
        "name": "nome_completo",
        "first_name": "nome_completo",
        "email": "email",
        "e_mail": "email",
        "telefone": "telefone",
        "phone_number": "telefone",
        "phone": "telefone",
        "whatsapp": "telefone",
    }

    message_lines: list[str] = []
    lead_fields: dict[str, str] = {}
    form_answers: dict[str, str] = {}
    form_question_labels: dict[str, str] = {}

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if message_lines and message_lines[-1] != "":
                message_lines.append("")
            continue
        match = field_pattern.match(line)
        if not match:
            message_lines.append(line)
            continue

        label = match.group(1).strip()
        value = match.group(2).strip()
        normalized_label = _normalize_meta_field_name(label)
        canonical_key = canonical_field_aliases.get(normalized_label)
        if canonical_key:
            lead_fields[canonical_key] = value
            continue

        form_answers[normalized_label] = value
        form_question_labels[normalized_label] = label

    message_text = "\n".join(message_lines).strip()
    payload = {
        "message_text": message_text or body,
        "lead_fields": lead_fields,
        "form_answers": form_answers,
        "form_question_labels": form_question_labels,
        "raw_text": body,
    }
    return {k: v for k, v in payload.items() if v not in (None, "", [], {})}


def _build_meta_message_source_meta(msg: dict, contact: dict, referral: dict, origem: str) -> dict:
    parsed_text = _extract_meta_message_payload((msg.get("text") or {}).get("body", ""))
    source_meta = {
        "provider": "meta",
        "origem": origem,
        "channel": "whatsapp_cloud_api",
        "message_id": msg.get("id", ""),
        "wa_id": contact.get("wa_id", "") or msg.get("from", ""),
        "user_id": contact.get("user_id", "") or msg.get("from_user_id", ""),
        "parent_user_id": contact.get("parent_user_id", "") or msg.get("parent_user_id", ""),
        "username": (
            contact.get("username", "")
            or (contact.get("profile") or {}).get("username", "")
            or msg.get("username", "")
        ),
        "profile": dict(contact.get("profile") or {}),
        "profile_name": (contact.get("profile") or {}).get("name", "") or msg.get("profile_name", ""),
        "referral": dict(referral or {}),
        "ctwa_clid": (referral or {}).get("ctwa_clid", ""),
        "raw_message": dict(msg or {}),
        "message_text": parsed_text.get("message_text", ""),
        "lead_fields": parsed_text.get("lead_fields", {}),
        "form_answers": parsed_text.get("form_answers", {}),
        "form_question_labels": parsed_text.get("form_question_labels", {}),
        "raw_text": parsed_text.get("raw_text", ""),
    }
    return {k: v for k, v in source_meta.items() if v not in (None, "", [], {})}


def _parse_meta_lead_entry(entry: dict, origem: str, fetched_value: dict | None = None) -> dict | None:
    """Extrai dados de um entry do payload Meta Lead Ads / Forms."""
    changes = entry.get("changes", [])
    for change in changes:
        value = dict(change.get("value", {}) or {})
        if fetched_value:
            merged_value = dict(fetched_value)
            merged_value.update({k: v for k, v in value.items() if v not in (None, "", [], {})})
            value = merged_value
        field_data = value.get("field_data", [])
        if not field_data:
            leadgen_id = value.get("leadgen_id", "")
            _append_alert(
                "lead_sem_campos",
                f"Lead descartado: formulário sem campos (leadgen_id={leadgen_id or '—'})",
                detail=f"origem={origem} page_id={value.get('page_id', '—')}",
            )
            continue
        fields = _extract_meta_field_data(field_data)

        nome = _get_meta_field(
            fields,
            "full_name",
            "nome_completo",
            "nome",
            "name",
            "first_name",
        )
        last = _get_meta_field(fields, "last_name")
        if last:
            nome = f"{nome} {last}".strip()

        telefone = _get_meta_field(
            fields,
            "phone_number",
            "telefone",
            "phone",
            "whatsapp",
        )
        telefone = _conversation_phone_key(telefone)

        email = _get_meta_field(fields, "email", "e-mail")
        cep = _get_meta_field(fields, "cep", "postal_code", "zip", "zip_code", "zipcode", "cep_endereco")

        padrao = {
            _normalize_meta_field_name(field_name)
            for field_name in (
                "full_name",
                "nome_completo",
                "nome",
                "name",
                "first_name",
                "last_name",
                "phone_number",
                "telefone",
                "phone",
                "whatsapp",
                "email",
                "e-mail",
                "cep",
                "postal_code",
                "zip",
                "zip_code",
                "zipcode",
                "cep_endereco",
            )
        }
        extras = {
            k: v
            for k, v in fields.items()
            if _normalize_meta_field_name(k) not in padrao and v
        }
        campanha = " | ".join(f"{k}: {v}" for k, v in extras.items()) if extras else origem
        extras_formatted = "\n".join(
            f"• {k.replace('_', ' ').strip('?').strip().capitalize()}: {v}"
            for k, v in extras.items()
        ) if extras else ""

        return {
            "nome":             nome,
            "telefone":         telefone,
            "email":            email,
            "cep":              cep,
            "campanha":         campanha,
            "extras_formatted": extras_formatted,
            "leadgen_id":       value.get("leadgen_id") or value.get("id", ""),
            "form_id":          value.get("form_id", ""),
            "page_id":          value.get("page_id", str(entry.get("id", ""))),
            "source_meta":      _build_meta_source_meta(value, fields, origem),
        }
    return None


def _find_instance_by_page_id(page_id: str, form_id: str = "") -> tuple[str, dict] | tuple[None, None]:
    """Resolve formulário/Página configurados; mantém fallback legado."""
    if not page_id:
        return None, None
    routed_iid = meta_oauth_pg_service.resolve_lead_instance(page_id, form_id)
    if routed_iid:
        routed_inst = instances.get(routed_iid)
        if routed_inst:
            return routed_iid, routed_inst
    for iid, inst in instances.items():
        if inst.get("meta_page_id") == page_id and inst.get("provider") == "meta":
            return iid, inst
    return None, None


def _find_fallback_meta_instance() -> tuple[str, dict] | tuple[None, None]:
    """
    Fallback conservador para instalações com uma única instância Meta.

    Isso evita quebrar o inbox quando o payload chega sem page_id compatível,
    mas não tenta adivinhar em ambientes com múltiplas instâncias.
    """
    meta_instances = []
    for iid, inst in instances.items():
        if inst.get("provider") == "meta":
            meta_instances.append((iid, inst))
    if len(meta_instances) == 1:
        return meta_instances[0]
    return None, None


def _lead_already_known_to_meta(lead: dict) -> bool:
    """
    True quando o lead já tem `leadgen_id` — ou seja, já veio de um formulário nativo
    (Lead Ads) que a Meta contabilizou sozinha. Reenviar 'Lead' via CAPI nesse caso não
    funde com a submissão nativa; só soma um evento extra no Gerenciador de Eventos.
    """
    leadgen_id = (lead.get("leadgen_id") or "").strip()
    if leadgen_id:
        return True
    source_meta = lead.get("source_meta") or {}
    if isinstance(source_meta, dict):
        return bool((source_meta.get("leadgen_id") or "").strip())
    return False


def _try_link_existing_leadgen_id(lead: dict) -> bool:
    """
    Quando o lead foi cadastrado manualmente (sem leadgen_id), cruza o telefone com
    submissões de formulário Meta já recebidas. Se achar, vincula o leadgen_id ao lead
    no CRM (persistente) e retorna True — evita disparar 'Lead' de novo pra alguém que
    a Meta já conhece, mesmo que o cadastro manual não tenha colado o ID.
    """
    phone = _conversation_phone_key(lead.get("telefone", "") or "")
    if not phone:
        return False
    try:
        leadgen_id = meta_pg_service.find_leadgen_id_by_phone(phone)
    except Exception:
        return False
    if not leadgen_id:
        return False
    try:
        lead_id = lead.get("id", "")
        if lead_id:
            crm_svc.update_fields(lead_id, leadgen_id=leadgen_id)
        lead["leadgen_id"] = leadgen_id
    except Exception:
        logger.debug("Falha ao extrair corpo do criativo", exc_info=True)
    logger.info(
        f"[META-CAPI] leadgen_id={leadgen_id} vinculado retroativamente ao lead "
        f"{lead.get('id', '')} via telefone — evento 'Lead' não será reenviado."
    )
    return True


def _maybe_queue_meta_capi_event(inst: dict | None, lead: dict, origem: str, event_name: str = "Lead"):
    if not inst:
        return
    if not is_meta_capi_enabled(inst, lead):
        return
    # O provider define somente o transporte do WhatsApp (Evolution GO ou
    # Cloud API). CAPI é independente: as credenciais são resolvidas pelo
    # send_meta_capi_event, priorizando OAuth da instância e usando a
    # configuração manual como fallback. Não exigir token manual aqui.
    if event_name == "Lead":
        if _lead_already_known_to_meta(lead) or _try_link_existing_leadgen_id(lead):
            logger.info(
                f"[META-CAPI] Evento 'Lead' ignorado para lead {lead.get('id', '')} — "
                f"já possui leadgen_id (Meta já contabilizou nativamente pelo formulário)."
            )
            return

    instance_id = str(lead.get("instancia_id") or "").strip()

    try:
        from copy import deepcopy as _deepcopy
        inst_copy = _deepcopy(inst)
        lead_copy = _deepcopy(lead)
    except Exception:
        inst_copy, lead_copy = inst, lead

    def _worker():
        try:
            ok, detail = send_meta_capi_event(inst_copy, lead_copy, origem=origem, event_name=event_name)
            if not ok:
                if is_permanent_capi_failure(detail):
                    logger.info(f"[META-CAPI] Evento '{event_name}' ignorado (falha permanente): {detail}")
                    return
                logger.info(f"[META-CAPI] Falha após retentativas '{event_name}': {detail}")
                if instance_id:
                    enqueue_capi_retry(instance_id, lead_copy, origem=origem, event_name=event_name)
        except Exception as exc:
            logger.info(f"[META-CAPI] Erro inesperado: {exc}")

    threading.Thread(target=_worker, daemon=True, name=f"meta-capi-{event_name.lower()}").start()


def _record_webhook_event(provider: str, instancia_id: str, event_type: str, payload: dict, dedupe_key: str = ""):
    try:
        ok, detail = meta_pg_service.record_webhook_event(
            provider=provider,
            instancia_id=instancia_id,
            event_type=event_type,
            payload=payload,
            dedupe_key=dedupe_key,
        )
        if not ok:
            logger.warning(f"[WEBHOOK-EVENTS] Falha ao persistir evento {event_type}: {detail}")
    except Exception as exc:
        logger.info(f"[WEBHOOK-EVENTS] Falha ao persistir evento {event_type}: {exc}")


def _record_meta_form_submission(lead: dict, origem: str, payload: dict):
    source_meta = payload.get("source_meta") or {}
    try:
        meta_pg_service.record_meta_form_submission(
            lead_id=lead.get("id", ""),
            instancia_id=lead.get("instancia_id", ""),
            origem=origem,
            page_id=payload.get("page_id", "") or source_meta.get("page_id", ""),
            form_id=payload.get("form_id", "") or source_meta.get("form_id", ""),
            leadgen_id=payload.get("leadgen_id", "") or source_meta.get("leadgen_id", ""),
            payload=payload,
        )
    except Exception as exc:
        logger.info(f"[META-FORMS] Falha ao persistir submissao: {exc}")


def _meta_stage_event_for_column(lead: dict) -> str | None:
    coluna_id = (lead.get("coluna_id") or "").strip().lower()

    # 1. Campo capi_event configurado explicitamente na coluna (prioridade máxima)
    try:
        col = next((c for c in crm_svc.get_columns() if c["id"] == coluna_id), None)
        if col:
            capi_event = (col.get("capi_event") or "").strip()
            if capi_event == "none":
                return None  # usuário configurou explicitamente "não disparar"
            if capi_event in ("Lead", "Contact", "QualifiedLead", "SubmitApplication", "Schedule", "Purchase"):
                return capi_event
    except Exception:
        logger.debug("Falha ao agregar hora do criativo", exc_info=True)

    # 2. Fallback: heurística por nome da coluna
    coluna_nome = ""
    try:
        coluna_nome = (crm_svc._col_name(coluna_id) or "").strip().lower()
    except Exception:
        coluna_nome = ""
    if any(token in coluna_nome for token in ("desqual", "descart", "perd", "lost")):
        return None
    mapping = {
        "novo": "Lead",
        "qualif": "Contact",
        "quente": "SubmitApplication",
        "reuniao": "Schedule",
        "fechado": "Purchase",
        "desqualificado": None,
    }
    if coluna_id in mapping:
        return mapping.get(coluna_id)
    if any(token in coluna_nome for token in ("agend", "reuni", "call", "demo")):
        return "Schedule"
    if any(token in coluna_nome for token in ("fech", "compr", "vend", "cliente")):
        return "Purchase"
    if any(token in coluna_nome for token in ("quente", "qualif", "aquec", "reativ", "aguard", "lanca", "lanç")):
        return "SubmitApplication"
    if any(token in coluna_nome for token in ("contato", "follow", "nutri")):
        return "Contact"
    return None


def _meta_stage_event_for_lead(lead: dict) -> str | None:
    status = (lead.get("lead_status") or "").strip().lower().replace(" ", "_")
    if status == "desqualificado":
        return None
    status_mapping = {
        "novo": "Lead",
        "em_contato": "Contact",
        "qualificado": "SubmitApplication",
        "agendado": "Schedule",
        "visita_agendada": "Schedule",
        "vendido": "Purchase",
    }
    if status in status_mapping:
        return status_mapping[status]
    return _meta_stage_event_for_column(lead)


def _build_meta_form_inbox_message(lead: dict, lead_data: dict, origem: str) -> str:
    titulo = "Novo lead recebido via Meta Lead Ads" if origem == "meta-leads" else "Novo lead recebido via Meta Forms"
    partes = [
        titulo,
        f"Nome: {lead.get('nome') or lead_data.get('nome') or '-'}",
        f"Telefone: {lead.get('telefone') or lead_data.get('telefone') or '-'}",
    ]
    email = lead.get("email") or lead_data.get("email") or ""
    if email:
        partes.append(f"Email: {email}")
    cep = lead.get("cep") or lead_data.get("cep") or ""
    if cep:
        partes.append(f"Cep: {cep}")
    extras = (lead_data.get("extras_formatted") or "").strip()
    if extras:
        partes.append("")
        partes.append("Respostas do formulario:")
        partes.append(extras)
    return "\n".join(partes).strip()


def _seed_meta_form_conversation(instance_id: str, lead: dict, lead_data: dict, origem: str) -> None:
    if not instance_id:
        return
    phone = _conversation_phone_key(lead.get("telefone", "") or lead_data.get("telefone", ""))
    if not phone:
        return

    new_source_meta = copy.deepcopy(lead_data.get("source_meta") or lead.get("source_meta") or {})
    message = _build_meta_form_inbox_message(lead, lead_data, origem)
    timestamp = _now_iso()
    needs_save = False
    emit_new_msg = False
    with conversations_lock:
        conv = _get_or_create_conversation(instance_id, phone, lead.get("nome", phone))
        conv["lead_origem"] = origem
        conv["lead_email"] = lead.get("email", "") or lead_data.get("email", "")
        conv["lead_campanha"] = lead.get("campanha", "") or lead_data.get("campanha", "")
        # Merge: preserva dados CTWA existentes e adiciona dados do formulário (leadgen_id etc.)
        if new_source_meta:
            merged = dict(conv.get("source_meta") or {})
            merged.update(new_source_meta)
            conv["source_meta"] = merged
            needs_save = True
        already_seeded = any(
            m.get("role") == "user"
            and (m.get("content") or "").startswith("Novo lead recebido via Meta")
            for m in conv.get("messages", [])
        )
        if not already_seeded:
            conv["ai_paused"] = True
            conv["unread"] = True
            _mark_needs_human(conv, "Lead recebido por formulario Meta", "meta_form")
            conv.pop("ai_resume_after", None)
            conv["messages"].append({
                "role": "user",
                "content": message,
                "timestamp": timestamp,
            })
            conv["last_activity"] = timestamp
            needs_save = True
            emit_new_msg = True
    if needs_save:
        _save_conversation(instance_id, phone)
    if emit_new_msg:
        _sse_emit(instance_id, "new_message", {"phone": phone, "role": "user", "content": message})
def _crm_patch_leadgen_id(instance_id: str, phone: str, source_meta: dict) -> None:
    """Vincula leadgen_id ao lead CRM existente quando o webhook de formulário chega após CTWA."""
    leadgen_id = source_meta.get("leadgen_id", "")
    if not leadgen_id:
        return
    try:
        for lead in crm_svc.get_leads():
            if lead.get("instancia_id") == instance_id and lead.get("telefone") == phone:
                if lead.get("leadgen_id"):
                    return  # já tem leadgen_id, não sobrescreve
                merged_meta = dict(lead.get("source_meta") or {})
                merged_meta.update(source_meta)
                crm_svc.upsert_lead(
                    telefone=phone,
                    nome=lead.get("nome", ""),
                    origem=lead.get("origem", ""),
                    instancia_id=instance_id,
                    source_meta=merged_meta,
                )
                logger.info(f"[META-LEADS] leadgen_id {leadgen_id} vinculado ao lead {lead.get('id')} no CRM")
                return
    except Exception as exc:
        logger.info(f"[META-LEADS] Falha ao atualizar leadgen_id no CRM: {exc}")


def _tag_lead_data_for_roleta(instance_id: str, lead_data: dict) -> None:
    """
    Com a roleta automática ATIVA para o dono desta instância, marca
    lead_data["source_meta"]["roleta_active"] = True antes de semear a
    conversa — _seed_meta_form_conversation faz merge disso em
    conv["source_meta"], então a conversa aparece no InboxSDR com a tag
    ROLETA (o frontend lê esse campo pra exibir o badge). O lead NÃO entra
    no CRM automaticamente — isso continua só acontecendo quando o
    atendente qualifica e envia manualmente, como já era antes. A roleta
    (distribuição entre atendentes) roda depois, no momento desse envio.
    """
    tenant_key = assignment_svc.auto_assign_tenant_key_for_instance(instance_id)
    if not tenant_key:
        return
    lead_data.setdefault("source_meta", {})["roleta_active"] = True


def _process_meta_webhook_body(body: dict, origem: str):
    """Processa payload Meta: salva lead no CRM e dispara SDR se instância configurada."""
    saved = 0
    for entry in body.get("entry", []):
        raw_value = _extract_meta_lead_change_value(entry)
        page_id = raw_value.get("page_id", str(entry.get("id", "")))
        matched_iid, matched_inst = _find_instance_by_page_id(page_id, raw_value.get("form_id", ""))
        if not matched_iid or not matched_inst:
            fallback_iid, fallback_inst = _find_fallback_meta_instance()
            if fallback_iid and fallback_inst:
                matched_iid, matched_inst = fallback_iid, fallback_inst
        fetched_value = {}
        if raw_value.get("leadgen_id") and not raw_value.get("field_data"):
            # meta_page_access_token (Page Token dedicado) tem prioridade sobre
            # meta_token quando preenchido — ver routers/instances.py.
            access_token = (matched_inst or {}).get("meta_page_access_token", "") or (matched_inst or {}).get("meta_token", "")
            fetched_value = _fetch_meta_lead_details(raw_value.get("leadgen_id", ""), access_token)
        dedupe_key = ""
        if raw_value.get("leadgen_id"):
            dedupe_key = f"meta:{origem}:{raw_value.get('leadgen_id')}"
        _record_webhook_event(
            provider="meta",
            instancia_id=matched_iid or "meta",
            event_type=origem,
            payload={"entry": entry, "fetched_value": fetched_value},
            dedupe_key=dedupe_key,
        )
        lead_data = _parse_meta_lead_entry(entry, origem, fetched_value=fetched_value)
        if not lead_data or not lead_data["telefone"]:
            leadgen_id = raw_value.get("leadgen_id", "")
            nome = (lead_data or {}).get("nome", "")
            _append_alert(
                "lead_sem_telefone",
                f"Lead descartado: sem telefone — {nome or 'nome desconhecido'} (leadgen_id={leadgen_id or '—'})",
                instance_id=matched_iid or "",
                detail=f"origem={origem} page_id={raw_value.get('page_id', '—')}",
            )
            continue
        lead_phone = _conversation_phone_key(lead_data["telefone"])
        instancia_id = matched_iid or "meta"

        if origem in ("meta-leads", "meta-forms"):
            # A instância já foi resolvida pela rota de formulário/Página acima.
            # Persisti-la junto da submissão mantém auditoria e backfill isolados
            # por tenant; passar um dict vazio gravava instancia_id="".
            _record_meta_form_submission({"instancia_id": instancia_id}, origem, lead_data)
        if matched_iid:
            _tag_lead_data_for_roleta(matched_iid, lead_data)
            _seed_meta_form_conversation(matched_iid, lead_data, lead_data, origem)
            # Atualizar leadgen_id no CRM se o lead já existir (ex: veio por CTWA antes)
            _crm_patch_leadgen_id(matched_iid, lead_phone, lead_data.get("source_meta") or {})
        # Só o fim do telefone no log — nome e número completo são PII do lead.
        logger.info(f"[{origem.upper()}] Lead salvo: ...{str(lead_phone)[-4:]} ? instância={instancia_id}")

        saved += 1
    return saved

# -- Webhook helpers ------------------------------------------------------------

INTERNAL_WEBHOOK_BASE = os.getenv("WEBHOOK_BASE_URL", "http://klead_backend:8000")
PUBLIC_WEBHOOK_BASE   = os.getenv("PUBLIC_WEBHOOK_BASE", "").strip()


def _build_webhook_url(instance_id: str, internal: bool = True, secret_override: str | None = None) -> str:
    if secret_override is None:
        with instances_lock:
            secret = instances.get(instance_id, {}).get("webhook_secret", "")
    else:
        secret = secret_override
    token_param = f"?token={secret}" if secret else ""
    if internal:
        # Usa o nome do serviço no Docker Swarm para comunicação direta na mesma rede
        # Ignora bloqueios do Cloudflare WAF e evita problemas com IPs dinâmicos
        base_url = INTERNAL_WEBHOOK_BASE
    else:
        base_url = PUBLIC_WEBHOOK_BASE if PUBLIC_WEBHOOK_BASE else INTERNAL_WEBHOOK_BASE
    return f"{base_url}/webhook/{instance_id}{token_param}"


def _register_webhook_evolution(instance_id: str, inst: dict) -> dict:
    """
    Registra (ou atualiza) o webhook no Evolution GO para esta instância.
    Usa evo_instance_name (se configurado) como identificador na API do Evolution,
    mantendo instance_id do KLEAD na URL do webhook.
    """
    import requests as req
    api_url = inst.get("api_url", "").rstrip("/")
    api_key = inst.get("api_key", "")
    if not api_url or not api_key:
        return {"ok": False, "detail": "api_url ou api_key não configurados"}

    # Nome da instância no Evolution GO (pode ser diferente do instance_id do KLEAD)
    evo_name = inst.get("evo_instance_name", "").strip() or instance_id

    full_url = _build_webhook_url(instance_id, secret_override=inst.get("webhook_secret", ""))
    payload = {
        "webhook": {
            "enabled": True,
            "url": full_url,
            "webhookByEvents": True,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT"],
        }
    }
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    try:
        r = req.put(f"{api_url}/webhook/set/{evo_name}", json=payload, headers=headers, timeout=10)
        if r.status_code in (200, 201):
            return {"ok": True, "webhook_url": full_url, "evo_instance": evo_name}
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    except Exception as e:
        logger.warning("Falha ao consultar Postgres do Evolution GO", exc_info=True)
        return {"ok": False, "detail": "Erro ao consultar o banco do Evolution GO."}


def _register_webhook_postgres(instance_id: str, inst: dict) -> dict:
    """Registra webhook de uma instância diretamente no PostgreSQL do Evolution GO."""
    host     = os.getenv("EVO_PG_HOST", "")
    port     = int(os.getenv("EVO_PG_PORT", "5432"))
    user     = os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("EVO_PG_PASSWORD", "")
    dbname   = os.getenv("EVO_PG_DB", "evogo_users")
    if not host or not password:
        return {"ok": False, "detail": "EVO_PG_HOST/EVO_PG_PASSWORD não configurados"}
    try:
        import psycopg2
        evo_name    = inst.get("evo_instance_name", "").strip() or instance_id
        webhook_url = _build_webhook_url(instance_id, secret_override=inst.get("webhook_secret", ""))
        conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("UPDATE instances SET webhook=%s, events=%s WHERE name=%s", (webhook_url, "MESSAGE", evo_name))
        rows = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if rows > 0:
            logger.info(f"[WEBHOOK] Registrado via SQL: {instance_id} ({evo_name})")
            return {"ok": True, "webhook_url": webhook_url}
        return {"ok": False, "detail": f"Instância '{evo_name}' não encontrada no banco do Evolution GO"}
    except ImportError:
        return {"ok": False, "detail": "psycopg2 não instalado"}
    except Exception as e:
        logger.warning("Falha ao consultar Postgres do Evolution GO", exc_info=True)
        return {"ok": False, "detail": "Erro ao consultar o banco do Evolution GO."}


def _sync_webhooks_postgres():
    """
    Sincroniza webhooks diretamente no banco PostgreSQL do Evolution GO.
    Roda no startup e garante que todos os webhooks estejam corretos,
    mesmo após rebuild do container ou reinstalação do banco.
    Fallback: tenta a Evolution REST API para instâncias sem acesso ao banco.
    """
    host     = os.getenv("EVO_PG_HOST", "")
    port     = int(os.getenv("EVO_PG_PORT", "5432"))
    user     = os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("EVO_PG_PASSWORD", "")
    dbname   = os.getenv("EVO_PG_DB", "evogo_users")

    synced_via_postgres = set()

    if host and password:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=host, port=port, user=user,
                password=password, dbname=dbname,
                connect_timeout=5,
            )
            cur = conn.cursor()
            for instance_id, inst in instances.items():
                if not has_evolution_outbound(inst):
                    continue
                evo_name    = inst.get("evo_instance_name", "").strip() or instance_id
                webhook_url = _build_webhook_url(instance_id)
                cur.execute(
                    "UPDATE instances SET webhook=%s WHERE name=%s",
                    (webhook_url, evo_name),
                )
                if cur.rowcount > 0:
                    synced_via_postgres.add(instance_id)
                    logger.info(f"[STARTUP] Webhook sincronizado via SQL: {instance_id} ({evo_name})")
                else:
                    logger.info(f"[STARTUP] Instancia '{evo_name}' nao encontrada no banco — verifique evo_instance_name")
            conn.commit()
            cur.close()
            conn.close()
        except ImportError:
            logger.warning("[STARTUP] psycopg2 nao instalado — pulando sync SQL")
        except Exception as e:
            logger.warning(f"[STARTUP] Erro ao sincronizar webhooks via SQL: {e}")
    else:
        logger.warning("[STARTUP] EVO_PG_HOST/EVO_PG_PASSWORD nao configurados — pulando sync SQL")

    # Fallback: REST API para instâncias que não foram sincronizadas via SQL
    for instance_id, inst in instances.items():
        if instance_id in synced_via_postgres:
            continue
        if not has_evolution_outbound(inst):
            continue
        result = _register_webhook_evolution(instance_id, inst)
        if result.get("ok"):
            logger.info(f"[STARTUP] Webhook re-registrado via REST API: {instance_id}")


_auto_save_tick_count = 0


def _auto_save_tick():
    """
    A cada 60s: salva configurações e retenta upserts de conversa que falharam.
    A cada CONV_FULL_SYNC_MINUTES (default 60): sync completo de conversas como
    reparo de deriva — as gravações por evento já acontecem em _save_conversation,
    então o snapshot completo (delete + regrava, custo O(tudo)) sai do caminho quente.
    """
    global _auto_save_tick_count
    full_sync_minutes = max(int(os.getenv("CONV_FULL_SYNC_MINUTES", "60")), 1)
    _auto_save_tick_count += 1
    try:
        _flush_dirty_instances()
    except Exception as exc:
        logger.warning(f"[AutoSave] Falha no retry de configs: {exc}")
    try:
        _flush_dirty_conversations()
    except Exception as exc:
        logger.warning(f"[AutoSave] Falha no retry de conversas: {exc}")
    if _auto_save_tick_count >= full_sync_minutes:
        _auto_save_tick_count = 0
        try:
            _save_configs()
        except Exception as exc:
            logger.warning(f"[AutoSave] Falha no sync completo de configs: {exc}")
        try:
            _save_conversations()
        except Exception as exc:
            logger.warning(f"[AutoSave] Falha no sync completo de conversas: {exc}")
        try:
            _purge_deleted_conversations_tick()
        except Exception as exc:
            logger.warning(f"[AutoSave] Falha ao purgar lixeira de conversas: {exc}")
        try:
            _refresh_meta_oauth_tokens_tick()
        except Exception as exc:
            logger.warning(f"[AutoSave] Falha ao renovar tokens Meta OAuth: {exc}")


def _crm_periodic_backup_tick():
    """Salva snapshot do CRM em arquivo. Mantém os últimos 48 backups (2 dias)."""
    crm_snapshot = crm_svc.export_crm()
    if not (crm_snapshot.get("columns") or crm_snapshot.get("leads")):
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = BACKUP_DIR / f"crm.{stamp}.json"
    _atomic_write(dst, json.dumps(crm_snapshot, ensure_ascii=False, indent=2))
    files = sorted(BACKUP_DIR.glob("crm.*.json"))
    for old in files[:-48]:
        old.unlink(missing_ok=True)
    logger.info(f"[BACKUP] CRM periódico — {len(crm_snapshot.get('leads', []))} leads, {len(crm_snapshot.get('columns', []))} colunas → backups/crm.{stamp}.json")


def _do_data_backup(reason: str = "manual") -> None:
    """Snapshot local do CRM em arquivo — camada extra independente do pg_dump.
    Os dados vivem no Postgres; aqui não há mais cópia de JSONs de estado."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        crm_snapshot = crm_svc.export_crm()
        if crm_snapshot.get("columns") or crm_snapshot.get("leads"):
            crm_dst = BACKUP_DIR / f"crm.{stamp}.json"
            _atomic_write(crm_dst, json.dumps(crm_snapshot, ensure_ascii=False, indent=2))
            logger.info(f"[BACKUP] {reason} — crm.json → backups/{stamp}")
    except Exception as exc:
        logger.warning(f"[BACKUP] Falha ao fazer backup do CRM: {exc}")


def _should_backup() -> bool:
    files = sorted(BACKUP_DIR.glob("crm.*.json"))
    if not files:
        return True
    try:
        last_mtime = datetime.fromtimestamp(files[-1].stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - last_mtime) > timedelta(hours=24)
    except Exception:
        return True


atexit.register(lambda: _do_data_backup("shutdown"))


def _migrate_evolution_url() -> None:
    OLD = "http://host.docker.internal:4000"
    NEW = os.getenv("EVO_API_URL", "").strip().rstrip("/")
    if not NEW:
        return
    changed = [iid for iid, inst in instances.items() if inst.get("api_url") == OLD]
    if not changed:
        return
    for iid in changed:
        instances[iid]["api_url"] = NEW
    _save_configs()
    logger.info(f"[MIGRATE] api_url atualizada para {NEW} em {len(changed)} instância(s): {', '.join(changed)}")








# -- Funções helper expostas via app.state para uso dos routers ----------------
# Todas definidas acima neste arquivo; expostas aqui para evitar importação circular.
app.state.now_iso                    = _now_iso
app.state.conversation_phone_key     = _conversation_phone_key
app.state.parse_whatsapp_jid         = _parse_whatsapp_jid
app.state.resolve_conversation_key   = _resolve_conversation_key
app.state.get_or_create_conversation = _get_or_create_conversation
app.state.mark_needs_human           = _mark_needs_human
app.state.clear_needs_human          = _clear_needs_human
app.state.save_conversations         = _save_conversations
app.state.save_conversation          = _save_conversation
app.state.delete_conversation_pg     = _delete_conversation_from_pg
app.state.delete_instance_conversations_pg = _delete_instance_conversations_from_pg
app.state.restore_conversation_pg    = _restore_conversation_from_pg
app.state.list_deleted_conversations_pg = _list_deleted_conversations_pg
app.state.save_configs               = _save_configs
app.state.save_instance_config       = _save_instance_config
app.state.delete_instance_config_pg  = _delete_instance_config_from_pg
app.state.make_fernet_for_user        = _make_fernet_for_user
app.state.encrypt_field                = _encrypt_field
app.state.decrypt_field_for_user       = _decrypt_field_for_user
app.state.send_reply                 = _send_reply
app.state.resolve_ai_settings        = _resolve_ai_settings
app.state.sse_emit                   = _sse_emit
app.state.maybe_queue_meta_capi_event  = _maybe_queue_meta_capi_event
app.state.meta_stage_event_for_lead    = _meta_stage_event_for_lead
app.state.meta_stage_event_for_column  = _meta_stage_event_for_column
# Funções do bloco webhook/SDR/Meta — usadas pelo router webhook
app.state.handle_inbound               = _handle_inbound
app.state.handle_audio_inbound         = _handle_audio_inbound
app.state.handle_media_inbound         = _handle_media_inbound
app.state.extract_message_text         = _extract_message_text
app.state.is_audio_message             = _is_audio_message
app.state.detect_media_message         = media_svc.detect_media_message
app.state.record_webhook_event         = _record_webhook_event
app.state.build_meta_message_source_meta = _build_meta_message_source_meta
app.state.persist_meta_identity_aliases = _persist_meta_identity_aliases
app.state.meta_webhook_verify          = _meta_webhook_verify
app.state.process_meta_webhook_body    = _process_meta_webhook_body
app.state.build_webhook_url            = _build_webhook_url
app.state.register_webhook_evolution   = _register_webhook_evolution
app.state.register_webhook_postgres    = _register_webhook_postgres
app.state.normalize_meta_ad_account_id = _normalize_meta_ad_account_id

# Helpers para routers/instances.py
app.state.ai_config                  = ai_config
app.state.meta_health_cache          = _meta_health_cache
app.state.alerts_lock                = _alerts_lock
app.state.load_knowledge             = _load_knowledge
app.state.ai_config_response         = _ai_config_response
app.state.apply_global_ai_defaults   = _apply_global_ai_defaults_to_instance
app.state.delete_instance_local_data = _delete_instance_local_data
app.state.instance_public_response   = _instance_public_response
app.state.is_masked_secret           = _is_masked_secret
app.state.load_alerts                = _load_alerts
app.state.make_instance              = _make_instance
app.state.mask_secret                = _mask_secret
app.state.resolve_openai_key         = _resolve_openai_key
app.state.safe_filename              = _safe_filename
app.state.safe_instance_path_part    = _safe_instance_path_part
app.state.save_ai_config             = _save_ai_config
app.state.subscribe_meta_page_leadgen = _subscribe_meta_page_leadgen
app.state.validate_api_url           = _validate_api_url
app.state.validate_meta_token        = _validate_meta_token
app.state.atomic_write               = _atomic_write
