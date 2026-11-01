from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from threading import RLock

import pytest
from fastapi import HTTPException

from routers import inbox_polls
from services.inbox_poll_pg_service import validate_template
from services.inbox_poll_results_service import fetch_results, parse_votes
import hashlib


def test_poll_template_validation_preserves_option_order_and_rejects_invalid_values():
    assert validate_template(" Modelo ", " Pergunta? ", [" Primeiro ", "Segundo"]) == (
        "Modelo", "Pergunta?", ["Primeiro", "Segundo"],
    )
    for options in (["Só uma"], ["A", ""], ["A", " a "], ["A"] * 13):
        with pytest.raises(ValueError):
            validate_template("Nome", "Pergunta?", options)


def _request():
    conv = {"messages": [], "ai_paused": False, "unread": True}
    state = SimpleNamespace(
        instances={"inst": {"provider": "evolution", "api_url": "https://evo", "api_key": "key"}},
        instances_lock=RLock(), conversations={"inst": {"5511999999999": conv}},
        conversations_lock=RLock(), resolve_conversation_key=lambda _inst, phone: phone,
        clear_needs_human=Mock(), now_iso=lambda: "2026-09-27T12:00:00+00:00",
        save_conversation=Mock(), sse_emit=Mock(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state)), conv


def test_inbox_poll_send_saves_snapshot_and_uses_multiple_answers(monkeypatch):
    request, conv = _request()
    template_id = uuid4()
    request_id = uuid4()
    send_id = str(uuid4())
    template = {
        "id": str(template_id), "owner_key": "owner@test.com", "name": "Original",
        "question": "Pergunta original?", "options_json": ["A", "B", "C"],
        "allow_multiple_answers": True,
    }
    send = {
        "id": send_id, "owner_key": "owner@test.com", "instance_id": "inst",
        "conversation_key": "5511999999999", "question_snapshot": template["question"],
        "options_snapshot_json": list(template["options_json"]), "allow_multiple_snapshot": True,
        "provider_message_id": "", "status": "sending",
    }
    batch = {
        "id": str(request_id), "status": "sending", "allow_multiple_snapshot": True,
        "questions_snapshot_json": [{"question": template["question"], "options": template["options_json"]}],
    }
    monkeypatch.setattr(inbox_polls, "require_instance_access", lambda *_: None)
    monkeypatch.setattr(inbox_polls, "_tenant", lambda _: ("owner@test.com", "agent@test.com"))
    monkeypatch.setattr(inbox_polls, "_ready", lambda: None)
    monkeypatch.setattr(inbox_polls.polls, "get_template", lambda *_: template)
    monkeypatch.setattr(inbox_polls.polls, "create_batch", lambda *_: (batch, True))
    monkeypatch.setattr(inbox_polls.polls, "create_send", lambda *_args, **_kwargs: (send, True))
    monkeypatch.setattr(inbox_polls.polls, "mark_send", lambda *_args, **_kwargs: {
        **send, "status": "sent", "provider_message_id": "poll-123",
    })
    outbound = Mock(return_value=(True, "", "evolution", "poll-123"))
    monkeypatch.setattr(inbox_polls, "send_outbound_poll", outbound)
    monkeypatch.setattr(inbox_polls.polls, "mark_batch", Mock())

    result = inbox_polls.send_inbox_poll(
        "inst", "5511999999999", inbox_polls.PollSendPayload(template_id=template_id, request_id=request_id), request,
    )

    assert result["ok"] is True
    assert len(result["sends"]) == 1
    assert outbound.call_args.kwargs["max_answers"] == 3
    assert conv["messages"][0]["poll_options"] == ["A", "B", "C"]
    assert conv["messages"][0]["provider_message_id"] == "poll-123"
    assert conv["ai_paused"] is True
    request.app.state.save_conversation.assert_called_once()


def test_inbox_poll_does_not_send_on_meta_channel(monkeypatch):
    request, _ = _request()
    request.app.state.instances["inst"] = {"provider": "meta", "phone_number_id": "123", "meta_token": "key"}
    monkeypatch.setattr(inbox_polls, "require_instance_access", lambda *_: None)
    monkeypatch.setattr(inbox_polls, "_tenant", lambda _: ("owner@test.com", "agent@test.com"))
    monkeypatch.setattr(inbox_polls, "_ready", lambda: None)
    monkeypatch.setattr(inbox_polls, "preferred_outbound_channel", lambda *_: "meta")
    outbound = Mock()
    monkeypatch.setattr(inbox_polls, "send_outbound_poll", outbound)

    with pytest.raises(HTTPException) as exc:
        inbox_polls.send_inbox_poll(
            "inst", "5511999999999", inbox_polls.PollSendPayload(template_id=uuid4(), request_id=uuid4()), request,
        )

    assert exc.value.status_code == 400
    outbound.assert_not_called()


