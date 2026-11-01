"""
media_service.py — Motor central de mídia (imagem, vídeo, áudio, documento).

Extraído de main.py/conversations.py para não concentrar toda a lógica de
mensageria num único arquivo. Este módulo é "puro" (sem tocar em `instances`,
`conversations` ou qualquer estado global da aplicação) — só sabe classificar,
baixar, guardar e enviar mídia. A orquestração (o que fazer com a mídia
recebida numa conversa) continua em main.py, que já é o dono do estado.

Duas responsabilidades:
  1. Recebimento — detectar mídia num payload de webhook do Evolution GO e
     baixar os bytes de verdade (antes só áudio era baixado; imagem/vídeo/
     documento ficavam só com a legenda, e sem legenda a mensagem sumia).
  2. Envio — imagem, vídeo, documento e áudio são todos enviados via
     `/send/media` (o Evolution GO desta instalação exige o campo `url`
     nesse endpoint e não tem `/send/audio` disponível), usando um link
     temporário self-hosted igual ao usado pra receber.
"""

import hashlib
import hmac
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

import requests

from services.evolution_service import download_media_bytes

logger = logging.getLogger("klead.media")

# ── Classificação de tipos ────────────────────────────────────────────────────

# mimetype → (media_type usado pelo Evolution GO, ícone exibido no Inbox)
MEDIA_MIME_MAP: dict[str, tuple[str, str]] = {
    "image/jpeg":      ("image",    "🖼"),
    "image/jpg":       ("image",    "🖼"),
    "image/png":       ("image",    "🖼"),
    "image/webp":      ("image",    "🖼"),
    "image/gif":       ("image",    "🖼"),
    "video/mp4":       ("video",    "🎬"),
    "video/3gpp":      ("video",    "🎬"),
    "video/quicktime": ("video",    "🎬"),
    "video/webm":      ("video",    "🎬"),
    "application/pdf": ("document", "📄"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":   ("document", "📄"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":         ("document", "📄"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("document", "📄"),
    "audio/mpeg":      ("audio",    "🎵"),
    "audio/mp4":       ("audio",    "🎵"),
    "audio/ogg":       ("audio",    "🎵"),
    "audio/x-m4a":     ("audio",    "🎵"),
    "audio/webm":      ("audio",    "🎵"),
    "audio/aac":       ("audio",    "🎵"),
}

MAX_BYTES_BY_TYPE: dict[str, int] = {
    "image":    5  * 1024 * 1024,   # 5 MB
    "video":    50 * 1024 * 1024,   # 50 MB
    "document": 50 * 1024 * 1024,   # 50 MB
    "audio":    10 * 1024 * 1024,   # 10 MB
}

# Extensão de fallback quando o mimetype não bate com nada conhecido
_EXT_BY_TYPE = {"image": "jpg", "video": "mp4", "document": "pdf", "audio": "ogg"}


def classify_mime(mime: str) -> tuple[str, str] | None:
    """Retorna (media_type, icone) para um mimetype conhecido, ou None."""
    return MEDIA_MIME_MAP.get((mime or "").strip().lower())


def max_bytes_for(media_type: str) -> int:
    return MAX_BYTES_BY_TYPE.get(media_type, 10 * 1024 * 1024)


# ── Storage temporário compartilhado ──────────────────────────────────────────

_TEMP_MEDIA_DIR = Path(os.getenv("KLEAD_TEMP_MEDIA_DIR", Path(__file__).parent.parent / "data" / "media_temp"))
_TEMP_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.[a-z0-9]{1,10}$")


def ensure_temp_dir() -> Path:
    _TEMP_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return _TEMP_MEDIA_DIR


def is_valid_temp_filename(filename: str) -> bool:
    """Nomes são sempre hex(uuid4) + extensão — evita path traversal."""
    return bool(_TEMP_FILENAME_RE.match(filename))


def temp_file_path(filename: str) -> Path:
    return _TEMP_MEDIA_DIR / filename


def media_token(filename: str) -> str:
    """Token HMAC determinístico por arquivo — capability URL assinada.

    Deriva de DASHBOARD_SECRET; quem conhece a URL completa (com ?tk=) consegue
    baixar, mas não dá pra forjar tokens de outros arquivos nem enumerar nomes.
    """
    secret = os.getenv("DASHBOARD_SECRET", "")
    return hmac.new(secret.encode(), f"media:{filename}".encode(), hashlib.sha256).hexdigest()[:32]


def check_media_token(filename: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(media_token(filename), token)


def signed_media_path(filename: str) -> str:
    """Caminho relativo /media/temp/<file>?tk=<hmac> pronto pra uso interno."""
    return f"/media/temp/{filename}?tk={media_token(filename)}"


def _delete_later(path: Path, delay: float) -> None:
    def _run():
        time.sleep(delay)
        try:
            path.unlink()
        except OSError:
            pass
    threading.Thread(target=_run, daemon=True, name="media-cleanup").start()


def save_temp_file(raw: bytes, ext: str, delete_after: float = 3600.0) -> str:
    """Salva bytes num arquivo temporário e agenda a remoção. Retorna o nome do arquivo."""
    ensure_temp_dir()
    ext = (ext or "bin").lstrip(".").lower() or "bin"
    filename = f"{uuid.uuid4().hex}.{ext}"
    (_TEMP_MEDIA_DIR / filename).write_bytes(raw)
    _delete_later(_TEMP_MEDIA_DIR / filename, delete_after)
    return filename


def ext_from_mimetype(mimetype: str, media_type: str = "") -> str:
    """Deriva uma extensão de arquivo a partir do mimetype (com fallback por tipo)."""
    mime = (mimetype or "").split(";")[0].strip().lower()
    if "/" in mime:
        ext = mime.split("/")[-1].split("+")[0]
        if ext in ("jpeg",):
            return "jpg"
        if ext:
            return ext
    return _EXT_BY_TYPE.get(media_type, "bin")


# ── Recebimento (WhatsApp → KLEAD) ────────────────────────────────────────────

def detect_media_message(data: dict) -> dict | None:
    """
    Detecta se o payload de webhook do Evolution GO contém imagem, vídeo ou
    documento (áudio é tratado à parte por `is_audio_message`, que já existia).
    Retorna {"type", "caption", "mimetype", "filename"} ou None.
    """
    msg = data.get("Message") or data.get("message") or {}
    if not isinstance(msg, dict):
        return None
    for key, media_type in (("imageMessage", "image"), ("videoMessage", "video"), ("documentMessage", "document")):
        node = msg.get(key)
        if not isinstance(node, dict):
            continue
        mimetype = node.get("mimetype", "") or node.get("mimeType", "")
        filename = node.get("fileName") or node.get("filename") or f"arquivo.{ext_from_mimetype(mimetype, media_type)}"
        return {
            "type":     media_type,
            "caption":  node.get("caption", ""),
            "mimetype": mimetype,
            "filename": filename,
        }
    return None


def download_inbound_media(api_url: str, api_key: str, data: dict, media_info: dict) -> dict:
    """
    Baixa os bytes reais da mídia recebida (imagem/vídeo/documento/áudio) via
    Evolution GO e guarda num arquivo temporário servível por `/media/temp/{nome}`.
    Retorna {"ok": True, "filename": ..., "size": ...} ou {"ok": False, "detail": ...}.
    """
    raw = download_media_bytes(api_url, api_key, data)
    if not raw:
        return {"ok": False, "detail": "download retornou vazio"}

    max_bytes = max_bytes_for(media_info.get("type", ""))
    if len(raw) > max_bytes:
        logger.warning(f"[Media] mídia recebida excede limite ({len(raw)} > {max_bytes}), descartando bytes, mantendo legenda.")
        return {"ok": False, "detail": "arquivo maior que o limite aceito"}

    ext = ext_from_mimetype(media_info.get("mimetype", ""), media_info.get("type", ""))
    # Mídia recebida fica disponível por mais tempo que a de envio — o humano
    # pode querer abrir a imagem/documento minutos depois de ler a conversa.
    filename = save_temp_file(raw, ext, delete_after=24 * 3600.0)
    return {"ok": True, "filename": filename, "size": len(raw)}


# ── Envio (KLEAD → WhatsApp) ───────────────────────────────────────────────────

def send_media(
    api_url: str,
    api_key: str,
    instance_id: str,
    phone: str,
    raw: bytes,
    mediatype: str,
    mimetype: str,
    filename: str,
    caption: str = "",
) -> tuple[bool, str]:
    """
    Envia imagem, vídeo, documento ou áudio via Evolution GO.

    Testado direto contra a API em produção: `/send/media` exige o campo
    `url` (rejeita com "URL is required" se só vier `media` em base64), e
    `/send/audio` não existe nessa versão do Evolution GO (404 em todos os
    caminhos alternativos testados) — áudio também precisa ir por aqui, com
    `mediatype: "audio"`. Por isso o arquivo é salvo num link temporário
    self-hosted e a URL é passada pro Evolution GO buscar.
    """
    ext = ext_from_mimetype(mimetype, mediatype)
    tmp_filename = save_temp_file(raw, ext, delete_after=300.0)
    media_url = f"{public_media_base()}{signed_media_path(tmp_filename)}"

    endpoint = f"{api_url.rstrip('/')}/send/media"
    payload = {
        "number": phone,
        "mediatype": mediatype,
        "type": mediatype,
        "mimetype": mimetype,
        "url": media_url,
        "caption": caption,
        "fileName": filename,
    }
    headers = {"Content-Type": "application/json", "apikey": api_key}
    try:
        # 180s: observado em produção que o Evolution GO pode levar mais de 1
        # minuto pra buscar o arquivo e entregar no WhatsApp (varia bastante
        # por tamanho de arquivo) — 60s gerava falso-negativo (a mensagem
        # chegava, mas já tínhamos desistido e avisado erro pro usuário).
        r = requests.post(endpoint, json=payload, headers=headers, timeout=180)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Media][{instance_id}] send_media {r.status_code}: {detail}")
        return False, detail
    except requests.RequestException as e:
        logger.warning(f"[Media][{instance_id}] send_media falha: {e}")
        return False, str(e)


def public_media_base() -> str:
    """URL base pela qual o Evolution GO alcança os arquivos temporários deste
    servidor. Mesma rede Docker por padrão — sobrescrever com EVO_MEDIA_BASE
    se o Evolution GO for externo."""
    return (os.getenv("EVO_MEDIA_BASE") or os.getenv("PUBLIC_WEBHOOK_BASE") or "http://klead_backend:8000").rstrip("/")
