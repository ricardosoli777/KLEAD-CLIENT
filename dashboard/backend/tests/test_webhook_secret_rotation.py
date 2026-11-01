from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.state import instances, instances_lock
from routers import webhook as router


@pytest.fixture
def evolution_instance():
    instance_id = "rotate-safe-test"
    with instances_lock:
        previous = dict(instances)
        instances.clear()
        instances[instance_id] = {
            "provider": "evolution",
            "api_url": "http://evolution:4000",
            "api_key": "api-key",
            "webhook_secret": "old-secret",
        }
    try:
        yield instance_id
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def _request(register, save, register_postgres=None):
    if register_postgres is None:
        register_postgres = lambda *_args: {"ok": False, "detail": "sql unavailable"}
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                register_webhook_evolution=register,
                register_webhook_postgres=register_postgres,
                save_instance_config=save,
            )
        )
    )


def _rotate(instance_id, request):
    with patch.object(router, "_require_admin", return_value={"email": "admin@test.com"}), patch.object(
        router.auth_svc, "can_access_instance", return_value=True
    ):
        return router.rotate_webhook_secret(instance_id, request, _="api-key")


def test_rotate_evolution_registers_new_secret_before_persisting(evolution_instance):
    calls = []

    def register(_instance_id, inst):
        calls.append(("register", inst["webhook_secret"]))
        return {"ok": True}

    def save(_instance_id):
        calls.append(("save", instances[evolution_instance]["webhook_secret"]))
        return True

    result = _rotate(evolution_instance, _request(register, save))

    assert result["ok"] is True
    assert result["webhook_secret"] != "old-secret"
    assert calls == [
        ("register", result["webhook_secret"]),
        ("save", result["webhook_secret"]),
    ]
    assert instances[evolution_instance]["webhook_secret"] == result["webhook_secret"]


def test_rotate_evolution_keeps_old_secret_when_registration_fails(evolution_instance):
    save = MagicMock()
    request = _request(lambda *_args: {"ok": False, "detail": "evolution down"}, save)

    with pytest.raises(HTTPException) as exc:
        _rotate(evolution_instance, request)

    assert exc.value.status_code == 502
    assert instances[evolution_instance]["webhook_secret"] == "old-secret"
    save.assert_not_called()


def test_rotate_evolution_rolls_back_both_sides_when_persistence_fails(evolution_instance):
    registered = []

    def register(_instance_id, inst):
        registered.append(inst["webhook_secret"])
        return {"ok": True}

    with pytest.raises(HTTPException) as exc:
        _rotate(evolution_instance, _request(register, lambda _instance_id: False))

    assert exc.value.status_code == 503
    assert instances[evolution_instance]["webhook_secret"] == "old-secret"
    assert len(registered) == 2
    assert registered[0] != "old-secret"
    assert registered[1] == "old-secret"


def test_rotate_evolution_falls_back_to_postgres(evolution_instance):
    api_register = MagicMock(return_value={"ok": False, "detail": "unsupported endpoint"})
    sql_secrets = []

    def sql_register(_instance_id, inst):
        sql_secrets.append(inst["webhook_secret"])
        return {"ok": True}

    result = _rotate(
        evolution_instance,
        _request(api_register, lambda _instance_id: True, sql_register),
    )

    assert result["ok"] is True
    assert sql_secrets == [result["webhook_secret"]]
    assert instances[evolution_instance]["webhook_secret"] == result["webhook_secret"]
