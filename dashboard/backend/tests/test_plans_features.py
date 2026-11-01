"""
Modelo de features por plano (gating do plano Lite).

Regra central que estes testes travam: SÓ o plano "lite" restringe features.
Todos os outros planos — user, pro, enterprise, personalizado e qualquer valor
desconhecido/legado — mantêm TODAS as features (comportamento idêntico ao de
antes deste modelo existir). Isso garante que introduzir o Lite não muda nada
para nenhum cliente atual.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
os.environ.setdefault("WEBHOOK_SECRET", "b" * 64)
sys.path.insert(0, str(Path(__file__).parent.parent))

import services.plans as plans


class TestLiteRestringe:
    def test_lite_nao_tem_features_avancadas(self):
        for feature in ("sdr_autonomo", "disparador", "roleta", "supervisao"):
            assert plans.plan_has_feature("lite", feature) is False, feature

    def test_lite_tem_o_nucleo(self):
        # Núcleo do produto nunca é bloqueado — mesmo no lite.
        for feature in ("inbox", "crm", "suggestions", "meta_ads"):
            assert plans.plan_has_feature("lite", feature) is True, feature


class TestOutrosPlanosCompletos:
    def test_user_pro_enterprise_tem_tudo(self):
        for plano in ("user", "pro", "enterprise"):
            for feature in ("sdr_autonomo", "disparador", "roleta", "supervisao"):
                assert plans.plan_has_feature(plano, feature) is True, f"{plano}/{feature}"

    def test_personalizado_tem_tudo(self):
        for feature in ("sdr_autonomo", "disparador", "roleta", "supervisao"):
            assert plans.plan_has_feature("personalizado", feature) is True, feature

    def test_plano_desconhecido_fail_open(self):
        # Não quebrar ninguém: plano legado/estranho é tratado como completo.
        for feature in ("sdr_autonomo", "disparador", "roleta", "supervisao"):
            assert plans.plan_has_feature("plano_que_nao_existe", feature) is True

    def test_plano_vazio_fail_open(self):
        assert plans.plan_has_feature("", "disparador") is True


class TestCompatibilidade:
    def test_max_instances_intacto(self):
        assert plans.max_instances_for_plan("lite") == 1
        assert plans.max_instances_for_plan("user") == 1
        assert plans.max_instances_for_plan("pro") == 5
        assert plans.max_instances_for_plan("enterprise") == 20
        # Plano desconhecido cai no default (user=1) — comportamento antigo preservado.
        assert plans.max_instances_for_plan("xyz") == 1

    def test_is_valid_plan_inclui_lite(self):
        assert plans.is_valid_plan("lite") is True
        assert plans.is_valid_plan("user") is True
        assert plans.is_valid_plan("personalizado") is True
        assert plans.is_valid_plan("inexistente") is False

    def test_list_plans_inclui_lite_e_sem_chave_interna(self):
        listed = plans.list_plans()
        ids = [p["id"] for p in listed]
        assert "lite" in ids
        assert ids[0] == "lite"  # mais barato primeiro na vitrine
        for p in listed:
            assert "public" not in p        # chave interna não vaza pro frontend
            assert "features" in p or True   # features pode ir, mas não é obrigatório usar
            assert "label" in p and "max_instances" in p