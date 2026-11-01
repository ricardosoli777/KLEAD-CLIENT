import re
import requests

META_API_VERSION = "v25.0"
META_BASE_URL    = f"https://graph.facebook.com/{META_API_VERSION}"


def _to_e164(phone: str) -> str:
    """Garante formato E.164 (+<dígitos>) exigido pela Meta Cloud API.
    Se já começar com +, retorna como está.
    Caso contrário, remove não-dígitos e adiciona +.
    """
    p = (phone or "").strip()
    if not p or p.startswith("+"):
        return p
    digits = re.sub(r"\D", "", p)
    return f"+{digits}" if digits else p


def check_meta_connection(phone_number_id: str, meta_token: str) -> dict:
    """Verifica se as credenciais Meta Cloud API são válidas."""
    if not phone_number_id or not meta_token:
        return {
            "state": "close",
            "connected": False,
            "detail": "phone_number_id e meta_token são obrigatórios.",
            "status_code": 400,
        }

    url     = f"{META_BASE_URL}/{phone_number_id}"
    headers = {"Authorization": f"Bearer {meta_token}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return {
                "state": "open",
                "connected": True,
                "detail": "",
                "status_code": 200,
            }

        detail = r.text[:500] or f"HTTP {r.status_code}"
        return {
            "state": "close",
            "connected": False,
            "detail": detail,
            "status_code": r.status_code,
        }
    except requests.RequestException:
        return {
            "state": "close",
            "connected": False,
            "detail": "Erro de conexão ao validar o token da Meta.",
            "status_code": 0,
        }


def send_whatsapp_message_meta(
    phone_number_id: str,
    meta_token: str,
    text: str,
    to: str = "",
    recipient: str = "",
    template_name: str = "",
    template_lang: str = "pt_BR",
) -> tuple[bool, str]:
    """
    Envia mensagem via Meta Cloud API.
    - Com template_name: envia template aprovado (campanha proativa).
    - Sem template_name: envia texto livre (só funciona dentro da janela de 24h).
    """
    url     = f"{META_BASE_URL}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {meta_token}",
        "Content-Type":  "application/json",
    }

    destination_key = "recipient" if recipient else "to"
    destination_value = _to_e164(recipient or to)
    if not destination_value:
        return False, "missing_recipient"

    if template_name:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            destination_key: destination_value,
            "type": "template",
            "template": {
                "name":     template_name,
                "language": {"code": template_lang},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": text}],
                    }
                ],
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            destination_key: destination_value,
            "type": "text",
            "text": {"body": text},
        }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        return False, detail
    except requests.RequestException as e:
        return False, str(e)


def send_whatsapp_media_meta(
    phone_number_id: str,
    meta_token: str,
    to: str,
    raw_bytes: bytes,
    mimetype: str,
    filename: str,
    caption: str = "",
) -> tuple[bool, str]:
    """
    Envia imagem ou documento via Meta Cloud API.
    1. Upload dos bytes → obtém media_id
    2. Envia mensagem com media_id
    """
    if mimetype.startswith("image/"):
        media_type = "image"
    elif "pdf" in mimetype or "document" in mimetype or "sheet" in mimetype or "presentation" in mimetype:
        media_type = "document"
    elif mimetype.startswith("audio/"):
        media_type = "audio"
    else:
        return False, f"Tipo não suportado pela Meta API: {mimetype}"

    headers_auth = {"Authorization": f"Bearer {meta_token}"}

    # Passo 1: upload para obter media_id
    try:
        up = requests.post(
            f"{META_BASE_URL}/{phone_number_id}/media",
            headers=headers_auth,
            files={"file": (filename, raw_bytes, mimetype)},
            data={"messaging_product": "whatsapp"},
            timeout=60,
        )
        if up.status_code not in (200, 201):
            return False, f"Upload de mídia falhou: {up.text[:200]}"
        media_id = up.json().get("id", "")
        if not media_id:
            return False, "Meta não retornou media_id após upload"
    except requests.RequestException as e:
        return False, str(e)

    # Passo 2: envia mensagem com media_id
    media_block: dict = {"id": media_id}
    if caption and media_type in ("image", "document"):
        media_block["caption"] = caption
    if media_type == "document":
        media_block["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _to_e164(to),
        "type": media_type,
        media_type: media_block,
    }
    try:
        r = requests.post(
            f"{META_BASE_URL}/{phone_number_id}/messages",
            headers={**headers_auth, "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if r.status_code in (200, 201):
            return True, ""
        return False, r.text[:200]
    except requests.RequestException as e:
        return False, str(e)
