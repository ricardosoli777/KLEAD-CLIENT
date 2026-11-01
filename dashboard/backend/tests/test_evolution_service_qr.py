from unittest.mock import Mock
import base64

from services.evolution_service import create_evolution_instance, download_media_bytes, send_whatsapp_poll


def test_send_poll_allows_exactly_one_choice(monkeypatch):
    captured = {}

    class Response:
        status_code = 201
        def json(self):
            return {"data": {"Info": {"ID": "poll-123"}}}

    def fake_post(url, json, headers, timeout):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return Response()

    monkeypatch.setattr("services.evolution_service.requests.post", fake_post)
    ok, detail, message_id = send_whatsapp_poll(
        "https://evo.example", "secret", "sender", "5511999999999",
        "Escolha uma opção", ["Confirmar", "Reagendar", "Aguardar"],
    )
    assert (ok, detail, message_id) == (True, "", "poll-123")
    assert captured["url"] == "https://evo.example/send/poll"
    assert captured["json"]["maxAnswer"] == 1


def _response(status_code, payload=None, text=""):
    res = Mock()
    res.status_code = status_code
    res.text = text
    res.json.return_value = payload or {}
    return res


def test_create_evolution_instance_returns_qr_code_string(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(("post", url, json, headers, timeout))
        return _response(200, {"hash": {"apikey": "instance-token"}})

    def fake_get(url, headers, timeout):
        calls.append(("get", url, None, headers, timeout))
        return _response(200, {"data": {"Qrcode": "base64-qr"}})

    monkeypatch.setattr("services.evolution_service.secrets.token_hex", lambda n: "generated-token")
    monkeypatch.setattr("services.evolution_service.requests.post", fake_post)
    monkeypatch.setattr("services.evolution_service.requests.get", fake_get)

    result = create_evolution_instance("http://evo.local/", "global-key", "RamonSDR")

    assert result == {"ok": True, "api_key": "instance-token", "qr_code": "base64-qr"}
    assert calls[0][1] == "http://evo.local/instance/create"
    assert calls[1][1] == "http://evo.local/instance/qr"
    assert calls[1][3]["apikey"] == "generated-token"


def test_create_evolution_instance_keeps_qr_code_string_when_qr_fetch_fails(monkeypatch):
    monkeypatch.setattr("services.evolution_service.secrets.token_hex", lambda n: "generated-token")
    monkeypatch.setattr(
        "services.evolution_service.requests.post",
        lambda *args, **kwargs: _response(200, {"token": "instance-token"}),
    )
    monkeypatch.setattr(
        "services.evolution_service.requests.get",
        lambda *args, **kwargs: _response(500, text="qr unavailable"),
    )

    result = create_evolution_instance("http://evo.local", "global-key", "RamonSDR")

    assert result["ok"] is True
    assert result["api_key"] == "instance-token"
    assert result["qr_code"] == ""
    assert result["detail"] == "HTTP 500"


def test_download_media_accepts_official_data_url_response(monkeypatch):
    raw = b"OggS" + (b"\x00" * 200)
    data_url = "data:audio/ogg;base64," + base64.b64encode(raw).decode()

    def fake_post(url, json, headers, timeout):
        assert url == "http://evo.local/message/downloadmedia"
        assert json == {"message": {"audioMessage": {"mimetype": "audio/ogg"}}}
        return _response(200, {"message": "success", "data": {"base64": data_url}})

    monkeypatch.setattr("services.evolution_service.requests.post", fake_post)

    result = download_media_bytes(
        "http://evo.local",
        "instance-key",
        {"Message": {"audioMessage": {"mimetype": "audio/ogg"}}},
    )

    assert result == raw


def test_download_media_rejects_short_or_generic_response_fields(monkeypatch):
    def fake_post(*args, **kwargs):
        return _response(200, {"message": "success", "content": base64.b64encode(b"not-media").decode()})

    monkeypatch.setattr("services.evolution_service.requests.post", fake_post)

    result = download_media_bytes("http://evo.local", "instance-key", {"Message": {"audioMessage": {}}})

    assert result is None
