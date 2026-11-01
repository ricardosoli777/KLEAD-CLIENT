"""CAPI não depende do transporte usado para o WhatsApp."""

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_evolution_with_oauth_queues_lead_event_without_manual_token():
    import main as m

    inst = {
        "id": "evo-oauth",
        "provider": "evolution",
        "meta_capi_enabled": False,
        # OAuth fica em meta_connections; campos manuais permanecem vazios.
        "meta_capi_event_source_id": "pixel-selected-via-oauth",
        "meta_capi_access_token": "",
    }
    lead = {
        "id": "lead-new",
        "instancia_id": "evo-oauth",
        "telefone": "5511999999999",
    }

    with patch.object(m, "is_meta_capi_enabled", return_value=True), patch.object(
        m, "_lead_already_known_to_meta", return_value=False
    ), patch.object(
        m, "_try_link_existing_leadgen_id", return_value=False
    ), patch.object(m.threading, "Thread", _ImmediateThread), patch.object(
        m, "send_meta_capi_event", return_value=(True, "")
    ) as send:
        m._maybe_queue_meta_capi_event(inst, lead, "crm", event_name="Lead")

    send.assert_called_once_with(inst, lead, origem="crm", event_name="Lead")


def test_manual_capi_still_queues_for_meta_cloud_provider():
    import main as m

    inst = {
        "id": "meta-manual",
        "provider": "meta",
        "meta_capi_enabled": True,
        "meta_capi_event_source_id": "pixel-manual",
        "meta_capi_access_token": "manual-token",
    }
    lead = {"id": "lead-contact", "instancia_id": "meta-manual", "telefone": "5511888888888"}

    with patch.object(m.threading, "Thread", _ImmediateThread), patch.object(
        m, "send_meta_capi_event", return_value=(True, "")
    ) as send:
        m._maybe_queue_meta_capi_event(inst, lead, "crm", event_name="Contact")

    send.assert_called_once_with(inst, lead, origem="crm", event_name="Contact")


def test_capi_disabled_never_queues_regardless_of_provider():
    import main as m

    inst = {"id": "evo-disabled", "provider": "evolution", "meta_capi_enabled": False}
    lead = {"id": "lead-disabled", "instancia_id": "evo-disabled", "telefone": "5511777777777"}

    with patch.object(m, "is_meta_capi_enabled", return_value=False), patch.object(
        m.threading, "Thread", _ImmediateThread
    ), patch.object(
        m, "send_meta_capi_event"
    ) as send:
        m._maybe_queue_meta_capi_event(inst, lead, "crm", event_name="Contact")

    send.assert_not_called()


def test_missing_credentials_reaches_sender_and_is_audited_there():
    import main as m

    inst = {"id": "evo-no-creds", "provider": "evolution", "meta_capi_enabled": True}
    lead = {"id": "lead-no-creds", "instancia_id": "evo-no-creds", "telefone": "5511666666666"}

    with patch.object(m.threading, "Thread", _ImmediateThread), patch.object(
        m, "send_meta_capi_event", return_value=(False, "meta_capi_missing_credentials")
    ) as send:
        m._maybe_queue_meta_capi_event(inst, lead, "crm", event_name="Contact")

    send.assert_called_once_with(inst, lead, origem="crm", event_name="Contact")


def test_preview_accepts_oauth_even_when_manual_flag_is_false():
    from services import meta_capi_service as svc

    inst = {"id": "evo-oauth", "provider": "evolution", "meta_capi_enabled": False}
    lead = {"id": "lead-oauth", "instancia_id": "evo-oauth", "telefone": "5511555555555"}

    with patch.object(
        svc, "_resolve_capi_credentials", return_value=("pixel-oauth", "token-oauth", "oauth")
    ):
        ok, payload, detail = svc.preview_meta_capi_event(inst, lead, origem="crm", event_name="Lead")

    assert ok is True
    assert detail == ""
    assert payload["data"][0]["event_name"] == "Lead"


def test_effective_state_enables_oauth_with_selected_pixel():
    from services import meta_capi_service as svc

    inst = {"id": "evo-oauth", "provider": "evolution", "meta_capi_enabled": False}
    lead = {"instancia_id": "evo-oauth"}

    with patch.object(svc, "_resolve_capi_oauth", return_value=("pixel-oauth", "token-oauth")):
        assert svc.is_meta_capi_enabled(inst, lead) is True


