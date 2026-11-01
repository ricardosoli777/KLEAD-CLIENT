"""
API Key security — compartilhado entre main.py e routers.
"""

import hmac
import os
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


def require_api_key(key: str = Security(api_key_header)):
    secret = os.getenv("DASHBOARD_SECRET", "")
    if not secret:
        raise HTTPException(500, "DASHBOARD_SECRET não configurado no servidor.")
    # compare_digest: comparação em tempo constante — evita canal lateral de timing
    if not hmac.compare_digest(key, secret):
        raise HTTPException(403, "API Key inválida.")
    return key
