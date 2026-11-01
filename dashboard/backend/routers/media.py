"""
Router de mídia — /media/*.

Serve os arquivos temporários geridos por services/media_service.py: mídia
recebida do WhatsApp (pra exibir no Inbox) e, se algum fluxo futuro voltar a
precisar de envio por URL, mídia de saída também passa por aqui.

Sem header de autenticação de propósito — o Evolution GO (que pode precisar
buscar esse arquivo) não tem como mandar o header X-API-Key. Em vez disso a
URL é uma capability URL assinada: nome hex(uuid4) + token HMAC (?tk=)
derivado de DASHBOARD_SECRET, verificado aqui.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import services.media_service as media_svc

router = APIRouter(tags=["media"])


@router.get("/media/temp/{filename}")
async def serve_temp_media(filename: str, tk: str = ""):
    if not media_svc.is_valid_temp_filename(filename):
        raise HTTPException(404)
    if not media_svc.check_media_token(filename, tk):
        raise HTTPException(404)
    path = media_svc.temp_file_path(filename)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