def test_preview_keeps_manual_capi_disabled_when_flag_is_false():
    from services import meta_capi_service as svc

    inst = {
        "id": "manual-disabled",
        "meta_capi_enabled": False,
        "meta_capi_event_source_id": "pixel-manual",
        "meta_capi_access_token": "token-manual",
    }
    lead = {"id": "lead-manual", "instancia_id": "manual-disabled", "telefone": "5511444444444"}

    with patch.object(svc, "_resolve_capi_oauth", return_value=("", "")):
        ok, payload, detail = svc.preview_meta_capi_event(inst, lead, origem="crm", event_name="Lead")

    assert ok is False
    assert payload is None
    assert detail == "meta_capi_disabled"


def test_saving_evolution_credentials_preserves_oauth_capi():
    from core.state import instances, instances_lock
    from routers import instances as router

    previous = dict(instances)
    saved = []
    state = SimpleNamespace(
        save_instance_config=lambda iid: saved.append(iid),
        validate_api_url=lambda value: value,
        is_masked_secret=lambda value: False,
        normalize_meta_ad_account_id=lambda value: value,
        validate_meta_token=lambda *_args: {"ok": True},
        subscribe_meta_page_leadgen=lambda *_args: {"ok": True},
        meta_health_cache={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    actor = {"email": "owner@test.com", "funcao": "admin", "allowed_instances": ["evo-oauth"]}

    with instances_lock:
        instances.clear()
        instances["evo-oauth"] = {"status": "idle", "meta_capi_enabled": False}
    try:
        with patch.object(router, "_require_admin", return_value=actor), patch.object(
            router.auth_svc, "can_access_instance", return_value=True
        ), patch.object(
            router.meta_oauth_pg_svc,
            "get_instance_assets",
            return_value={"status": "active", "pixel_id": "pixel-oauth"},
        ):
            result = router.update_credentials(
                "evo-oauth", router.UpdateInstancePayload(provider="evolution"), request, _="key"
            )

        assert result["ok"] is True
        assert instances["evo-oauth"]["meta_capi_enabled"] is True
        assert saved == ["evo-oauth"]
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_credentials_response_reports_effective_oauth_capi_state():
    from core.state import instances, instances_lock
    from routers import instances as router

    previous = dict(instances)
    state = SimpleNamespace(mask_secret=lambda value: value and "***")
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    actor = {"email": "owner@test.com", "funcao": "admin", "allowed_instances": ["evo-oauth"]}
    base = {
        "api_url": "https://go.example",
        "api_key": "secret",
        "openai_key": "",
        "prompt": "",
        "provider": "evolution",
        "meta_capi_enabled": False,
        "meta_capi_event_source_id": "",
    }
    with instances_lock:
        instances.clear()
        instances["evo-oauth"] = base
    try:
        with patch.object(router, "_require_admin", return_value=actor), patch.object(
            router.auth_svc, "can_access_instance", return_value=True
        ), patch.object(
            router.meta_oauth_pg_svc,
            "get_instance_assets",
            return_value={"status": "active", "pixel_id": "pixel-oauth"},
        ):
            result = router.get_credentials("evo-oauth", request, _="key")

        assert result["meta_capi_enabled"] is True
        assert result["meta_capi_event_source_id"] == "pixel-oauth"
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_disconnect_oauth_disables_capi_without_manual_fallback():
    from core.state import instances, instances_lock
    from routers import meta_oauth as router

    previous = dict(instances)
    saved = []
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(save_instance_config=lambda iid: saved.append(iid)))
    )
    with instances_lock:
        instances.clear()
        instances["evo-oauth"] = {
            "meta_capi_enabled": True,
            "meta_capi_event_source_id": "pixel-oauth",
            "meta_capi_access_token": "",
        }
    try:
        with patch.object(router.auth_svc, "require_instance_access"), patch.object(
            router.meta_oauth_pg, "disconnect_instance", return_value=(True, "")
        ):
            result = router.meta_disconnect("evo-oauth", request, _="key")

        assert result == {"ok": True}
        assert instances["evo-oauth"]["meta_capi_enabled"] is False
        assert instances["evo-oauth"]["meta_capi_event_source_id"] == ""
        assert saved == ["evo-oauth"]
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)
