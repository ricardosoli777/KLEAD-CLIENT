from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from core.state import instances, instances_lock
from routers import instances as router
from services import evolution_service


@pytest.fixture
def reconnect_instance_state():
    instance_id = "reconnect-cache-test"
    with instances_lock:
        previous = dict(instances)
        instances.clear()
        instances[instance_id] = {
            "api_url": "http://evolution:4000/",
            "api_key": "instance-token",
        }
    try:
        yield instance_id
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_invalidate_instance_cache_removes_all_keys_for_server():
    with evolution_service._instance_all_lock:
        previous = dict(evolution_service._instance_all_cache)
        evolution_service._instance_all_cache.clear()
        evolution_service._instance_all_cache[
            ("http://evolution:4000", "global-a")
        ] = (1.0, [{"connected": False}])
        evolution_service._instance_all_cache[
            ("http://evolution:4000", "global-b")
        ] = (2.0, [{"connected": True}])
        evolution_service._instance_all_cache[
            ("http://other:4000", "global-c")
        ] = (3.0, [])
    try:
        evolution_service.invalidate_instance_cache("http://evolution:4000/")
        with evolution_service._instance_all_lock:
            assert set(evolution_service._instance_all_cache) == {
                ("http://other:4000", "global-c")
            }
    finally:
        with evolution_service._instance_all_lock:
            evolution_service._instance_all_cache.clear()
            evolution_service._instance_all_cache.update(previous)


def test_reconnect_success_invalidates_cached_status(reconnect_instance_state):
    request = SimpleNamespace()
    with patch.object(router.auth_svc, "require_instance_access"), patch.object(
        router, "reconnect_instance", return_value=(True, "")
    ) as reconnect, patch.object(router, "invalidate_instance_cache") as invalidate:
        result = router.reconnect_whatsapp(reconnect_instance_state, request, _="key")

    assert result == {"ok": True}
    reconnect.assert_called_once_with(
        "http://evolution:4000/", "instance-token", reconnect_instance_state
    )
    invalidate.assert_called_once_with("http://evolution:4000/")


def test_reconnect_failure_keeps_cache_and_returns_502(reconnect_instance_state):
    request = SimpleNamespace()
    with patch.object(router.auth_svc, "require_instance_access"), patch.object(
        router, "reconnect_instance", return_value=(False, "session unavailable")
    ), patch.object(router, "invalidate_instance_cache") as invalidate:
        with pytest.raises(HTTPException) as exc:
            router.reconnect_whatsapp(reconnect_instance_state, request, _="key")

    assert exc.value.status_code == 502
    invalidate.assert_not_called()
