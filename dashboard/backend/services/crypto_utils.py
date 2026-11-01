"""
Criptografia de credenciais por usuário, utilizável sem contexto de request
(ex: dentro de threads de background do CAPI, que não têm acesso a
request.app.state). Mesma lógica de main.py._make_fernet_for_user, duplicada
aqui deliberadamente para não criar dependência de main.py em módulos de
serviço — main.py continua sendo a única fonte para a criptografia por
instância (_make_fernet_for_instance), que sempre roda em contexto de request.
"""
import base64
import os


def make_fernet_for_user(email: str):
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        secret = os.getenv("DASHBOARD_SECRET", "")
        if not secret or not email:
            return None
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=email.encode(),
            info=b"klead-meta-oauth-credentials",
        ).derive(secret.encode())
        return Fernet(base64.urlsafe_b64encode(derived))
    except ImportError:
        return None


def encrypt_field(fernet, value: str) -> str:
    if not value:
        return value
    # Fail-closed (mesma regra de main.py._encrypt_field): sem Fernet, recusar
    # a gravação em vez de persistir o segredo em texto claro silenciosamente.
    if not fernet:
        raise RuntimeError(
            "Criptografia indisponível (cryptography/DASHBOARD_SECRET) — "
            "recusando gravar credencial em texto claro."
        )
    return fernet.encrypt(value.encode()).decode()


def decrypt_field_for_user(email: str, value: str) -> str:
    if not value:
        return value
    fernet = make_fernet_for_user(email)
    if not fernet:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        return value
