"""
Testes dos caminhos otimizados para escala (auditoria de 500 instâncias):
cache da base de conhecimento e dedupe de follow-ups em andamento.
"""

import os
import sys
import time
from pathlib import Path

# Setup de ambiente minimo para importar os modulos (mesmo padrao dos demais testes)
os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)
sys.path.insert(0, str(Path(__file__).parent.parent))

import main as app_module  # noqa: E402


class TestKnowledgeCache:
    def _kb_dir(self, iid):
        d = app_module.KNOWLEDGE_DIR / iid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cleanup(self, iid):
        d = app_module.KNOWLEDGE_DIR / iid
        if d.exists():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        with app_module._knowledge_cache_lock:
            app_module._knowledge_cache.pop(iid, None)

    def test_cache_hit_nao_rele_arquivos(self):
        iid = "kbcache1"
        d = self._kb_dir(iid)
        try:
            (d / "base.txt").write_text("conteudo original", encoding="utf-8")
            first = app_module._load_knowledge(iid)
            assert "conteudo original" in first
            with app_module._knowledge_cache_lock:
                assert iid in app_module._knowledge_cache
            # Segunda chamada sem mudança nos arquivos retorna o mesmo objeto do cache
            second = app_module._load_knowledge(iid)
            assert second is first
        finally:
            self._cleanup(iid)

    def test_mudanca_de_arquivo_invalida_cache(self):
        iid = "kbcache2"
        d = self._kb_dir(iid)
        try:
            (d / "base.txt").write_text("versao um", encoding="utf-8")
            assert "versao um" in app_module._load_knowledge(iid)
            time.sleep(0.01)  # garante mtime_ns diferente
            (d / "base.txt").write_text("versao dois", encoding="utf-8")
            assert "versao dois" in app_module._load_knowledge(iid)
        finally:
            self._cleanup(iid)

    def test_diretorio_removido_limpa_cache(self):
        iid = "kbcache3"
        d = self._kb_dir(iid)
        try:
            (d / "base.txt").write_text("algo", encoding="utf-8")
            app_module._load_knowledge(iid)
            (d / "base.txt").unlink()
            d.rmdir()
            assert app_module._load_knowledge(iid) == ""
            with app_module._knowledge_cache_lock:
                assert iid not in app_module._knowledge_cache
        finally:
            self._cleanup(iid)


class TestFollowupInFlight:
    def test_run_followup_sempre_libera_o_slot(self):
        from unittest.mock import patch

        fu_key = "inst-x:5511999990000"
        with app_module._followups_in_flight_lock:
            app_module._followups_in_flight.add(fu_key)
        with patch.object(app_module, "_send_followup", side_effect=RuntimeError("boom")):
            try:
                app_module._run_followup("inst-x", "5511999990000", {}, {}, fu_key)
            except RuntimeError:
                pass
        with app_module._followups_in_flight_lock:
            assert fu_key not in app_module._followups_in_flight
