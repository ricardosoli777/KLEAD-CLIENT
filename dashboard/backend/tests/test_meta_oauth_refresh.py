"""
Testes da renovação proativa do token OAuth da Meta.

Cobre a seleção de conexões vinculadas, a extensão efetiva da validade e
a reconexão obrigatória quando a troca não estende o prazo ou o token vence.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestListConnectionsExpiringSoon:
    def _make_mock_conn(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_filtra_por_janela_de_dias(self):
        from services import meta_oauth_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.fetchall.return_value = [
            ("conn-1", "user@x.com", "encrypted-token", None),
        ]
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            items = svc.list_connections_expiring_soon(7)

        assert len(items) == 1
        assert items[0]["user_email"] == "user@x.com"
        sql, params = cur.execute.call_args[0]
        assert "token_expires_at < now() + (%s * interval" in sql
        assert "AND EXISTS" in sql
        assert "ima.meta_connection_id = meta_connections.id" in sql
        assert params == (7,)

    def test_erro_de_conexao_retorna_lista_vazia(self):
        from services import meta_oauth_pg_service as svc

        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", side_effect=OSError("pg down")):
            items = svc.list_connections_expiring_soon(7)

        assert items == []


class TestUpdateConnectionToken:
    def _make_mock_conn(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_atualiza_token_e_expiracao(self):
        from services import meta_oauth_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.rowcount = 1
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, _ = svc.update_connection_token("conn-1", "novo-token-encrypted", None)

        assert ok is True
        sql, params = cur.execute.call_args[0]
        assert "UPDATE meta_connections SET access_token_encrypted" in sql
        assert params[0] == "novo-token-encrypted"
        assert params[2] == "conn-1"

    def test_conexao_nao_encontrada_retorna_false(self):
        from services import meta_oauth_pg_service as svc

        conn, cur = self._make_mock_conn()
        cur.rowcount = 0
        with patch.object(svc, "ensure_schema", return_value=(True, "")), \
             patch.object(svc, "_connect", return_value=conn):
            ok, detail = svc.update_connection_token("conn-inexistente", "token", None)

        assert ok is False
        assert detail == "connection_not_found_or_inactive"


class TestRefreshMetaOauthTokensTick:
    @pytest.mark.parametrize("expires_in", [172800, 172810, 0, -1])
    def test_troca_sem_extensao_nao_registra_renovacao(self, expires_in):
        import main as m

        expires_at = datetime.now(timezone.utc) + timedelta(days=2)
        expiring = [{"id": "conn-1", "user_email": "user@x.com",
                     "access_token_encrypted": "enc-old", "token_expires_at": expires_at.isoformat()}]
        with patch.object(m, "_META_OAUTH_APP_ID", "app-id"), \
             patch.object(m, "_META_OAUTH_APP_SECRET", "app-secret"), \
             patch.object(m.meta_oauth_pg_service, "list_connections_expiring_soon", return_value=expiring), \
             patch.object(m, "_decrypt_field_for_user", return_value="old-token"), \
             patch.object(m.meta_oauth_service, "exchange_for_long_lived_token",
                          return_value={"ok": True, "access_token": "new-token", "expires_in": expires_in}), \
             patch.object(m.meta_oauth_pg_service, "update_connection_token") as update, \
             patch.object(m, "_append_alert") as alert, \
             patch.object(m.logger, "info") as log_info:
            m._refresh_meta_oauth_tokens_tick()

        update.assert_not_called()
        log_info.assert_not_called()
        alert.assert_called_once()
        assert "não confirmou extensão" in alert.call_args.args[1]

    def test_token_vencido_pede_reconexao_sem_chamar_meta(self):
        import main as m

        expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        expiring = [{"id": "conn-1", "user_email": "user@x.com",
                     "access_token_encrypted": "enc-old", "token_expires_at": expires_at.isoformat()}]
        with patch.object(m, "_META_OAUTH_APP_ID", "app-id"), \
             patch.object(m, "_META_OAUTH_APP_SECRET", "app-secret"), \
             patch.object(m.meta_oauth_pg_service, "list_connections_expiring_soon", return_value=expiring), \
             patch.object(m.meta_oauth_service, "exchange_for_long_lived_token") as exchange, \
             patch.object(m.meta_oauth_pg_service, "update_connection_token") as update, \
             patch.object(m, "_append_alert") as alert:
            m._refresh_meta_oauth_tokens_tick()

        exchange.assert_not_called()
        update.assert_not_called()
        alert.assert_called_once()
        assert alert.call_args.args[0] == "meta_oauth_token_expired"

    def test_sem_app_configurado_nao_faz_nada(self):
        import main as m

        with patch.object(m, "_META_OAUTH_APP_ID", ""), \
             patch.object(m, "meta_oauth_pg_service") as mock_pg:
            m._refresh_meta_oauth_tokens_tick()

        mock_pg.list_connections_expiring_soon.assert_not_called()

    def test_renovacao_bem_sucedida_atualiza_conexao(self):
        import main as m

        expiring = [{"id": "conn-1", "user_email": "user@x.com", "access_token_encrypted": "enc-old",
                     "token_expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}]
        with patch.object(m, "_META_OAUTH_APP_ID", "app-id"), \
             patch.object(m, "_META_OAUTH_APP_SECRET", "app-secret"), \
             patch.object(m.meta_oauth_pg_service, "list_connections_expiring_soon", return_value=expiring), \
             patch.object(m, "_decrypt_field_for_user", return_value="token-antigo"), \
             patch.object(m.meta_oauth_service, "exchange_for_long_lived_token",
                          return_value={"ok": True, "access_token": "token-novo", "expires_in": 5184000}), \
             patch.object(m, "_make_fernet_for_user", return_value="fake-fernet"), \
             patch.object(m, "_encrypt_field", return_value="enc-novo"), \
             patch.object(m.meta_oauth_pg_service, "update_connection_token", return_value=(True, "")) as mock_update, \
             patch.object(m, "_append_alert") as mock_alert:
            m._refresh_meta_oauth_tokens_tick()

        mock_update.assert_called_once()
        assert mock_update.call_args[0][0] == "conn-1"
        assert mock_update.call_args[0][1] == "enc-novo"
        mock_alert.assert_not_called()

    def test_renovacao_falha_dispara_alerta(self):
        import main as m

        expiring = [{"id": "conn-1", "user_email": "user@x.com", "access_token_encrypted": "enc-old", "token_expires_at": None}]
        with patch.object(m, "_META_OAUTH_APP_ID", "app-id"), \
             patch.object(m, "_META_OAUTH_APP_SECRET", "app-secret"), \
             patch.object(m.meta_oauth_pg_service, "list_connections_expiring_soon", return_value=expiring), \
             patch.object(m, "_decrypt_field_for_user", return_value="token-antigo"), \
             patch.object(m.meta_oauth_service, "exchange_for_long_lived_token",
                          return_value={"ok": False, "detail": "token expirado"}), \
             patch.object(m.meta_oauth_pg_service, "update_connection_token") as mock_update, \
             patch.object(m, "_append_alert") as mock_alert:
            m._refresh_meta_oauth_tokens_tick()

        mock_update.assert_not_called()
        mock_alert.assert_called_once()
        assert mock_alert.call_args[0][0] == "meta_oauth_token_expiring"