def test_retry_recovers_history_without_resending_poll(monkeypatch):
    request, conv = _request()
    template_id = uuid4()
    request_id = uuid4()
    sent = {
        "id": str(uuid4()), "owner_key": "owner@test.com", "instance_id": "inst",
        "template_id": str(template_id),
        "batch_id": str(request_id), "question_index": 1,
        "conversation_key": "5511999999999", "question_snapshot": "Pergunta original?",
        "options_snapshot_json": ["A", "B"], "allow_multiple_snapshot": False,
        "provider_message_id": "poll-123", "status": "sent",
    }
    batch = {
        "id": str(request_id), "status": "sent", "allow_multiple_snapshot": False,
        "questions_snapshot_json": [{"question": sent["question_snapshot"], "options": sent["options_snapshot_json"]}],
    }
    monkeypatch.setattr(inbox_polls, "require_instance_access", lambda *_: None)
    monkeypatch.setattr(inbox_polls, "_tenant", lambda _: ("owner@test.com", "agent@test.com"))
    monkeypatch.setattr(inbox_polls, "_ready", lambda: None)
    monkeypatch.setattr(inbox_polls.polls, "get_template", lambda *_: {
        "id": str(template_id), "name": "Pergunta original?",
        "question": "Pergunta original?", "options_json": ["A", "B"],
    })
    monkeypatch.setattr(inbox_polls.polls, "create_batch", lambda *_: (batch, False))
    monkeypatch.setattr(inbox_polls.polls, "has_legacy_batch_sends", lambda *_: False)
    monkeypatch.setattr(inbox_polls.polls, "create_send", lambda *_args, **_kwargs: (sent, False))
    monkeypatch.setattr(inbox_polls.polls, "mark_batch", Mock())
    outbound = Mock()
    monkeypatch.setattr(inbox_polls, "send_outbound_poll", outbound)

    payload = inbox_polls.PollSendPayload(template_id=template_id, request_id=request_id)
    first = inbox_polls.send_inbox_poll("inst", "5511999999999", payload, request)
    second = inbox_polls.send_inbox_poll("inst", "5511999999999", payload, request)

    assert first["already_sent"] is True
    assert second["already_sent"] is True
    assert len(conv["messages"]) == 1
    request.app.state.save_conversation.assert_called_once()
    outbound.assert_not_called()

    monkeypatch.setattr(inbox_polls.polls, "has_legacy_batch_sends", lambda *_: True)
    with pytest.raises(HTTPException) as exc:
        inbox_polls.send_inbox_poll("inst", "5511999999999", payload, request)
    assert exc.value.status_code == 409
    outbound.assert_not_called()


def test_inbox_poll_groups_send_one_native_poll_with_labeled_options(monkeypatch):
    request, conv = _request()
    template_id = uuid4()
    request_id = uuid4()
    questions = [
        {"question": "Qual imóvel?", "options": ["Casa", "Apartamento"]},
        {"question": "Quando pretende comprar?", "options": ["Agora", "Depois"]},
    ]
    template = {
        "id": str(template_id), "owner_key": "owner@test.com", "name": "Qualificação",
        "question": questions[0]["question"], "options_json": questions[0]["options"],
        "questions": questions, "allow_multiple_answers": False,
    }
    batch = {
        "id": str(request_id), "status": "sending", "allow_multiple_snapshot": False,
        "questions_snapshot_json": questions,
    }
    sent_rows = []

    def create_send(*args, **kwargs):
        question = kwargs["question"]
        sent = {
            "id": str(uuid4()), "owner_key": "owner@test.com", "instance_id": "inst",
            "conversation_key": "5511999999999", "question_snapshot": question["question"],
            "options_snapshot_json": question["options"], "allow_multiple_snapshot": False,
            "provider_message_id": "", "status": "sending", "batch_id": str(request_id),
            "question_index": 1, "question_count": 1,
        }
        sent_rows.append(sent)
        return sent, True

    def mark_send(send_id, _owner, status, provider_message_id="", error_detail=""):
        sent = next(item for item in sent_rows if item["id"] == send_id)
        return {**sent, "status": status, "provider_message_id": provider_message_id}

    monkeypatch.setattr(inbox_polls, "require_instance_access", lambda *_: None)
    monkeypatch.setattr(inbox_polls, "_tenant", lambda _: ("owner@test.com", "agent@test.com"))
    monkeypatch.setattr(inbox_polls, "_ready", lambda: None)
    monkeypatch.setattr(inbox_polls.polls, "get_template", lambda *_: template)
    monkeypatch.setattr(inbox_polls.polls, "create_batch", lambda *_: (batch, True))
    monkeypatch.setattr(inbox_polls.polls, "create_send", create_send)
    monkeypatch.setattr(inbox_polls.polls, "mark_send", mark_send)
    monkeypatch.setattr(inbox_polls.polls, "mark_batch", Mock())
    requests_sent = []

    def fake_post(url, json, headers, timeout):
        requests_sent.append({"url": url, "json": json})
        return SimpleNamespace(status_code=201, json=lambda: {"data": {"Info": {"ID": "poll-1"}}})

    monkeypatch.setattr("services.evolution_service.requests.post", fake_post)

    result = inbox_polls.send_inbox_poll(
        "inst", "5511999999999", inbox_polls.PollSendPayload(template_id=template_id, request_id=request_id), request,
    )

    assert result["ok"] is True
    assert requests_sent == [{
        "url": "https://evo/send/poll",
        "json": {
            "number": "5511999999999", "question": "Qualificação",
            "options": [
                "Casa", "Apartamento", "Agora", "Depois",
            ],
            "maxAnswer": 1,
        },
    }]
    assert len(result["sends"]) == len(conv["messages"]) == 1
    assert conv["messages"][0]["poll_options"] == requests_sent[0]["json"]["options"]


