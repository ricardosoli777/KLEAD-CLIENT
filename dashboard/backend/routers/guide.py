"""
routers/guide.py — Vídeos da página "Como Usar".

Leitura: qualquer usuário logado. Escrita (criar/editar/apagar): só superadmin.
"""
from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel

from core.security import require_api_key
from services.auth_service import get_auth_user, require_superadmin
import services.guide_service as guide_svc

router = APIRouter(tags=["guide"])


class VideoIn(BaseModel):
    title: str
    description: str = ""
    source: str  # URL .mp4, URL de player ou HTML de <iframe>
    module: int = 1  # número do módulo (agrupamento)
    order: int = 1   # posição do vídeo dentro do módulo


@router.get("/guide/videos", dependencies=[Security(require_api_key)])
def list_videos(request: Request):
    get_auth_user(request)  # exige login
    return {"videos": guide_svc.list_videos()}


@router.post("/guide/videos", dependencies=[Security(require_api_key)])
def create_video(payload: VideoIn, request: Request):
    require_superadmin(request)
    try:
        return guide_svc.add_video(payload.title, payload.description, payload.source, payload.module, payload.order)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/guide/videos/{video_id}", dependencies=[Security(require_api_key)])
def update_video(video_id: str, payload: VideoIn, request: Request):
    require_superadmin(request)
    try:
        return guide_svc.update_video(video_id, payload.title, payload.description, payload.source, payload.module, payload.order)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "Vídeo não encontrado")


@router.delete("/guide/videos/{video_id}", dependencies=[Security(require_api_key)])
def delete_video(video_id: str, request: Request):
    require_superadmin(request)
    if not guide_svc.delete_video(video_id):
        raise HTTPException(404, "Vídeo não encontrado")
    return {"ok": True}
