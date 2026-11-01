"""
Rate limiter compartilhado — importado por main.py e pelos routers.
Usa email do JWT como chave quando autenticado; fallback para IP.
"""

import logging
import services.auth_service as auth_svc
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_key(request: Request) -> str:
    try:
        token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
        if token:
            user = auth_svc.verify_token(token)
            if user and user.get("email"):
                return f"user:{user['email']}"
    except Exception:
        logging.getLogger("klead").debug("Falha no rate limiter", exc_info=True)
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_key, default_limits=["300/minute"])
