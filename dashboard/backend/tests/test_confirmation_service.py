import pytest
from unittest.mock import MagicMock, patch

from services.confirmation_service import (
    default_templates,
    emphasize_whatsapp_details,
    match_reply,
    normalize_options,
    render_message,
    validate_message_text,
)
from services import confirmation_pg_service
from services.confirmation_pg_service import generate_public_token
from routers import crm as crm_router


def test_five_editable_default_templates_are_available():
    templates = default_templates()
    assert len(templates) == 5
    assert templates[0]["name"] == "Confirmação de atendimento"
    assert [item["id"] for item in templates[0]["options"]] == ["confirm", "reschedule", "wait"]
    assert templates[0]["options"][2]["label"] == "RESPONDER DEPOIS"
    assert "Precisamos confirmar" in templates[0]["message_text"]
    assert templates[0]["options"][0]["auto_reply"] == "✅ Visita confirmada com sucesso."


def test_render_message_interpolates_lead_and_keeps_link_optional():
    template = default_templates()[0]
    rendered = render_message(
        template["message_text"],
        template["options"],
        {"nome": "João", "data": "12/08", "hora": "14h"},
    )
    assert "Olá, João!" in rendered
    assert "✅ CONFIRMAR" in rendered
    assert "http" not in rendered

    with_link = render_message(
        template["message_text"],
        template["options"],
        {"nome": "João", "data": "12/08", "hora": "14h"},
        "https://klead.example/c/token",
    )
    assert with_link.endswith("https://klead.example/c/token")


def test_reply_matching_accepts_label_and_synonym_but_not_sentence():
    options = default_templates()[0]["options"]
    assert match_reply(" confirmar ", options) == "confirm"
    assert match_reply("REAGENDAR", options) == "reschedule"
    assert match_reply("ESPERAR", options) == "wait"
    assert match_reply("quero confirmar amanhã", options) is None


def test_option_and_variable_validation():
    with pytest.raises(ValueError):
        normalize_options([])
    with pytest.raises(ValueError):
        normalize_options([
            {"id": "yes", "label": "Sim"},
            {"id": "other", "label": "sim"},
        ])
    with pytest.raises(ValueError):
        validate_message_text("Olá, {telefone}")


def test_normalized_options_keep_editable_auto_reply():
    result = normalize_options([{"id": "confirm", "label": "Confirmar", "auto_reply": "Confirmado!"}])
    assert result[0]["auto_reply"] == "Confirmado!"


def test_public_tokens_are_url_safe_and_unique():
    first = generate_public_token()
    second = generate_public_token()
    assert first != second
    assert len(first) >= 40
    assert all(char.isalnum() or char in "-_" for char in first)


def test_confirmation_schema_does_not_cascade_during_crm_snapshot():
    schema = confirmation_pg_service.SCHEMA_SQL
    assert "lead_id uuid NOT NULL REFERENCES crm_leads" not in schema
    assert "DROP CONSTRAINT IF EXISTS crm_confirmations_lead_id_fkey" in schema


def test_create_confirmation_supersedes_previous_pending_before_insert():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(confirmation_pg_service.crm_pg_service, "_connect", return_value=conn), \
         patch.object(confirmation_pg_service, "get_confirmation", return_value={"id": "new"}):
        result = confirmation_pg_service.create_confirmation(
            owner_key="tenant", lead_id="00000000-0000-0000-0000-000000000001",
            instancia_id="inst", conversation_key="5511999999999", provider="evolution",
            message_text="Olá", rendered_text="Olá", options=[{"id": "confirm", "label": "Confirmar"}],
            created_by="user@example.com",
        )
    sql_calls = [call.args[0] for call in cur.execute.call_args_list]
    supersede_index = next(i for i, sql in enumerate(sql_calls) if "status='superseded'" in sql)
    insert_index = next(i for i, sql in enumerate(sql_calls) if "INSERT INTO crm_confirmations" in sql)
    assert supersede_index < insert_index
    assert result == {"id": "new"}


def test_delete_for_lead_is_explicit_and_scoped():
    conn = MagicMock()
    cur = MagicMock(rowcount=2)
    conn.cursor.return_value = cur
    with patch.object(confirmation_pg_service.crm_pg_service, "pg_configured", return_value=True), \
         patch.object(confirmation_pg_service.crm_pg_service, "_connect", return_value=conn):
        deleted = confirmation_pg_service.delete_for_lead("00000000-0000-0000-0000-000000000001")
    assert deleted == 2
    cur.execute.assert_called_once_with(
        "DELETE FROM crm_confirmations WHERE lead_id=%s",
        ("00000000-0000-0000-0000-000000000001",),
    )


def test_poll_reply_is_not_resolved_as_text_automation():
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    conn.cursor.return_value = cur
    with patch.object(confirmation_pg_service.crm_pg_service, "pg_configured", return_value=True), \
         patch.object(confirmation_pg_service.crm_pg_service, "_connect", return_value=conn):
        assert confirmation_pg_service.resolve_text_reply("inst", "5511999999999", "CONFIRMAR") is None
    query = cur.execute.call_args.args[0]
    assert "delivery_format='text'" in query


def test_whatsapp_details_are_bold_without_duplicate_markers():
    text = "Olá, Ricardo Oliveira! Visita em 25/08/2026 às 14:00."
    formatted = emphasize_whatsapp_details(text, "Ricardo Oliveira")
    assert formatted == "Olá, *Ricardo Oliveira*! Visita em *25/08/2026* às *14:00*."
    assert emphasize_whatsapp_details(formatted, "Ricardo Oliveira") == formatted


def test_whatsapp_details_support_short_name_brazilian_dates_and_hour_formats():
    text = "Olá, Ricardo! Visita em 25 de agosto de 2026, às 14h30. Retorno 26/08 às 9h."
    formatted = emphasize_whatsapp_details(text, "Ricardo Oliveira dos Santos")
    assert formatted == (
        "Olá, *Ricardo*! Visita em *25 de agosto de 2026*, às *14h30*. "
        "Retorno *26/08* às *9h*."
    )
    assert emphasize_whatsapp_details(formatted, "Ricardo Oliveira dos Santos") == formatted


def test_text_confirmation_always_returns_empty_provider_message_id():
    with patch.object(crm_router, "send_outbound_text", return_value=(True, "", "evolution")):
        result = crm_router._send_confirmation_outbound(
            {}, "inst", "5511999999999", "Olá", [], "text"
        )
    assert result == (True, "", "evolution", "")


def test_poll_confirmation_returns_poll_provider_message_id_after_intro():
    options = [{"label": "CONFIRMAR"}, {"label": "REMARCAR"}]
    with patch.object(crm_router, "send_outbound_text", return_value=(True, "", "evolution")) as intro, \
         patch.object(crm_router, "send_outbound_poll", return_value=(True, "", "evolution", "poll-123")) as poll:
        result = crm_router._send_confirmation_outbound(
            {}, "inst", "5511999999999", "Olá, *Ricardo*", options, "poll"
        )
    assert result == (True, "", "evolution", "poll-123")
    intro.assert_called_once()
    poll.assert_called_once()
