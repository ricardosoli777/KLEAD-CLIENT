from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

import services.auth_service as auth_svc
import services.instances_pg_service as instances_pg_svc
from routers.instances import (
    CreateInstancePayload,
    _existing_instance_id,
    create_instance,
)
from routers.auth import auth_delete_user


def test_instance_lookup_is_case_insensitive():
    from core.state import instances, instances_lock

    with instances_lock:
        previous = dict(instances)
        instances.clear()
        instances["Kadosh"] = {"status": "idle"}
    try:
        assert _existing_instance_id("kadosh") == "Kadosh"
        assert _existing_instance_id("KADOSH") == "Kadosh"
        assert _existing_instance_id("outra") is None
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_postgres_schema_enforces_case_insensitive_instance_ids():
    assert "UNIQUE INDEX IF NOT EXISTS instances_instance_id_lower_uidx" in instances_pg_svc.SCHEMA_SQL
    assert "ON instances (lower(instance_id))" in instances_pg_svc.SCHEMA_SQL


def test_create_instance_requires_authenticated_admin_before_mutating_state():
    request = SimpleNamespace()
    payload = CreateInstancePayload(
        instance_id="sem-dono",
        openai_key="",
        prompt="",
    )

    with patch(
        "routers.instances._require_admin",
        side_effect=HTTPException(401, "Não autenticado"),
    ):
        with pytest.raises(HTTPException) as exc:
            create_instance(payload, request, _="internal-key")

    assert exc.value.status_code == 401


def test_register_created_instance_updates_access_and_ownership_together():
    previous = dict(auth_svc._users)
    try:
        auth_svc._users.clear()
        auth_svc._users["owner@test.com"] = {
            "email": "owner@test.com",
            "allowed_instances": ["existente"],
            "owned_instances": [],
        }
        with patch.object(auth_svc.settings_pg_service, "pg_configured", return_value=False), patch.object(
            auth_svc, "_save"
        ) as save:
            assert auth_svc.register_created_instance("owner@test.com", "nova") is True

        user = auth_svc._users["owner@test.com"]
        assert user["allowed_instances"] == ["existente", "nova"]
        assert user["owned_instances"] == ["nova"]
        save.assert_called_once()
    finally:
        auth_svc._users.clear()
        auth_svc._users.update(previous)


def test_register_created_instance_rolls_back_if_postgres_rejects_owner():
    previous = dict(auth_svc._users)
    try:
        auth_svc._users.clear()
        auth_svc._users["owner@test.com"] = {
            "email": "owner@test.com",
            "allowed_instances": [],
            "owned_instances": [],
            "updated_at": "before",
        }
        with patch.object(auth_svc.settings_pg_service, "pg_configured", return_value=True), patch.object(
            auth_svc.settings_pg_service,
            "save_setting",
            return_value=(False, "db unavailable"),
        ):
            assert auth_svc.register_created_instance("owner@test.com", "nova") is False

        assert auth_svc._users["owner@test.com"] == {
            "email": "owner@test.com",
            "allowed_instances": [],
            "owned_instances": [],
            "updated_at": "before",
        }
    finally:
        auth_svc._users.clear()
        auth_svc._users.update(previous)


def test_create_instance_rolls_back_memory_if_owner_registration_fails():
    from core.state import instances, instances_lock

    previous = dict(instances)
    save_instance = Mock()
    state = SimpleNamespace(
        safe_instance_path_part=lambda value: value,
        validate_api_url=lambda value: None,
        make_instance=lambda *args, **kwargs: {"status": "idle"},
        save_instance_config=save_instance,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={}, cookies={})
    payload = CreateInstancePayload(
        instance_id="owner-race",
        openai_key="",
        prompt="",
    )
    actor = {
        "email": "removed@test.com",
        "funcao": "admin",
        "allowed_instances": [],
        "owned_instances": [],
        "max_instances": 1,
    }

    with instances_lock:
        instances.clear()
    try:
        with patch("routers.instances._require_admin", return_value=actor), patch(
            "routers.instances.auth_svc.can_create_more_instances", return_value=True
        ), patch(
            "routers.instances.auth_svc.register_created_instance", return_value=False
        ):
            with pytest.raises(HTTPException) as exc:
                create_instance(payload, request, _="internal-key")

        assert exc.value.status_code == 409
        assert "owner-race" not in instances
        save_instance.assert_not_called()
    finally:
        with instances_lock:
            instances.clear()
            instances.update(previous)


def test_delete_user_cascades_only_owned_instances():
    previous = dict(auth_svc._users)
    owner_email = "owner-to-delete@test.com"
    actor = {
        "email": "super@test.com",
        "funcao": "superadmin",
        "allowed_instances": [],
    }
    request = SimpleNamespace(app=SimpleNamespace(), headers={}, cookies={})
    deleted = []
    try:
        auth_svc._users.clear()
        auth_svc._users.update({
            actor["email"]: actor,
            owner_email: {
                "email": owner_email,
                "funcao": "admin",
                "status": "active",
                "allowed_instances": ["propria", "compartilhada"],
                "owned_instances": ["propria"],
            },
        })

        with patch("routers.auth.require_admin", return_value=actor), patch(
            "routers.instances._delete_instance_core",
            side_effect=lambda iid, req: deleted.append(iid) or {"ok": True},
        ), patch("routers.auth._audit_actor"):
            result = auth_delete_user(owner_email, request)

        assert result == {"ok": True, "deleted_instances": ["propria"]}
        assert deleted == ["propria"]
        assert auth_svc.get_user_by_email(owner_email) is None
    finally:
        auth_svc._users.clear()
        auth_svc._users.update(previous)


def test_remove_instance_cleans_superadmin_references_without_deleting_account():
    previous = dict(auth_svc._users)
    email = "super@test.com"
    try:
        auth_svc._users.clear()
        auth_svc._users[email] = {
            "email": email,
            "funcao": "superadmin",
            "status": "active",
            "allowed_instances": ["RamonSDR", "RamonMeta"],
            "owned_instances": ["RamonMeta"],
        }

        with patch.object(auth_svc, "_save") as save:
            removed = auth_svc.remove_instance_from_all_users("RamonMeta")

        assert removed == []
        assert auth_svc._users[email]["allowed_instances"] == ["RamonSDR"]
        assert auth_svc._users[email]["owned_instances"] == []
        save.assert_called_once()
    finally:
        auth_svc._users.clear()
        auth_svc._users.update(previous)
