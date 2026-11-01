import base64
import binascii
import logging
import os
import threading
import time
import requests
import secrets

logger = logging.getLogger("klead.evolution")

# Cache do GET /instance/all por servidor Evolution GO. Sem isto, cada consulta
# de status de UMA instância baixa a lista COMPLETA de instâncias do servidor —
# com N instâncias sendo consultadas em paralelo pelo dashboard, o custo vira
# O(N²) por ciclo de polling. Com o cache, é 1 fetch por TTL por servidor.
_INSTANCE_ALL_TTL = float(os.getenv("EVO_INSTANCE_ALL_TTL", "10"))
_instance_all_cache: dict[tuple, tuple[float, list]] = {}
_instance_all_lock = threading.Lock()


def invalidate_instance_cache(api_url: str) -> None:
    """Remove estados cacheados de um servidor após mutações de conexão."""
    base_url = api_url.rstrip("/")
    with _instance_all_lock:
        stale_keys = [key for key in _instance_all_cache if key[0] == base_url]
        for key in stale_keys:
            _instance_all_cache.pop(key, None)


def get_all_instances(api_url: str, global_api_key: str) -> list:
    """
    Retorna a lista de instâncias do Evolution GO (GET /instance/all), com cache
    de _INSTANCE_ALL_TTL segundos por (servidor, chave). Em falha de rede,
    devolve o último resultado bom conhecido (stale) em vez de lista vazia.
    """
    cache_key = (api_url.rstrip("/"), global_api_key)
    now = time.monotonic()
    with _instance_all_lock:
        cached = _instance_all_cache.get(cache_key)
    if cached and now - cached[0] < _INSTANCE_ALL_TTL:
        return cached[1]

    url = f"{api_url.rstrip('/')}/instance/all"
    headers = {"apikey": global_api_key}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            items = data.get("data", []) if isinstance(data, dict) else data
            if isinstance(items, list):
                with _instance_all_lock:
                    _instance_all_cache[cache_key] = (now, items)
                return items
        logger.warning(f"[Evolution GO] /instance/all HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[Evolution GO] /instance/all falhou: {e}")
    return cached[1] if cached else []


