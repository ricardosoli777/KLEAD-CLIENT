from services.evolution_service import send_whatsapp_message, send_whatsapp_poll
from services.meta_service import send_whatsapp_message_meta
import services.crypto_utils as crypto_utils
import services.meta_oauth_pg_service as meta_oauth_pg


def has_evolution_outbound(inst: dict) -> bool:
    """Retorna True quando a instância tem credenciais Evolution válidas para saída."""
    return bool((inst.get("api_url") or "").strip() and (inst.get("api_key") or "").strip())


def resolve_meta_outbound_credentials(inst: dict, instance_id: str = "") -> tuple[str, str, str]:
    """Resolve (phone_number_id, token, fonte) para WhatsApp Cloud API.

    Uma seleção OAuth de WABA/número tem prioridade porque representa a
    escolha mais recente do usuário. A configuração manual permanece como
    fallback para instâncias existentes e tokens de Usuário do Sistema.
    """
    iid = (instance_id or inst.get("id") or inst.get("instance_id") or "").strip()
    if iid:
        assets = meta_oauth_pg.get_instance_assets(iid)
        if assets and assets.get("status") == "active" and assets.get("phone_number_id"):
            connection = meta_oauth_pg.get_connection_by_id(assets.get("meta_connection_id", ""))
            if connection:
                token = crypto_utils.decrypt_field_for_user(
                    connection.get("user_email", ""), connection.get("access_token_encrypted", "")
                )
                if token:
                    return assets["phone_number_id"], token, "oauth"
    return (
        (inst.get("phone_number_id") or "").strip(),
        (inst.get("meta_token") or "").strip(),
        "manual",
    )


def has_meta_outbound(inst: dict, instance_id: str = "") -> bool:
    """Retorna True quando há número e token Meta efetivos (OAuth ou manual)."""
    phone_number_id, token, _source = resolve_meta_outbound_credentials(inst, instance_id)
    return bool(phone_number_id and token)


def preferred_outbound_channel(inst: dict, instance_id: str = "") -> str:
    """
    Respeita o provedor escolhido explicitamente. Para registros legados sem
    provider válido, preserva o fallback histórico Evolution → Meta.
    """
    provider = (inst.get("provider") or "").strip().lower()
    if provider == "evolution":
        return "evolution"
    if provider == "meta":
        # Uma instância pode usar a integração Meta somente para Lead Ads/CAPI
        # e continuar enviando mensagens pelo Evolution. Nesse modo não há
        # phone_number_id da Cloud API, portanto não se deve forçar Meta.
        if has_meta_outbound(inst, instance_id):
            return "meta"
        if has_evolution_outbound(inst):
            return "evolution"
        return "meta"
    if has_evolution_outbound(inst):
        return "evolution"
    if has_meta_outbound(inst):
        return "meta"
    return "evolution"


def send_outbound_text(inst: dict, instance_id: str, phone: str, text: str) -> tuple[bool, str, str]:
    """
    Envia texto pelo canal de saída preferencial da instância.
    Retorna (sucesso, detalhe_erro, canal_usado).
    """
    channel = preferred_outbound_channel(inst, instance_id)
    target = str(phone or "").strip().lower()
    if target.startswith(("bsuid:", "parent_bsuid:", "username:")):
        return False, "Contato sem destino de saída habilitado nesta versão.", channel
    if channel == "meta":
        phone_number_id, meta_token, _source = resolve_meta_outbound_credentials(inst, instance_id)
        if not phone_number_id or not meta_token:
            return False, "WhatsApp Oficial não configurado para envio nesta instância.", channel
        success, detail = send_whatsapp_message_meta(
            phone_number_id=phone_number_id,
            meta_token=meta_token,
            to=phone,
            text=text,
            # Sem template_name: respostas SDR/inbox usam texto livre (janela 24h).
            # Templates são usados apenas por campaign_service para mensagens proativas.
        )
    else:
        if not has_evolution_outbound(inst):
            return False, "Evolution GO não configurado para envio nesta instância.", channel
        success, detail = send_whatsapp_message(
            api_url=inst.get("api_url", ""),
            api_key=inst.get("api_key", ""),
            instance_id=inst.get("evo_instance_name") or instance_id,
            phone=phone,
            text=text,
        )
    return success, detail, channel


def can_send_outbound_audio(inst: dict) -> bool:
    """Áudio TTS só faz sentido se houver Evolution disponível para saída."""
    return has_evolution_outbound(inst)


def send_outbound_poll(inst: dict, instance_id: str, phone: str, question: str,
                       options: list[str], max_answers: int = 1) -> tuple[bool, str, str, str]:
    """Enquete é suportada somente pelo Evolution GO nesta fase."""
    channel = preferred_outbound_channel(inst, instance_id)
    if channel != "evolution" or not has_evolution_outbound(inst):
        return False, "Enquete individual requer uma instância Evolution GO.", channel, ""
    if str(phone or "").strip().lower().startswith(("bsuid:", "parent_bsuid:", "username:")):
        return False, "Contato sem destino de saída habilitado nesta versão.", channel, ""
    ok, detail, message_id = send_whatsapp_poll(
        api_url=inst.get("api_url", ""), api_key=inst.get("api_key", ""),
        instance_id=inst.get("evo_instance_name") or instance_id,
        phone=phone, question=question, options=options, max_answers=max_answers,
    )
    return ok, detail, channel, message_id
