"""
Testes para os pg_services criados na migração JSON → Postgres.
Usa mocks de psycopg2 — não requer banco de dados em execução.

Cobertura:
  - Fernet round-trip (encrypt → decrypt)
  - instances_pg_service: _cfg_to_params, _row_to_cfg, sync_all (mock)
  - settings_pg_service: save_setting, load_setting (mock)
  - Flags de migração (defaults e override por env)
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# Fernet encryption round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestFernetRoundTrip:
    def test_encrypt_decrypt_returns_original(self):
        from main import _make_fernet, _encrypt_field, _decrypt_field

        fernet = _make_fernet()
        original = "super-secret-api-key-12345"
        encrypted = _encrypt_field(fernet, original)
        decrypted = _decrypt_field(fernet, encrypted)

        assert decrypted == original
        assert encrypted != original
        assert encrypted.startswith("gAAAAA")

    def test_empty_string_stays_empty(self):
        from main import _make_fernet, _encrypt_field, _decrypt_field

        fernet = _make_fernet()
        assert _encrypt_field(fernet, "") == ""
        assert _decrypt_field(fernet, "") == ""

    def test_different_keys_cannot_decrypt(self):
        from cryptography.fernet import Fernet, InvalidToken
        from main import _encrypt_field, _decrypt_field

        import base64, hashlib
        secret_a = "a" * 64
        secret_b = "b" * 64
        key_a = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_a.encode()).digest()))
        key_b = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_b.encode()).digest()))

        encrypted = _encrypt_field(key_a, "valor-secreto")
        result = _decrypt_field(key_b, encrypted)
        assert result == encrypted  # falha silenciosa retorna o token original


# ═══════════════════════════════════════════════════════════════════════════════
# instances_pg_service — conversão de linha
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstancesPgServiceConversion:
    def test_cfg_to_params_jsonb_fields_serialized(self):
        from services.instances_pg_service import _cfg_to_params, _COLUMNS, _JSONB_COLUMNS

        cfg = {
            "contacts": [{"number": "5511999990001"}],
            "auto_handoff_keywords": ["urgente", "cancelar"],
        }
        params = _cfg_to_params(cfg)
        idx_contacts = _COLUMNS.index("contacts")
        idx_keywords = _COLUMNS.index("auto_handoff_keywords")

        assert json.loads(params[idx_contacts]) == [{"number": "5511999990001"}]
        assert json.loads(params[idx_keywords]) == ["urgente", "cancelar"]

    def test_row_to_cfg_deserializes_jsonb(self):
        from services.instances_pg_service import _row_to_cfg, _COLUMNS

        row = [None] * len(_COLUMNS)
        idx_contacts = _COLUMNS.index("contacts")
        idx_keywords = _COLUMNS.index("auto_handoff_keywords")
        row[idx_contacts] = [{"number": "5511"}]
        row[idx_keywords] = ["stop"]
        row[_COLUMNS.index("api_url")] = "http://evolution_go:4000"
        row[_COLUMNS.index("provider")] = "meta"

        cfg = _row_to_cfg(tuple(row))
        assert cfg["contacts"] == [{"number": "5511"}]
        assert cfg["auto_handoff_keywords"] == ["stop"]
        assert cfg["api_url"] == "http://evolution_go:4000"

    def test_cfg_to_params_none_values_use_defaults(self):
        from services.instances_pg_service import _cfg_to_params, _COLUMNS

        params = _cfg_to_params({})  # empty config → all defaults
        idx_provider = _COLUMNS.index("provider")
        idx_max_turns = _COLUMNS.index("max_ai_turns")

        assert params[idx_provider] == "evolution"
        assert params[idx_max_turns] == 8


# ═══════════════════════════════════════════════════════════════════════════════
# instances_pg_service — sync_all com mock de Postgres
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstancesPgSyncAll:
    def _make_mock_conn(self):
        cur = MagicMock()
        cur.fetchone.return_value = (0,)
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_sync_all_upserts_each_instance(self):
        from services import instances_pg_service

        conn, cur = self._make_mock_conn()
        with patch.object(instances_pg_service, "_connect", return_value=conn), \
             patch.object(instances_pg_service, "pg_configured", return_value=True):
            configs = {
                "inst1": {"api_url": "http://evo:4000", "provider": "evolution"},
                "inst2": {"api_url": "http://evo:4000", "provider": "meta"},
            }
            ok, detail = instances_pg_service.sync_all(configs)

        assert ok is True
        assert cur.execute.call_count >= 3  # 2 upserts + 1 delete

    def test_sync_all_aborts_delete_when_configs_empty_and_pg_has_rows(self):
        from services import instances_pg_service

        conn, cur = self._make_mock_conn()
        cur.fetchone.return_value = (5,)  # simula 5 rows no PG
        with patch.object(instances_pg_service, "_connect", return_value=conn), \
             patch.object(instances_pg_service, "pg_configured", return_value=True):
            ok, detail = instances_pg_service.sync_all({})

        assert ok is True
        delete_calls = [c for c in cur.execute.call_args_list if "DELETE" in str(c)]
        assert len(delete_calls) == 0  # proteção: não apagou nada


# ═══════════════════════════════════════════════════════════════════════════════
# settings_pg_service — save_setting / load_setting com mock
# ═══════════════════════════════════════════════════════════════════════════════

class TestSettingsPgService:
    def _make_mock_conn(self, return_value=None):
        cur = MagicMock()
        cur.fetchone.return_value = (return_value,) if return_value is not None else None
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_save_setting_calls_upsert(self):
        from services import settings_pg_service

        conn, cur = self._make_mock_conn()
        with patch.object(settings_pg_service, "_connect", return_value=conn), \
             patch.object(settings_pg_service, "pg_configured", return_value=True):
            ok, detail = settings_pg_service.save_setting("users", {"admin@x.com": {"funcao": "admin"}})

        assert ok is True
        assert cur.execute.called
        sql_called = cur.execute.call_args_list[-1][0][0]
        assert "INSERT" in sql_called and "klead_settings" in sql_called

    def test_load_setting_returns_value(self):
        from services import settings_pg_service

        data = {"admin@x.com": {"funcao": "admin"}}
        conn, cur = self._make_mock_conn(return_value=data)
        with patch.object(settings_pg_service, "_connect", return_value=conn), \
             patch.object(settings_pg_service, "pg_configured", return_value=True):
            ok, result = settings_pg_service.load_setting("users")

        assert ok is True
        assert result == data

    def test_load_setting_returns_false_when_not_found(self):
        from services import settings_pg_service

        conn, cur = self._make_mock_conn(return_value=None)
        cur.fetchone.return_value = None
        with patch.object(settings_pg_service, "_connect", return_value=conn), \
             patch.object(settings_pg_service, "pg_configured", return_value=True):
            ok, result = settings_pg_service.load_setting("nonexistent")

        assert ok is False


# ═══════════════════════════════════════════════════════════════════════════════
# conversation_pg_service — upsert/delete incremental (caminho quente)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversationUpsert:
    def setup_method(self):
        # _synced_hashes é cache global do processo (propositalmente, para
        # persistir entre chamadas em produção) — precisa ser isolado entre
        # testes para não mascarar upserts subsequentes como "no-op".
        from services import conversation_pg_service as svc
        svc._synced_hashes.clear()

    def _make_mock_conn(self, conv_id="11111111-1111-1111-1111-111111111111"):
        cur = MagicMock()
        cur.fetchone.return_value = (conv_id,)
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_upsert_grava_conversa_e_regrava_mensagens(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        conv = {
            "name": "Lead A",
            "stage": "qualifying",
            "messages": [{"role": "user", "content": "Oi", "timestamp": "2026-01-01T00:00:00"}],
            "last_activity": "2026-01-01T00:00:00",
        }
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn), \
             patch("psycopg2.extras.execute_values") as mock_ev:
            ok, detail = svc.upsert_conversation("inst-1", "5511999990000", conv)

        assert ok is True, detail
        sqls = [c[0][0] for c in cur.execute.call_args_list]
        assert any("ON CONFLICT (instancia_id, conversation_key)" in s for s in sqls)
        assert any("DELETE FROM conversation_messages" in s for s in sqls)
        assert mock_ev.called  # mensagens reinseridas em lote
        assert conn.commit.called

    def test_upsert_sem_mensagens_nao_chama_execute_values(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn), \
             patch("psycopg2.extras.execute_values") as mock_ev:
            ok, _ = svc.upsert_conversation("inst-1", "5511999990000", {"name": "X", "messages": []})

        assert ok is True
        mock_ev.assert_not_called()

    def test_upsert_falha_retorna_false_e_faz_rollback(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.execute.side_effect = OSError("pg down")
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.upsert_conversation("inst-1", "5511999990000", {"name": "X", "messages": []})

        assert ok is False
        assert "pg down" in detail
        assert conn.rollback.called

    def test_delete_conversation_e_soft_delete_nao_remove_a_linha(self):
        """delete_conversation deve marcar deleted_at (UPDATE), nunca DELETE a linha —
        é o que permite restaurar uma conversa apagada por engano."""
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, _ = svc.delete_conversation("inst-1", "5511999990000")

        assert ok is True
        sql, params = cur.execute.call_args[0]
        assert "UPDATE conversations SET deleted_at" in sql
        assert "DELETE" not in sql
        assert params == ("inst-1", "5511999990000")

    def test_delete_instance_conversations_executa_delete_por_instancia(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, _ = svc.delete_instance_conversations("inst-1")

        assert ok is True
        sql, params = cur.execute.call_args[0]
        assert "DELETE FROM conversations WHERE instancia_id" in sql
        assert params == ("inst-1",)


# ═══════════════════════════════════════════════════════════════════════════════
# conversation_pg_service — leitura paginada (preview, Sessão 1 do roadmap de escala)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversationsPagination:
    def _row(self, phone, last_activity, created_at="2026-01-01T00:00:00+00:00"):
        from datetime import datetime

        ts = datetime.fromisoformat(last_activity)
        created = datetime.fromisoformat(created_at)
        return (
            phone, f"Lead {phone}", "qualifying", False, False,
            None, "", 0, False, "", None, "", None,
            "", "", False, {}, created, ts,
        )

    def test_load_conversations_page_filtra_instancia_e_nao_deletadas(self):
        from services import conversation_pg_service as svc

        cur = MagicMock()
        cur.fetchall.return_value = [self._row("5511900000001", "2026-01-02T10:00:00+00:00")]
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, result = svc.load_conversations_page("inst-1", cursor=None, limit=50)

        assert ok is True
        sql, params = cur.execute.call_args[0]
        assert "instancia_id = %s AND deleted_at IS NULL" in sql
        assert "ORDER BY last_activity DESC" in sql
        assert params[0] == "inst-1"
        assert result["conversations"][0]["phone"] == "5511900000001"
        assert result["next_cursor"] is None  # menos itens que o limit

    def test_load_conversations_page_avanca_cursor_sem_repetir_itens(self):
        """2ª página (usando o next_cursor da 1ª) deve filtrar estritamente
        antes do último item da página anterior — sem repetir nem pular."""
        from services import conversation_pg_service as svc

        page1_cur = MagicMock()
        page1_cur.fetchall.return_value = [
            self._row("a", "2026-01-03T00:00:00+00:00"),
            self._row("b", "2026-01-02T00:00:00+00:00"),
        ]
        conn1 = MagicMock()
        conn1.cursor.return_value = page1_cur
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn1):
            ok1, page1 = svc.load_conversations_page("inst-1", cursor=None, limit=2)
        assert ok1 is True
        next_cursor = page1["next_cursor"]
        assert next_cursor == "2026-01-02T00:00:00+00:00|b"

        page2_cur = MagicMock()
        page2_cur.fetchall.return_value = [self._row("c", "2026-01-01T00:00:00+00:00")]
        conn2 = MagicMock()
        conn2.cursor.return_value = page2_cur
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn2):
            ok2, page2 = svc.load_conversations_page("inst-1", cursor=next_cursor, limit=2)

        assert ok2 is True
        sql, params = page2_cur.execute.call_args[0]
        assert "(last_activity, conversation_key) < (%s, %s)" in sql
        assert params[1:3] == ["2026-01-02T00:00:00+00:00", "b"]
        returned_phones = [c["phone"] for c in page2["conversations"]]
        assert returned_phones == ["c"]  # não repete "a" nem "b"

    def test_load_conversation_messages_page_usa_id_da_conversa_e_ordena_desc(self):
        from services import conversation_pg_service as svc

        cur = MagicMock()
        cur.fetchone.return_value = ("conv-uuid-1",)
        cur.fetchall.return_value = [
            ("user", "oi", None, "text", "", {}),
        ]
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, messages = svc.load_conversation_messages_page("inst-1", "5511900000001", limit=100)

        assert ok is True
        assert len(messages) == 1
        # 2ª chamada de execute é a query de mensagens (1ª resolve o conversation_id)
        sql, params = cur.execute.call_args_list[1][0]
        assert "conversation_id = %s" in sql
        assert "ORDER BY timestamp DESC" in sql
        assert params[0] == "conv-uuid-1"

    def test_load_conversation_messages_page_conversa_inexistente_retorna_false(self):
        from services import conversation_pg_service as svc

        cur = MagicMock()
        cur.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.load_conversation_messages_page("inst-1", "nao-existe")

        assert ok is False
        assert detail == "conversation_not_found"

class TestConversationSoftDeleteTrash:
    """Lixeira: restore_conversation, list_deleted_conversations, purge_deleted_conversations."""

    def _make_mock_conn(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_restore_conversation_falha_se_nao_encontrada(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.fetchone.return_value = None
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.restore_conversation("inst-1", "5511999990000")

        assert ok is False
        assert detail == "not_found_or_not_deleted"
        assert conn.rollback.called

    def test_restore_conversation_sucesso_recarrega_do_snapshot(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.fetchone.return_value = ("11111111-1111-1111-1111-111111111111",)
        restored_conv = {"name": "Sérgio", "messages": []}
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn), \
             patch.object(svc, "load_snapshot", return_value=(True, {"inst-1": {"5511999990000": restored_conv}})):
            ok, conv = svc.restore_conversation("inst-1", "5511999990000")

        assert ok is True
        assert conv == restored_conv
        sql, params = cur.execute.call_args[0]
        assert "UPDATE conversations SET deleted_at = NULL" in sql
        assert params == ("inst-1", "5511999990000")

    def test_list_deleted_conversations_filtra_por_instancia(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = [
            ("inst-1", "5511999990000", "Sérgio", "sergio@x.com", None, None),
        ]
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            items = svc.list_deleted_conversations("inst-1")

        assert len(items) == 1
        assert items[0]["conversation_key"] == "5511999990000"
        sql, params = cur.execute.call_args[0]
        assert "WHERE deleted_at IS NOT NULL AND instancia_id" in sql
        assert params == ("inst-1",)

    def test_purge_deleted_conversations_usa_janela_de_retencao(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.rowcount = 3
        with patch.object(svc, "_ensure_schema_once", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, purged, _ = svc.purge_deleted_conversations(30)

        assert ok is True
        assert purged == 3
        sql, params = cur.execute.call_args[0]
        assert "deleted_at < now() - (%s * interval" in sql
        assert params == (30,)

    def test_truncate_all_preserva_conversas_soft_deleted(self):
        """sync_snapshot (reparo periódico) não pode apagar de vez o que foi
        soft-deleted — senão a lixeira sumiria na próxima sincronização."""
        from services import conversation_pg_service as svc

        cur = MagicMock()
        svc._truncate_all(cur)

        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert any("conversation_messages WHERE conversation_id IN" in s and "deleted_at IS NULL" in s for s in calls_sql)
        assert any(s == "DELETE FROM conversations WHERE deleted_at IS NULL" for s in calls_sql)


# ═══════════════════════════════════════════════════════════════════════════════
# sync_snapshot: diff por hash em vez de TRUNCATE + regrava tudo
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncSnapshotDiff:
    def _make_mock_conn(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def setup_method(self):
        from services import conversation_pg_service as svc
        svc._synced_hashes.clear()

    def teardown_method(self):
        from services import conversation_pg_service as svc
        svc._synced_hashes.clear()

    def test_conversa_inalterada_nao_gera_upsert(self):
        from services import conversation_pg_service as svc

        conv = {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        key = ("inst-1", "5511999990000")
        svc._synced_hashes[key] = svc._conv_hash(conv)

        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = [key]  # existing_keys no Postgres = igual à RAM
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.sync_snapshot({"inst-1": {"5511999990000": conv}})

        assert ok is True
        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert not any(s.strip().startswith("INSERT INTO conversations") for s in calls_sql)
        assert not any(s.strip().startswith("DELETE FROM conversations") for s in calls_sql)
        assert conn.commit.called

    def test_conversa_nova_ou_alterada_e_persistida(self):
        from services import conversation_pg_service as svc

        conv = {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = []  # nada no Postgres ainda
        cur.fetchone.return_value = ("conv-uuid-1",)  # RETURNING id do insert
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.sync_snapshot({"inst-1": {"5511999990000": conv}})

        assert ok is True
        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert any("INSERT INTO conversations" in s and "ON CONFLICT" in s for s in calls_sql)
        assert svc._synced_hashes[("inst-1", "5511999990000")] == svc._conv_hash(conv)

    def test_conversa_removida_da_ram_e_apagada_deriva(self):
        from services import conversation_pg_service as svc

        stale_key = ("inst-1", "5511888880000")
        svc._synced_hashes[stale_key] = "hash-antigo"
        outra_conv = {"name": "Lead B", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = [stale_key]  # existe no Postgres mas não está mais na RAM
        cur.fetchone.return_value = ("conv-uuid-2",)  # RETURNING id da outra conversa (nova)
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            # conv_data não vazio (tem outra conversa) para não disparar a guarda anti-wipe —
            # o que se testa aqui é a limpeza de deriva de UMA conversa que saiu da RAM.
            ok, detail = svc.sync_snapshot({"inst-1": {"5511777770000": outra_conv}})

        assert ok is True
        calls = cur.execute.call_args_list
        delete_calls = [c for c in calls if c[0][0].strip().startswith("DELETE FROM conversations WHERE instancia_id")]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1] == ("inst-1", "5511888880000")
        assert stale_key not in svc._synced_hashes

    def test_guarda_anti_wipe_ainda_funciona(self):
        from services import conversation_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.fetchone.return_value = (5,)  # 5 conversas ativas já no Postgres
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.sync_snapshot({})

        assert ok is True
        assert detail == "skip-empty-guard"
        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert not any("DELETE FROM conversations" in s for s in calls_sql)
        assert not conn.commit.called

    def test_existing_keys_query_preserva_soft_deletes(self):
        from services import conversation_pg_service as svc

        conv = {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = ("conv-uuid-1",)
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            svc.sync_snapshot({"inst-1": {"5511999990000": conv}})

        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert any(
            "SELECT instancia_id, conversation_key FROM conversations" in s and "deleted_at IS NULL" in s
            for s in calls_sql
        )

    def test_guarda_anti_deriva_em_massa_pula_limpeza(self):
        from services import conversation_pg_service as svc

        # 10 conversas já no Postgres, mas o snapshot da RAM só traz 1 (ex:
        # falha parcial de leitura de outras instâncias) — mais de 5% (e mais
        # de 5 chaves) "sumiriam", então a limpeza deve ser pulada.
        existing = [("inst-1", f"55119999900{i:02d}") for i in range(10)]
        conv = {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = existing
        cur.fetchone.return_value = ("conv-uuid-1",)
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.sync_snapshot({"inst-1": {existing[0][1]: conv}})

        assert ok is True
        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert not any(s.strip().startswith("DELETE FROM conversations WHERE instancia_id") for s in calls_sql)
        assert conn.commit.called

    def test_guarda_anti_deriva_nao_bloqueia_limpeza_pequena(self):
        from services import conversation_pg_service as svc

        # 10 conversas no Postgres, RAM sem 1 delas só — fração pequena (10%
        # mas só 1 chave, abaixo do piso de 5 chaves), limpeza normal deve ocorrer.
        existing = [("inst-1", f"55119999900{i:02d}") for i in range(10)]
        conv = {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = existing
        cur.fetchone.return_value = ("conv-uuid-1",)
        incoming_keys = existing[1:]
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.sync_snapshot({"inst-1": {k[1]: conv for k in incoming_keys}})

        assert ok is True
        calls_sql = [c[0][0] for c in cur.execute.call_args_list]
        assert any(s.strip().startswith("DELETE FROM conversations WHERE instancia_id") for s in calls_sql)

    def test_rollback_nao_deixa_cache_hash_inconsistente(self):
        from services import conversation_pg_service as svc

        conv = {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"}
        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = []

        def boom(*args, **kwargs):
            sql = args[0] if args else ""
            if isinstance(sql, str) and sql.strip().startswith("INSERT INTO conversations"):
                raise RuntimeError("pg down no meio do sync")
            return MagicMock()

        cur.execute.side_effect = boom
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.sync_snapshot({"inst-1": {"5511999990000": conv}})

        assert ok is False
        assert conn.rollback.called
        assert ("inst-1", "5511999990000") not in svc._synced_hashes


# ═══════════════════════════════════════════════════════════════════════════════
# Postgres como única fonte de verdade — sem JSON, com fail-fast no startup
# ═══════════════════════════════════════════════════════════════════════════════

class TestPgOnlyPersistence:
    """Sem PG configurado (dev/teste) nada persiste; com PG configurado mas
    falhando no load, o startup aborta — subir com estado vazio faria o
    próximo save sobrescrever os dados reais no banco."""

    def test_save_conversations_sem_pg_nao_persiste(self):
        import main as app_module
        from unittest.mock import patch
        with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=False), \
             patch.object(app_module.conversation_pg_service, "sync_snapshot") as mock_sync:
            app_module._save_conversations()
        mock_sync.assert_not_called()

    def test_save_conversation_sem_pg_nao_persiste(self):
        import main as app_module
        from unittest.mock import patch
        with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=False), \
             patch.object(app_module.conversation_pg_service, "upsert_conversation") as mock_up:
            app_module._save_conversation("inst-x", "5511999990000")
        mock_up.assert_not_called()

    def test_save_conversation_persiste_somente_a_conversa_alvo(self):
        import main as app_module
        from unittest.mock import patch
        iid = "inst-upsert-test"
        with app_module.conversations_lock:
            app_module.conversations[iid] = {
                "5511999990000": {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"},
                "5511888880000": {"name": "Lead B", "messages": [], "last_activity": "2026-01-01T00:00:00"},
            }
        try:
            with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=True), \
                 patch.object(app_module.conversation_pg_service, "upsert_conversation",
                              return_value=(True, "")) as mock_up:
                app_module._save_conversation(iid, "5511999990000")
            mock_up.assert_called_once()
            args = mock_up.call_args[0]
            assert args[0] == iid
            assert args[1] == "5511999990000"
            assert args[2]["name"] == "Lead A"
        finally:
            with app_module.conversations_lock:
                app_module.conversations.pop(iid, None)

    def test_save_conversation_falha_agenda_retry(self):
        import main as app_module
        from unittest.mock import patch
        iid = "inst-retry-test"
        with app_module.conversations_lock:
            app_module.conversations[iid] = {
                "5511999990000": {"name": "Lead A", "messages": [], "last_activity": "2026-01-01T00:00:00"},
            }
        try:
            with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=True), \
                 patch.object(app_module.conversation_pg_service, "upsert_conversation",
                              return_value=(False, "pg down")):
                app_module._save_conversation(iid, "5511999990000")
            with app_module._dirty_convs_lock:
                assert (iid, "5511999990000") in app_module._dirty_convs

            # O flush retenta e, com sucesso, esvazia a fila
            with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=True), \
                 patch.object(app_module.conversation_pg_service, "upsert_conversation",
                              return_value=(True, "")) as mock_up:
                app_module._flush_dirty_conversations()
            mock_up.assert_called_once()
            with app_module._dirty_convs_lock:
                assert not app_module._dirty_convs
        finally:
            with app_module.conversations_lock:
                app_module.conversations.pop(iid, None)
            with app_module._dirty_convs_lock:
                app_module._dirty_convs.clear()

    def test_save_instance_config_persiste_somente_a_instancia_alvo(self):
        import main as app_module
        from unittest.mock import patch
        iid = "inst-cfg-test"
        with app_module.instances_lock:
            app_module.instances[iid] = app_module._make_instance("http://evo:4000", "k", "", "prompt")
        try:
            with patch.object(app_module.instances_pg_service, "pg_configured", return_value=True), \
                 patch.object(app_module.instances_pg_service, "upsert_instance",
                              return_value=(True, "")) as mock_up:
                app_module._save_instance_config(iid)
            mock_up.assert_called_once()
            args = mock_up.call_args[0]
            assert args[0] == iid
            assert args[1]["api_url"] == "http://evo:4000"
        finally:
            with app_module.instances_lock:
                app_module.instances.pop(iid, None)

    def test_save_instance_config_falha_agenda_retry(self):
        import main as app_module
        from unittest.mock import patch
        iid = "inst-cfg-retry"
        with app_module.instances_lock:
            app_module.instances[iid] = app_module._make_instance("http://evo:4000", "k", "", "prompt")
        try:
            with patch.object(app_module.instances_pg_service, "pg_configured", return_value=True), \
                 patch.object(app_module.instances_pg_service, "upsert_instance",
                              return_value=(False, "pg down")):
                app_module._save_instance_config(iid)
            with app_module._dirty_instances_lock:
                assert iid in app_module._dirty_instances

            with patch.object(app_module.instances_pg_service, "pg_configured", return_value=True), \
                 patch.object(app_module.instances_pg_service, "upsert_instance",
                              return_value=(True, "")) as mock_up:
                app_module._flush_dirty_instances()
            mock_up.assert_called_once()
            with app_module._dirty_instances_lock:
                assert not app_module._dirty_instances
        finally:
            with app_module.instances_lock:
                app_module.instances.pop(iid, None)
            with app_module._dirty_instances_lock:
                app_module._dirty_instances.clear()

    def test_save_conversation_inexistente_nao_grava(self):
        import main as app_module
        from unittest.mock import patch
        with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=True), \
             patch.object(app_module.conversation_pg_service, "upsert_conversation") as mock_up:
            app_module._save_conversation("inst-que-nao-existe", "5511999990000")
        mock_up.assert_not_called()

    def test_init_conversations_falha_pg_aborta_startup(self):
        import main as app_module
        import pytest
        from unittest.mock import patch
        with patch.object(app_module.conversation_pg_service, "pg_configured", return_value=True), \
             patch.object(app_module.conversation_pg_service, "load_snapshot", side_effect=OSError("pg down")):
            with pytest.raises(RuntimeError, match="abortando"):
                app_module.init_conversations_storage()

    def test_init_instances_falha_pg_aborta_startup(self):
        import main as app_module
        import pytest
        from unittest.mock import patch
        with patch.object(app_module.instances_pg_service, "pg_configured", return_value=True), \
             patch.object(app_module.instances_pg_service, "load_all", side_effect=OSError("pg down")):
            with pytest.raises(RuntimeError, match="abortando"):
                app_module.init_instances_storage()

    def test_load_users_falha_pg_aborta_startup(self):
        import services.auth_service as auth_svc
        import pytest
        from unittest.mock import patch
        with patch.object(auth_svc.settings_pg_service, "pg_configured", return_value=True), \
             patch.object(auth_svc.settings_pg_service, "load_setting", return_value=(False, "connection refused")):
            with pytest.raises(RuntimeError, match="abortando"):
                auth_svc.load_users()

    def test_load_assignments_falha_pg_aborta_startup(self):
        import services.assignment_service as assign_svc
        import pytest
        from unittest.mock import patch
        with patch.object(assign_svc.settings_pg_service, "pg_configured", return_value=True), \
             patch.object(assign_svc.settings_pg_service, "load_setting", return_value=(False, "timeout")):
            with pytest.raises(RuntimeError, match="abortando"):
                assign_svc.load_assignments()

    def test_load_assignments_chave_ausente_e_instalacao_nova(self):
        import services.assignment_service as assign_svc
        from unittest.mock import patch
        with patch.object(assign_svc.settings_pg_service, "pg_configured", return_value=True), \
             patch.object(assign_svc.settings_pg_service, "load_setting",
                          return_value=(False, "chave 'assignments' não encontrada")):
            assign_svc.load_assignments()
        assert assign_svc._state == {"tenants": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# health endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_health_returns_ok(self):
        import main as app_module
        from starlette.testclient import TestClient
        from unittest.mock import patch

        # /health usa ping() barato (SELECT 1) — ensure_schema roda só no startup
        with patch.object(app_module.instances_pg_service, "ping", return_value=(True, "")):
            client = TestClient(app_module.app)
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["postgres"] is True
        assert "instances" in body

    def test_health_reports_postgres_down(self):
        import main as app_module
        from starlette.testclient import TestClient

        with patch.object(
            app_module.instances_pg_service,
            "ping",
            return_value=(False, "connection refused"),
        ):
            client = TestClient(app_module.app)
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["postgres"] is False
        assert "connection refused" in body["postgres_detail"]


# ═══════════════════════════════════════════════════════════════════════════════
# pg_pool — devolução de conexão ao pool em caminho de erro (rede de segurança)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPgPoolProxyRelease:
    def _make_proxy(self):
        from services.pg_pool import _PooledConnProxy
        pool = MagicMock()
        raw = MagicMock()
        raw.closed = 0
        return _PooledConnProxy(pool, raw), pool, raw

    def test_del_releases_unclosed_connection(self):
        """Módulos legados retornam no except sem close() — o __del__ devolve ao pool."""
        import gc
        proxy, pool, raw = self._make_proxy()
        del proxy  # variável sai de escopo sem close(), como num caminho de erro
        gc.collect()
        pool.putconn.assert_called_once_with(raw)

    def test_close_then_del_releases_only_once(self):
        """close() explícito + __del__ não devolvem a mesma conexão duas vezes."""
        import gc
        proxy, pool, raw = self._make_proxy()
        proxy.close()
        del proxy
        gc.collect()
        assert pool.putconn.call_count == 1

    def test_close_rolls_back_open_transaction(self):
        """Transação abortada não pode voltar ao pool sem rollback."""
        proxy, pool, raw = self._make_proxy()
        proxy.close()
        raw.rollback.assert_called_once()
        pool.putconn.assert_called_once_with(raw)