def _decode_media_base64(value: str) -> bytes | None:
    """Decodifica base64 bruto ou data URL retornado pelo Evolution GO."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("data:"):
        marker = ";base64,"
        if marker not in value:
            return None
        value = value.split(marker, 1)[1]
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) < 128:
        return None
    return raw


def create_evolution_instance(api_url: str, global_api_key: str, instance_name: str) -> dict:
    """
    Cria uma nova instância no Evolution GO usando a global API key.
    Retorna {"ok": True, "api_key": "<instance_token>", "qr_code": "<base64>"} ou {"ok": False, "detail": "..."}.
    """
    base = api_url.rstrip("/")
    headers = {"apikey": global_api_key, "Content-Type": "application/json"}
    instance_token = secrets.token_hex(16)
    payload = {
        "instanceName": instance_name,
        "name": instance_name,
        "token": instance_token,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    }
    try:
        r = requests.post(f"{base}/instance/create", json=payload, headers=headers, timeout=15)
        if r.status_code not in (200, 201):
            return {"ok": False, "detail": f"HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
        # Extrai token da instância — Evolution GO pode retornar em hash.apikey, token ou apikey
        # Fallback para o token gerado por nós caso a resposta não traga
        token = (
            (data.get("hash") or {}).get("apikey")
            or data.get("token")
            or data.get("apikey")
            or instance_token
        )
        # Busca QR code via endpoint correto usando token da instância.
        # O contrato público é sempre qr_code como string.
        qr_res = _fetch_qr(base, instance_token)
        result = {"ok": True, "api_key": token, "qr_code": qr_res.get("qr", "")}
        if qr_res.get("already_connected"):
            result["already_connected"] = True
        if qr_res.get("error"):
            result["detail"] = qr_res["error"]
        return result
    except requests.RequestException as e:
        return {"ok": False, "detail": str(e)}


def _fetch_qr(base: str, instance_token: str) -> dict:
    """Retorna {"qr": "<base64>"} | {"already_connected": True} | {"error": "..."}"""
    try:
        r = requests.get(f"{base}/instance/qr", headers={"apikey": instance_token}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data") or {}
            # Evolution GO retorna o campo em minusculas ("qrcode"); mantemos as
            # variantes antigas como fallback caso essa versao mude de novo.
            qr = inner.get("qrcode") or inner.get("Qrcode") or data.get("qrcode") or data.get("Qrcode") or ""
            return {"qr": qr}
        if r.status_code == 400:
            try:
                err = r.json().get("error", "")
            except Exception:
                err = r.text
            if "already" in err.lower() or "logged" in err.lower():
                return {"already_connected": True}
            return {"error": err}
    except Exception as e:
        return {"error": str(e)}
    return {"error": f"HTTP {r.status_code}"}


def get_qrcode(api_url: str, instance_api_key: str, instance_name: str) -> dict:
    """
    Busca o QR code atual da instância via GET /instance/qr com token da instância.
    Retorna {"ok": True, "qr_code": "..."} | {"ok": True, "already_connected": True} | {"ok": False, "detail": "..."}.
    """
    base = api_url.rstrip("/")
    result = _fetch_qr(base, instance_api_key)
    if result.get("already_connected"):
        return {"ok": True, "qr_code": "", "already_connected": True}
    return {"ok": True, "qr_code": result.get("qr", "")}


def check_connection_state(api_url: str, api_key: str) -> str:
    """
    Verifica se o servidor Evolution GO está acessível.
    Retorna 'open' se o servidor responde (qualquer HTTP), 'close' se não alcança.
    A Evolution GO em porta 4000 não expõe endpoint de status do WhatsApp.
    """
    base = api_url.rstrip("/")
    headers = {"apikey": api_key}
    # Tenta o endpoint de send (único conhecido) com GET — só pra ver se o servidor responde
    for path in ("/send/text", "/"):
        try:
            r = requests.get(f"{base}{path}", headers=headers, timeout=4)
            # Qualquer resposta HTTP (mesmo 404/405) = servidor está de pé
            return "open"
        except requests.ConnectionError:
            return "close"
        except requests.Timeout:
            return "close"
        except requests.RequestException:
            continue
    return "close"



def send_whatsapp_message(
    api_url: str,
    api_key: str,
    instance_id: str,
    phone: str,
    text: str,
) -> tuple[bool, str]:
    """
    Envia mensagem via Evolution GO API.
    Retorna (sucesso, mensagem_de_erro).
    """
    url = f"{api_url.rstrip('/')}/send/text"
    payload = {"number": phone, "text": text}
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key,
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Evolution GO][{instance_id}] {r.status_code}: {detail}")
        return False, detail
    except requests.RequestException as e:
        logger.warning(f"[Evolution GO][{instance_id}] Falha de conexão: {e}")
        return False, str(e)


def send_whatsapp_poll(
    api_url: str,
    api_key: str,
    instance_id: str,
    phone: str,
    question: str,
    options: list[str],
    max_answers: int = 1,
) -> tuple[bool, str, str]:
    """Envia enquete; o padrão mantém as confirmações legadas com escolha única."""
    clean_question = str(question or "").strip()
    clean_options = [str(option or "").strip() for option in options]
    if (not clean_question or len(clean_question) > 255 or not 1 <= len(clean_options) <= 12
            or any(not option or len(option) > 100 for option in clean_options)
            or not 1 <= max_answers <= len(clean_options)):
        return False, "Pergunta, opções ou limite de respostas inválidos.", ""
    url = f"{api_url.rstrip('/')}/send/poll"
    payload = {
        "number": phone,
        "question": clean_question,
        "options": clean_options,
        "maxAnswer": max_answers,
    }
    headers = {"Content-Type": "application/json", "apikey": api_key}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        if response.status_code not in (200, 201):
            detail = response.text[:200] or f"HTTP {response.status_code}"
            logger.warning(f"[Evolution GO][{instance_id}] poll {response.status_code}: {detail}")
            return False, detail, ""
        data = response.json()
        message_id = str((((data.get("data") or {}).get("Info") or {}).get("ID") or ""))
        return True, "", message_id
    except (requests.RequestException, ValueError) as exc:
        logger.warning(f"[Evolution GO][{instance_id}] Falha ao enviar enquete: {exc}")
        return False, str(exc), ""


def download_media_bytes(api_url: str, api_key: str, message_data: dict) -> bytes | None:
    """
    Baixa mídia (áudio, imagem, etc.) do Evolution GO.
    Retorna bytes brutos ou None em caso de falha.

    Endpoint confirmado via log de debug do próprio Evolution GO
    (`[GIN-debug] POST /message/downloadmedia`) — a rota é case-sensitive
    e minúscula; `/chat/getBase64FromMediaMessage` (usado antes) nunca
    existiu nessa versão (sempre deu 404).
    """
    url = f"{api_url.rstrip('/')}/message/downloadmedia"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    # message_data vem do webhook como {"Info": {...}, "Message": {...}} —
    # o endpoint quer só o nó da mensagem em si (onde fica audioMessage/
    # imageMessage/etc), não o envelope inteiro com Info junto.
    inner_message = message_data.get("Message") or message_data.get("message") or message_data
    payload = {"message": inner_message}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            nested = data.get("data") if isinstance(data.get("data"), dict) else {}
            b64 = (
                nested.get("base64")
                or data.get("base64")
                or data.get("media")
                or data.get("image")
                or ""
            )
            if b64:
                raw = _decode_media_base64(b64)
                if raw:
                    return raw
                logger.warning("[Evolution GO] download_media: campo base64 invalido ou curto demais")
            logger.warning(f"[Evolution GO] download_media: resposta 200 sem mídia reconhecida: {list(data.keys())}")
        else:
            # Documentação dessa versão do Evolution GO é escassa/inconsistente —
            # loga a estrutura real da mensagem pra conseguir ajustar o payload
            # com dado de verdade em vez de ficar chutando contra a doc.
            logger.warning(
                f"[Evolution GO] download_media {r.status_code}: {r.text[:200]} "
                f"| payload.message keys={list(inner_message.keys()) if isinstance(inner_message, dict) else type(inner_message)}"
            )
    except Exception as e:
        logger.warning(f"[Evolution GO] download_media falhou: {e}")
    return None


def _get_instance_uuid(api_url: str, global_api_key: str, instance_name: str) -> str:
    """Retorna o UUID da instância buscando via /instance/all."""
    data = get_instance_info(api_url, global_api_key, instance_name)
    return data.get("id", "")


def get_whatsapp_settings(api_url: str, global_api_key: str, instance_name: str) -> dict:
    """
    Retorna as configurações avançadas da instância.
    /instance/all já retorna os campos — extraímos diretamente.
    """
    data = get_instance_info(api_url, global_api_key, instance_name)
    if not data:
        return {}
    return {
        "alwaysOnline":    data.get("alwaysOnline", False),
        "rejectCall":      data.get("rejectCall", False),
        "msgCall":         data.get("msgRejectCall", ""),
        "readMessages":    data.get("readMessages", False),
        "readStatus":      data.get("ignoreStatus", False),
        "groupsIgnore":    data.get("ignoreGroups", False),
        "syncFullHistory": data.get("syncFullHistory", False),
    }


def set_whatsapp_settings(api_url: str, instance_api_key: str, instance_name: str, settings: dict, global_api_key: str = "") -> tuple[bool, str]:
    """
    Atualiza as configurações avançadas da instância no Evolution GO.
    O endpoint /advanced-settings exige UUID (não nome) e token da instância.
    """
    uuid = _get_instance_uuid(api_url, global_api_key or instance_api_key, instance_name)
    if not uuid:
        return False, f"Instância '{instance_name}' não encontrada no Evolution GO"
    url = f"{api_url.rstrip('/')}/instance/{uuid}/advanced-settings"
    headers = {"apikey": instance_api_key, "Content-Type": "application/json"}
    payload = {
        "alwaysOnline":    settings.get("alwaysOnline", False),
        "rejectCall":      settings.get("rejectCall", False),
        "msgRejectCall":   settings.get("msgCall", ""),
        "readMessages":    settings.get("readMessages", False),
        "ignoreStatus":    settings.get("readStatus", False),
        "ignoreGroups":    settings.get("groupsIgnore", False),
        "syncFullHistory": settings.get("syncFullHistory", False),
    }
    try:
        r = requests.put(url, json=payload, headers=headers, timeout=10)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Evolution GO] set_whatsapp_settings {r.status_code}: {detail}")
        return False, detail
    except Exception as e:
        logger.warning(f"[Evolution GO] set_whatsapp_settings falhou: {e}")
        return False, str(e)


def get_instance_info(api_url: str, global_api_key: str, instance_name: str) -> dict:
    """
    Retorna dados completos da instância filtrando pelo nome na lista cacheada
    de /instance/all. Inclui: connected, jid, name, alwaysOnline, rejectCall, etc.
    """
    for inst in get_all_instances(api_url, global_api_key):
        if inst.get("name", "").lower() == instance_name.lower():
            return inst
    return {}


def delete_evolution_instance(api_url: str, global_api_key: str, instance_name: str) -> tuple[bool, str]:
    """
    Remove a instância do Evolution GO usando o UUID interno.
    Usa GLOBAL_API_KEY pois é rota administrativa.
    """
    uuid = _get_instance_uuid(api_url, global_api_key, instance_name)
    if not uuid:
        return False, f"Instância '{instance_name}' não encontrada no Evolution GO"
    url = f"{api_url.rstrip('/')}/instance/delete/{uuid}"
    headers = {"apikey": global_api_key, "Content-Type": "application/json"}
    try:
        r = requests.delete(url, headers=headers, timeout=10)
        if r.status_code in (200, 201, 204):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Evolution GO] delete_evolution_instance {r.status_code}: {detail}")
        return False, detail
    except Exception as e:
        logger.warning(f"[Evolution GO] delete_evolution_instance falhou: {e}")
        return False, str(e)


def disconnect_instance(api_url: str, api_key: str, instance_id: str) -> tuple[bool, str]:
    """
    Desconecta a instância do WhatsApp sem deletar.
    Usa token da instância.
    """
    url = f"{api_url.rstrip('/')}/instance/disconnect"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json={}, headers=headers, timeout=10)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Evolution GO][{instance_id}] disconnect {r.status_code}: {detail}")
        return False, detail
    except Exception as e:
        logger.warning(f"[Evolution GO][{instance_id}] disconnect falhou: {e}")
        return False, str(e)


def reconnect_instance(api_url: str, api_key: str, instance_id: str) -> tuple[bool, str]:
    """
    Reconecta uma sessão de WhatsApp já pareada, sem gerar QR Code novo.
    Só funciona se a sessão ainda existir no Evolution GO (não foi deslogada
    de verdade) — nesse caso, é preciso usar o QR Code novo.
    Usa token da instância.
    """
    url = f"{api_url.rstrip('/')}/instance/reconnect"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json={}, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Evolution GO][{instance_id}] reconnect {r.status_code}: {detail}")
        return False, detail
    except Exception as e:
        logger.warning(f"[Evolution GO][{instance_id}] reconnect falhou: {e}")
        return False, str(e)


def update_webhook_events(api_url: str, api_key: str, instance_name: str, webhook_url: str, events: list) -> tuple[bool, str]:
    """
    Atualiza a URL e os eventos do webhook no Evolution GO.
    Usa token da instância.
    """
    url = f"{api_url.rstrip('/')}/webhook/set/{instance_name}"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": True,
            "webhookBase64": False,
            "events": events,
        }
    }
    try:
        r = requests.put(url, json=payload, headers=headers, timeout=10)
        if r.status_code in (200, 201):
            return True, ""
        detail = r.text[:200] or f"HTTP {r.status_code}"
        logger.warning(f"[Evolution GO] update_webhook_events {r.status_code}: {detail}")
        return False, detail
    except Exception as e:
        logger.warning(f"[Evolution GO] update_webhook_events falhou: {e}")
        return False, str(e)
