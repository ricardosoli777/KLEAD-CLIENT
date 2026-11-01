"""
plans.py — Definição dos planos de assinatura do KLEAD.

Fonte única de verdade pro limite de instâncias por plano e pras features que
cada plano libera. Hoje o superadmin atribui o plano na mão pra cada usuário;
quando o Stripe conectar, o webhook de pagamento aprovado chama
`services.auth_service.set_user_plan()` com o `price_id`/plano correspondente,
sem precisar mexer em mais nada.

Features (gating por plano):
  Só o plano "lite" RESTRINGE features. Todos os outros planos — user, pro,
  enterprise, personalizado e qualquer valor desconhecido/legado — têm TODAS as
  features (comportamento idêntico ao de antes deste modelo existir). Ou seja,
  adicionar este modelo não muda nada para nenhum usuário atual; só o plano lite,
  quando atribuído, passa a esconder/bloquear SDR autônomo, disparador, roleta e
  supervisão.
"""

# Features que PODEM ser restritas por plano. O núcleo (inbox, CRM, sugestões de
# resposta do co-piloto, credenciais e dashboard/análise de Meta Ads) está sempre
# disponível e nunca é bloqueado — por isso não aparece aqui.
GATED_FEATURES = ("sdr_autonomo", "disparador", "roleta", "supervisao")

# Conjunto completo de features restringíveis — o que todo plano "cheio" tem.
_ALL_GATED = list(GATED_FEATURES)

PLANS = {
    # Lite: inbox humano com sugestões de resposta (co-piloto) + CRM + dashboard/
    # análise de Meta Ads. SEM SDR autônomo, disparador, roleta e supervisão.
    # Vem primeiro (mais barato) na vitrine de planos. Tem quatro formas de
    # cobrança no Stripe: mensal recorrente (padrão), avulso 30 dias, semestral
    # e anual — ver stripe_service.PRICE_IDS.
    "lite": {
        "label": "Lite",
        "max_instances": 1,
        "features": [],  # nenhuma das features restringíveis
    },
    "user": {
        "label": "User",
        "max_instances": 1,
        "features": list(_ALL_GATED),
    },
    "pro": {
        "label": "Pro",
        "max_instances": 5,
        "features": list(_ALL_GATED),
    },
    # Enterprise saiu da vitrine pública (public=False): virou "sob consulta" —
    # a landing mostra um CTA de contato e o valor/limite é negociado caso a
    # caso. O plano segue válido pra atribuição manual pelo superadmin.
    "enterprise": {
        "label": "Enterprise",
        "max_instances": 20,
        "features": list(_ALL_GATED),
        "public": False,
    },
}

DEFAULT_PLAN = "user"

PERSONALIZADO = "personalizado"


def is_valid_plan(plano: str) -> bool:
    return plano in PLANS or plano == PERSONALIZADO


def max_instances_for_plan(plano: str, custom_max_instances: int | None = None) -> int:
    if plano == PERSONALIZADO:
        return custom_max_instances if custom_max_instances and custom_max_instances > 0 else PLANS[DEFAULT_PLAN]["max_instances"]
    return PLANS.get(plano, PLANS[DEFAULT_PLAN])["max_instances"]


def plan_has_feature(plano: str, feature: str) -> bool:
    """
    True se o plano libera a feature. Fail-open por design: qualquer plano que
    NÃO esteja explicitamente no dicionário PLANS (personalizado, legado,
    desconhecido) é tratado como completo — preserva o comportamento atual e
    não restringe ninguém por acidente. Só planos listados com `features`
    limitado (hoje, apenas "lite") de fato bloqueiam algo.
    """
    plan = PLANS.get(plano)
    if plan is None:
        return True  # personalizado / desconhecido → completo (não quebra ninguém)
    # Feature fora da lista de restringíveis nunca é bloqueada (núcleo do produto).
    if feature not in GATED_FEATURES:
        return True
    return feature in plan.get("features", [])


def list_plans() -> list[dict]:
    """Planos vendáveis para a vitrine pública. Planos com public=False (ex: lite
    sem preço configurado) ficam de fora até estarem prontos para venda."""
    return [
        {"id": key, **{k: v for k, v in value.items() if k != "public"}}
        for key, value in PLANS.items()
        if value.get("public", True)
    ]
