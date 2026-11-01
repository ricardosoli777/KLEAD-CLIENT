from unittest.mock import patch

from services.outbound_routing import preferred_outbound_channel, send_outbound_text


def test_preferred_outbound_channel_respects_explicit_evolution_provider():
    inst = {
        "provider": "evolution",
        "api_url": "https://evo.example",
        "api_key": "evo-secret",
        "meta_token": "meta-token",
        "phone_number_id": "1234567890",
    }

    assert preferred_outbound_channel(inst) == "evolution"

    with patch("services.outbound_routing.send_whatsapp_message", return_value=(True, "")) as evo_send, \
         patch("services.outbound_routing.send_whatsapp_message_meta", return_value=(True, "")) as meta_send:
        success, detail, channel = send_outbound_text(inst, "RamonSDR", "5511999990001", "Olá")

    assert success is True
    assert detail == ""
    assert channel == "evolution"
    evo_send.assert_called_once()
    meta_send.assert_not_called()


def test_preferred_outbound_channel_respects_meta_even_with_stale_evolution_credentials():
    inst = {
        "provider": "meta",
        "api_url": "https://evo.example",
        "api_key": "evo-secret",
        "meta_token": "meta-token",
        "phone_number_id": "1234567890",
    }

    assert preferred_outbound_channel(inst) == "meta"

    with patch("services.outbound_routing.meta_oauth_pg.get_instance_assets", return_value=None), \
         patch("services.outbound_routing.send_whatsapp_message", return_value=(True, "")) as evo_send, \
         patch("services.outbound_routing.send_whatsapp_message_meta", return_value=(True, "")) as meta_send:
        success, detail, channel = send_outbound_text(inst, "RamonSDR", "5511999990001", "Olá")

    assert (success, detail, channel) == (True, "", "meta")
    meta_send.assert_called_once_with(
        phone_number_id="1234567890", meta_token="meta-token",
        to="5511999990001", text="Olá",
    )
    evo_send.assert_not_called()


def test_preferred_outbound_channel_falls_back_to_meta_when_evolution_is_missing():
    inst = {
        "provider": "meta",
        "api_url": "",
        "api_key": "",
        "meta_token": "meta-token",
        "phone_number_id": "1234567890",
    }

    assert preferred_outbound_channel(inst) == "meta"

    with patch("services.outbound_routing.meta_oauth_pg.get_instance_assets", return_value=None), \
         patch("services.outbound_routing.send_whatsapp_message", return_value=(True, "")) as evo_send, \
         patch("services.outbound_routing.send_whatsapp_message_meta", return_value=(True, "")) as meta_send:
        success, detail, channel = send_outbound_text(inst, "RamonSDR", "5511999990001", "Olá")

    assert success is True
    assert detail == ""
    assert channel == "meta"
    meta_send.assert_called_once()
    evo_send.assert_not_called()


def test_meta_lead_ads_only_uses_evolution_for_outbound():
    inst = {
        "provider": "meta",
        "api_url": "https://evo.example",
        "api_key": "evo-secret",
        "evo_instance_name": "ramon-evo",
        "meta_token": "oauth-token",
        "phone_number_id": "",
    }

    with patch("services.outbound_routing.meta_oauth_pg.get_instance_assets", return_value={
        "status": "active", "whatsapp_enabled": False, "phone_number_id": "",
    }), patch("services.outbound_routing.send_whatsapp_message", return_value=(True, "")) as evo_send, \
         patch("services.outbound_routing.send_whatsapp_message_meta", return_value=(True, "")) as meta_send:
        result = send_outbound_text(inst, "RamonSDR", "5511999990001", "Olá")

    assert result == (True, "", "evolution")
    evo_send.assert_called_once_with(
        api_url="https://evo.example", api_key="evo-secret",
        instance_id="ramon-evo", phone="5511999990001", text="Olá",
    )
    meta_send.assert_not_called()


def test_meta_without_phone_id_returns_clear_error_without_request():
    inst = {"provider": "meta", "meta_token": "oauth-token", "phone_number_id": ""}

    with patch("services.outbound_routing.meta_oauth_pg.get_instance_assets", return_value=None), \
         patch("services.outbound_routing.send_whatsapp_message_meta") as meta_send:
        result = send_outbound_text(inst, "lead-only", "5511999990001", "Olá")

    assert result == (
        False,
        "WhatsApp Oficial não configurado para envio nesta instância.",
        "meta",
    )
    meta_send.assert_not_called()


def test_meta_outbound_uses_oauth_phone_and_token_before_manual_credentials():
    inst = {"provider": "meta", "meta_token": "manual", "phone_number_id": "manual-phone"}
    assets = {
        "status": "active", "phone_number_id": "oauth-phone", "meta_connection_id": "conn-1",
    }
    connection = {
        "user_email": "owner@test.com", "access_token_encrypted": "encrypted-token",
    }
    with patch("services.outbound_routing.meta_oauth_pg.get_instance_assets", return_value=assets), \
         patch("services.outbound_routing.meta_oauth_pg.get_connection_by_id", return_value=connection), \
         patch("services.outbound_routing.crypto_utils.decrypt_field_for_user", return_value="oauth-token"), \
         patch("services.outbound_routing.send_whatsapp_message_meta", return_value=(True, "")) as meta_send:
        result = send_outbound_text(inst, "oauth-instance", "5511999990001", "Olá")

    assert result == (True, "", "meta")
    meta_send.assert_called_once_with(
        phone_number_id="oauth-phone", meta_token="oauth-token",
        to="5511999990001", text="Olá",
    )


def test_typed_identity_without_supported_destination_is_never_sent():
    inst = {"provider": "meta", "meta_token": "token", "phone_number_id": "phone-id"}
    for target in (
        "bsuid:BR.ABC123",
        "parent_bsuid:PARENT.123",
        "username:WABA-1:lead.exemplo",
    ):
        with patch("services.outbound_routing.send_whatsapp_message_meta") as meta_send:
            result = send_outbound_text(inst, "meta-1", target, "Olá")
        assert result == (
            False,
            "Contato sem destino de saída habilitado nesta versão.",
            "meta",
        )
        meta_send.assert_not_called()
