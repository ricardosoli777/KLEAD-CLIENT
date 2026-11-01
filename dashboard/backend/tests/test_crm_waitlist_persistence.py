from unittest.mock import patch

import pytest

from services import crm_pg_service, crm_service


class _Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def close(self):
        pass


class _Connection:
    def __init__(self):
        self.cursor_obj = _Cursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _snapshot_with_waitlist_lead(coluna_id=None):
    return {
        "columns": [
            {
                "id": "RamonSDR__novo",
                "name": "Novo Lead",
                "color": "#25D366",
                "order": 0,
                "instancia_id": "RamonSDR",
            }
        ],
        "leads": [
            {
                "id": "c359373e-9fae-436b-b7f6-bf3aa82826c0",
                "instancia_id": "RamonSDR",
                "nome": "Lead de teste",
                "origem": "manual",
                "coluna_id": coluna_id,
                "created_at": "2026-08-22T12:00:00+00:00",
                "updated_at": "2026-08-22T12:01:00+00:00",
            }
        ],
        "listas": [
            {
                "id": "opera",
                "name": "OPERA LEADS",
                "filtros": {},
                "lead_ids": ["c359373e-9fae-436b-b7f6-bf3aa82826c0"],
                "created_at": "2026-08-22T12:00:00+00:00",
            }
        ],
    }


def test_crm_schema_allows_lead_outside_kanban():
    assert "ALTER TABLE crm_leads ALTER COLUMN coluna_id DROP NOT NULL" in crm_pg_service.SCHEMA_SQL


@pytest.mark.parametrize("in_memory_value", [None, ""])
def test_sync_snapshot_persists_waitlist_lead_with_null_column(in_memory_value):
    conn = _Connection()
    with patch.object(crm_pg_service, "ensure_schema", return_value=(True, "")), patch.object(
        crm_pg_service, "_connect", return_value=conn
    ):
        ok, detail = crm_pg_service.sync_snapshot(_snapshot_with_waitlist_lead(in_memory_value))

    assert (ok, detail) == (True, "")
    assert conn.committed is True
    lead_insert = next(
        params
        for sql, params in conn.cursor_obj.calls
        if "INSERT INTO crm_leads" in sql
    )
    assert lead_insert[20] is None


def test_crm_save_does_not_hide_postgres_failure():
    persisted = {"columns": [], "leads": [{"id": "persisted"}]}
    dirty = {"columns": [], "leads": [{"id": "dirty"}]}
    with patch.object(crm_service, "_last_persisted_crm", persisted), patch.object(
        crm_service, "_crm", dirty
    ), patch.object(crm_service.crm_pg_service, "pg_configured", return_value=True), patch.object(
        crm_service.crm_pg_service, "sync_snapshot", return_value=(False, "pg failed")
    ):
        with pytest.raises(crm_service.CrmPersistenceError, match="pg failed"):
            crm_service._save()
        assert crm_service._crm == persisted
