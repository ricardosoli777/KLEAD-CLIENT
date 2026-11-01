"""
services/guide_service.py — Vídeos/tutoriais da página "Como Usar".

Armazenamento simples em JSON (data/guide_videos.json), mesmo padrão dos
demais stores em arquivo. Escrita é exclusiva do superadmin (validada no
router); leitura é liberada para qualquer usuário logado.
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_FILE = os.path.join(_DATA_DIR, "guide_videos.json")
_lock = threading.Lock()

_IFRAME_SRC_RE = re.compile(r"<iframe[^>]*\ssrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _load() -> list[dict]:
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(videos: list[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)


def normalize_source(raw: str) -> tuple[str, str]:
    """
    Recebe o que o superadmin colou (URL .mp4, URL de player ou o HTML de um
    <iframe> inteiro) e devolve (kind, url).
      - kind "mp4"    -> renderizado com <video controls>
      - kind "iframe" -> renderizado num <iframe> próprio (só a src é salva,
                         nunca o HTML colado — evita injetar markup arbitrário)
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Informe o link do vídeo ou o código do iframe")

    m = _IFRAME_SRC_RE.search(raw)
    if m:
        url = m.group(1).strip()
    else:
        url = raw

    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Link inválido — use uma URL http(s) ou um <iframe> com src")

    kind = "mp4" if url.lower().split("?")[0].endswith(".mp4") else "iframe"
    return kind, url


def list_videos() -> list[dict]:
    with _lock:
        videos = _load()
    videos.sort(key=lambda v: (int(v.get("module") or 1), int(v.get("order") or 1), v.get("created_at") or ""))
    return videos


def add_video(title: str, description: str, source: str, module: int = 1, order: int = 1) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("Informe um título")
    kind, url = normalize_source(source)
    video = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "description": (description or "").strip(),
        "kind": kind,
        "url": url,
        "module": max(1, int(module or 1)),
        "order": max(1, int(order or 1)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        videos = _load()
        videos.append(video)
        _save(videos)
    return video


def update_video(video_id: str, title: str, description: str, source: str, module: int = 1, order: int = 1) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("Informe um título")
    kind, url = normalize_source(source)
    with _lock:
        videos = _load()
        for video in videos:
            if video.get("id") == video_id:
                video.update({
                    "title": title,
                    "description": (description or "").strip(),
                    "kind": kind,
                    "url": url,
                    "module": max(1, int(module or 1)),
                    "order": max(1, int(order or 1)),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                _save(videos)
                return video
    raise KeyError(video_id)


def delete_video(video_id: str) -> bool:
    with _lock:
        videos = _load()
        remaining = [v for v in videos if v.get("id") != video_id]
        if len(remaining) == len(videos):
            return False
        _save(remaining)
        return True
