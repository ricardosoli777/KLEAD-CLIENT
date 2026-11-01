"""
Testes da trilha de auditoria (audit_events).

O caso que motivou a feature: um lead foi excluído sem querer e não sobrou
rastro nenhum — a crm_lead_timeline cai por ON DELETE CASCADE junto com o lead.
A trilha vive numa tabela separada e guarda um snapshot do objeto excluído,
o que torna o registro restaurável.

Dois invariantes que não podem quebrar nunca:
  1. auditoria NÃO derruba a operação que ela registra (banco fora do ar,
     meta não serializável, request esquisito — tudo engolido);
  2. snapshot NÃO vaza segredo (hash de senha, segredo do 2FA).
"""
from unittest.mock import patch

import services.audit_log_service as audit_svc


class TestRecordEventNuncaQuebra:
    """Auditoria é best-effort: falhar em logar nunca pode falhar a ação."""

    def test_falha_no_banco_nao_levanta(self):
        with patch.object(audit_svc, "_ensure_schema", side_effect=Exception("PG caiu")):
            # Não levanta — só engole e loga.
            audit_svc.record_event(action="lead_deleted", target_id="x")

    def test_conexao_recusada_nao_levanta(self):
        with patch.object(audit_svc, "_ensure_schema", return_value=True), \
             patch.object(audit_svc.pg_pool, "pooled_connection", side_effect=OSError("conn refused")):
            audit_svc.record_event(action="user_deleted", target_id="a@b.com")

    def test_meta_nao_serializavel_nao_levanta(self):
        """Um objeto exótico no snapshot não pode explodir a exclusão do lead."""
        class Exotico:
            def __repr__(self):
                raise RuntimeError("nem repr funciona")

        capturado = {}

        class FakeCursor:
            def execute(self, sql, params):
                capturado["params"] = params
            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()
            def commit(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch.object(audit_svc, "_ensure_schema", return_value=True), \
             patch.object(audit_svc._svc_pg, "_pg_settings", return_value={}), \
             patch.object(audit_svc.pg_pool, "pooled_connection", return_value=FakeConn()):
            audit_svc.record_event(action="lead_deleted", target_id="l1", meta={"x": Exotico()})

        # Gravou mesmo assim, com um marcador no lugar do meta quebrado.
        assert capturado, "deveria ter tentado gravar"
        assert "não serializável" in capturado["params"][8]

    def test_action_vazia_e_ignorada(self):
        with patch.object(audit_svc, "_ensure_schema") as ensure:
            audit_svc.record_event(action="")
            ensure.assert_not_called()


class TestSnapshotRestauravel:
    """O ponto central: o objeto excluído tem que sobreviver no log."""

    def test_snapshot_do_lead_vai_inteiro_pro_meta(self):
        capturado = {}

        class FakeCursor:
            def execute(self, sql, params):
                capturado["params"] = params
            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()
            def commit(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        lead = {
            "id": "lead-123",
            "nome": "Sergio",
            "telefone": "+5511947383694",
            "instancia_id": "inst-1",
            "origem": "meta_form",
        }

        with patch.object(audit_svc, "_ensure_schema", return_value=True), \
             patch.object(audit_svc._svc_pg, "_pg_settings", return_value={}), \
             patch.object(audit_svc.pg_pool, "pooled_connection", return_value=FakeConn()):
            audit_svc.record_event(
                actor_email="dono@example.com", actor_funcao="superadmin",
                action="lead_deleted", target_type="lead", target_id="lead-123",
                instancia_id="inst-1", detail="Lead excluído: Sergio", meta=lead,
            )

        params = capturado["params"]
        meta_json = params[8]
        # Tudo que precisa pra restaurar o lead está no snapshot.
        assert "Sergio" in meta_json
        assert "+5511947383694" in meta_json
        assert "meta_form" in meta_json
        assert params[3] == "lead_deleted"
        assert params[5] == "lead-123"

    def test_detail_longo_e_truncado(self):
        """Campo detail é text mas truncamos em 2000 — não deixa um payload
        gigante entupir a tabela."""
        capturado = {}

        class FakeCursor:
            def execute(self, sql, params):
                capturado["params"] = params
            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()
            def commit(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch.object(audit_svc, "_ensure_schema", return_value=True), \
             patch.object(audit_svc._svc_pg, "_pg_settings", return_value={}), \
             patch.object(audit_svc.pg_pool, "pooled_connection", return_value=FakeConn()):
            audit_svc.record_event(action="lead_deleted", detail="x" * 5000)

        assert len(capturado["params"][7]) == 2000


class TestSegredoNaoVaza:
    """Snapshot de usuário excluído não pode carregar hash de senha nem 2FA."""

    def test_safe_user_remove_campos_sensiveis(self):
        import services.auth_service as auth_svc

        user = {
            "email": "alvo@x.com",
            "nome": "Alvo",
            "funcao": "admin",
            "password_hash": "$2b$12$hashsupersecreto",
            "totp_secret": "JBSWY3DPEHPK3PXP",
            "totp_backup_codes": ["abc123", "def456"],
        }
        safe = auth_svc._safe_user(user)

        assert "password_hash" not in safe
        assert "totp_secret" not in safe
        assert "totp_backup_codes" not in safe
        assert safe["email"] == "alvo@x.com"


class TestActorFromRequest:
    """Extração do ator nunca levanta, mesmo com request esquisito."""

    def test_request_sem_nada_devolve_vazio(self):
        class RequestPelado:
            pass

        email, funcao, ip = audit_svc.actor_from_request(RequestPelado())
        assert (email, funcao) == ("", "")

    def test_ip_vem_do_x_forwarded_for(self):
        """Atrás do Traefik/Cloudflare o IP real está no X-Forwarded-For."""
        class FakeRequest:
            headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
            cookies = {}

        with patch("services.auth_service.try_get_auth_user", return_value=None):
            _, _, ip = audit_svc.actor_from_request(FakeRequest())
        assert ip == "203.0.113.7"


class TestGetEventsFiltros:
    def test_filtros_viram_where_com_parametros_ligados(self):
        """SQL com parâmetros ligados (%s) — nunca interpolação de string."""
        capturado = {}

        class FakeCursor:
            def execute(self, sql, params):
                capturado["sql"] = sql
                capturado["params"] = params
            def fetchall(self):
                return []
            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch.object(audit_svc, "_ensure_schema", return_value=True), \
             patch.object(audit_svc._svc_pg, "_pg_settings", return_value={}), \
             patch.object(audit_svc.pg_pool, "pooled_connection", return_value=FakeConn()):
            audit_svc.get_events(actor_email="a@b.com", action="lead_deleted", limit=50)

        assert "WHERE actor_email = %s AND action = %s" in capturado["sql"]
        assert capturado["params"] == ("a@b.com", "lead_deleted", 50)

    def test_falha_de_leitura_devolve_lista_vazia(self):
        with patch.object(audit_svc, "_ensure_schema", side_effect=Exception("boom")):
            assert audit_svc.get_events() == []