def test_poll_groups_reject_more_than_twelve_combined_options_and_long_option():
    from services.inbox_poll_pg_service import validate_questions

    with pytest.raises(ValueError, match="doze"):
        validate_questions([
            {"question": "Grupo A", "options": [str(index) for index in range(7)]},
            {"question": "Grupo B", "options": [str(index) for index in range(6)]},
        ])
    with pytest.raises(ValueError, match="100"):
        validate_questions([{"question": "Grupo", "options": ["A" * 101, "B"]}])


def test_poll_votes_are_mapped_to_sent_snapshot_and_keep_voter_identity():
    option_hash = hashlib.sha256("Opção antiga".encode()).hexdigest()
    votes = parse_votes({"votes": [{
        "voterJid": "5511999999999@s.whatsapp.net", "voterName": "Cliente",
        "selectedOptions": [option_hash], "voteMessageId": "vote-1",
        "votedAt": "2026-09-27T12:00:00Z",
    }]}, ["Opção antiga", "Outra opção"])
    assert votes[0]["selected_options"] == ["Opção antiga"]
    assert votes[0]["voter_key"] == "5511999999999@s.whatsapp.net"
    assert parse_votes({"votes": [{"voterJid": "x", "selectedOptions": ["hash desconhecido"]}]}, ["A"]) == []


def test_poll_results_404_means_no_provider_result(monkeypatch):
    monkeypatch.setattr("services.inbox_poll_results_service.requests.get", lambda *_args, **_kwargs: SimpleNamespace(status_code=404))
    assert fetch_results("https://evo", "key", "poll-123") is None


def test_poll_results_require_matching_send_and_use_snapshot(monkeypatch):
    request, _ = _request()
    send_id = uuid4()
    monkeypatch.setattr(inbox_polls, "require_instance_access", lambda *_: None)
    monkeypatch.setattr(inbox_polls, "_tenant", lambda _: ("owner@test.com", "agent@test.com"))
    monkeypatch.setattr(inbox_polls, "_ready", lambda: None)
    get_send = Mock(return_value={
        "id": str(send_id), "provider": "evolution", "provider_message_id": "poll-123",
        "options_snapshot_json": ["Antiga", "Nova"],
    })
    monkeypatch.setattr(inbox_polls.polls, "get_send", get_send)
    monkeypatch.setattr(inbox_polls.poll_results, "fetch_results", lambda *_: {"votes": []})
    monkeypatch.setattr(inbox_polls.poll_results, "parse_votes", lambda result, options: [{"selected_options": options}])
    save_votes = Mock()
    monkeypatch.setattr(inbox_polls.polls, "save_votes", save_votes)
    monkeypatch.setattr(inbox_polls.polls, "list_votes", lambda *_: [])

    result = inbox_polls.get_inbox_poll_results("inst", "5511999999999", send_id, request)
    assert result["provider_available"] is True
    get_send.assert_called_once_with(str(send_id), "owner@test.com", "inst", "5511999999999")
    assert save_votes.call_args.args[1][0]["selected_options"] == ["Antiga", "Nova"]
