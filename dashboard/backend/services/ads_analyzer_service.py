"""
Serviço de análise de campanhas Meta Ads via LLM.

Etapa 1: análise em chamada única (single-shot).
Etapa 2 (futura): agente com ferramentas — busca de benchmarks, comparação histórica,
                  sugestões de segmentação, análise de criativos por URL.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests as _requests

logger = logging.getLogger("klead.ads_analyzer")

# ── Benchmarks e conhecimento embutido ────────────────────────────────────────

_SYSTEM_PROMPT = """
Você é um especialista sênior em Meta Ads (Facebook/Instagram Ads) com mais de 10 anos
de experiência gerenciando contas de médio e grande porte no Brasil.
Sua função é analisar métricas de campanhas e diagnosticar com precisão o que precisa
de atenção, separando problemas de criativo, audiência e orçamento.

## BENCHMARKS META ADS — BRASIL

### Frequência (impressões / alcance)
- 1.0–1.5: Excelente — audiência nova e engajada
- 1.5–3.0: Saudável — ideal para a maioria dos objetivos
- 3.0–4.0: Atenção — início de fadiga criativa
- >4.0:    Crítico — fadiga confirmada, troque o criativo ou expanda a audiência

### CTR (Click-Through Rate)
- Awareness / Alcance:  >0.5% bom | <0.2% ruim
- Tráfego / Lead Ads:   >1.0% bom | <0.5% preocupante | <0.2% crítico
- Geração de leads:     >1.5% bom | <0.8% revisar | <0.3% crítico
- Conversão (compra):   >2.0% excelente | >1.0% bom | <0.5% revisar

### CPM — Brasil (custo por mil impressões)
- <R$ 10:    Muito baixo (audiência grande, pouco competitiva)
- R$ 10–25:  Bom
- R$ 25–50:  Atenção (considere ampliar a audiência)
- R$ 50–100: Alto (audiência estreita ou mercado competitivo)
- >R$ 100:   Crítico — revise o targeting imediatamente

### Quality Ranking
- ABOVE_AVERAGE:        Criativo relevante e de alta qualidade ✓
- AVERAGE:              Normal, mas há espaço para melhoria
- BELOW_AVERAGE_10%:    Revisar criativo e CTA com urgência
- BELOW_AVERAGE_20%:    Problema sério — pause e recrie
- BELOW_AVERAGE_35%:    Crítico — pause imediatamente

### Diagnóstico cruzado Frequência × CTR
- Alta freq + CTR caindo:   Fadiga criativa → mude o criativo
- Alta freq + CTR estável:  Audiência engajada → escale com cuidado
- Baixa freq + CTR baixo:   Problema no criativo, não na audiência
- Baixa freq + CTR ok:      Campanha saudável → escale o orçamento

### Orçamento mínimo para aprendizado
- Meta recomenda ≥ 50 conversões/semana por conjunto de anúncios
- Orçamento mínimo prático: R$ 30–50/dia por campanha de leads
- Campanhas com orçamento abaixo do mínimo ficam em "aprendizado" crônico

## COMO IDENTIFICAR A CAUSA RAIZ

PROBLEMA NO CRIATIVO:
- Quality ranking BELOW_AVERAGE
- CTR baixo + frequência alta (veem mas não clicam)
- CTR baixo + frequência baixa (primeira impressão não converte)

PROBLEMA NA AUDIÊNCIA / SEGMENTAÇÃO:
- CPM muito alto (audiência estreita ou superlotada)
- Alcance baixo vs. orçamento gasto
- CTR ok mas poucos leads (clicam mas não preenchem)
- Frequência subindo rápido (audiência pequena)

PROBLEMA NO ORÇAMENTO:
- Orçamento diário < R$ 30 (abaixo do mínimo de aprendizado)
- Muitas campanhas com pouco recurso cada (dispersão)
- Spend alto mas CPL altíssimo (falta de otimização)

## FORMATO DE RESPOSTA

Retorne SOMENTE JSON válido, sem texto fora do JSON:
{
  "status_geral": "bom|atencao|critico",
  "resumo": "2-3 frases descrevendo o estado geral",
  "alertas": [
    {
      "nivel": "critico|atencao|ok",
      "categoria": "criativo|audiencia|orcamento|desempenho",
      "titulo": "título curto do alerta",
      "detalhe": "explicação com dados concretos"
    }
  ],
  "analise": {
    "criativo": {
      "status": "bom|atencao|critico",
      "diagnostico": "análise do criativo",
      "acoes": ["ação 1", "ação 2"]
    },
    "audiencia": {
      "status": "bom|atencao|critico",
      "diagnostico": "análise da audiência e segmentação",
      "acoes": ["ação 1", "ação 2"]
    },
    "orcamento": {
      "status": "bom|atencao|critico",
      "diagnostico": "análise do orçamento e distribuição",
      "acoes": ["ação 1", "ação 2"]
    }
  },
  "proximas_acoes": [
    "1. ação prioritária",
    "2. ação secundária",
    "3. ação de médio prazo"
  ],
  "por_campanha": [
    {
      "nome": "nome da campanha",
      "status": "bom|atencao|critico",
      "acao_prioritaria": "o que fazer primeiro nesta campanha"
    }
  ]
}
"""


def _build_metrics_text(metrics: dict[str, Any]) -> str:
    """Formata as métricas em texto estruturado para o prompt."""
    lines = [
        "=== DADOS CONTA (últimos 30 dias) ===",
        f"Gasto total: R$ {metrics.get('total_spend', 0):.2f}",
        f"Leads gerados: {metrics.get('total_leads', 0)}",
        f"Alcance total: {metrics.get('total_reach', 0):,}".replace(",", "."),
        f"CPL médio: {'R$ ' + str(round(metrics.get('avg_cpl', 0) or 0, 2)) if metrics.get('avg_cpl') else '—'}",
        "",
        "=== CAMPANHAS ===",
    ]

    for c in metrics.get("campaigns", []):
        freq = c.get("frequency", 0)
        ctr = (c.get("clicks", 0) / c.get("impressions", 1) * 100) if c.get("impressions") else 0
        quality = c.get("quality_ranking") or "N/A"
        budget = f"R$ {c['daily_budget']:.2f}/dia" if c.get("daily_budget") else "—"

        lines.append(f"\nCampanha: {c.get('campaign', '?')}")
        lines.append(f"  Impressões: {c.get('impressions', 0):,} | Alcance: {c.get('reach', 0):,} | Frequência: {freq:.2f}x".replace(",", "."))
        lines.append(f"  CTR: {ctr:.2f}% | CPM: R$ {c.get('cpm', 0):.2f} | CPC: R$ {c.get('cpc', 0):.2f}")
        lines.append(f"  Leads: {c.get('leads', 0)} | CPL: {'R$ ' + str(round(c['cpl'], 2)) if c.get('cpl') else '—'}")
        lines.append(f"  Gasto: R$ {c.get('spend', 0):.2f} | Orç/dia: {budget}")
        lines.append(f"  Quality ranking: {quality}")

    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Extrai JSON de texto que pode conter markdown."""
    text = text.strip()
    # Remove ```json ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


def analyze_meta_ads(metrics: dict[str, Any], ai_settings: dict[str, Any]) -> dict[str, Any]:
    """
    Analisa métricas Meta Ads usando LLM.

    Parâmetros:
        metrics: retorno de get_meta_ads_metrics()
        ai_settings: retorno de _resolve_ai_settings()

    Retorna análise estruturada.

    Etapa 2: adicionar tools=[get_benchmark, compare_industry, suggest_audience]
             permitindo que o agente consulte informações adicionais antes de responder.
    """
    if not ai_settings.get("api_key"):
        raise ValueError("api_key_missing")

    provider = ai_settings.get("provider", "groq")
    model = ai_settings.get("model", "openai/gpt-oss-120b")
    api_key = ai_settings["api_key"]

    metrics_text = _build_metrics_text(metrics)
    user_message = (
        "Analise as seguintes métricas de campanhas Meta Ads e identifique o que está "
        "bem, o que precisa de atenção e o que está crítico. Dê recomendações acionáveis "
        "e específicas com base nos dados.\n\n"
        f"{metrics_text}"
    )

    # OpenAI ou Groq
    from openai import OpenAI
    from services.openai_service import create_chat_completion
    is_groq = provider == "groq"
    kwargs = {"base_url": "https://api.groq.com/openai/v1"} if is_groq else {}
    client = OpenAI(api_key=api_key, timeout=60, max_retries=1, **kwargs)

    try:
        resp = create_chat_completion(
            client, is_groq, tokens=1500,
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    except Exception:
        # Fallback sem json_object (alguns modelos Groq/legados não suportam)
        resp = create_chat_completion(
            client, is_groq, tokens=1500,
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )

    return _extract_json(resp.choices[0].message.content)


# ── Sistema de chat com o agente Meta Ads ─────────────────────────────────────

_CHAT_SYSTEM_PREFIX = """
Você é um especialista sênior em Meta Ads (Facebook/Instagram Ads) consultando
diretamente o gestor de marketing. Responda de forma direta, prática e baseada
nos dados reais da conta abaixo.

REGRAS:
- Seja conciso: máximo 4-6 linhas por resposta (exceto se pedir explicação detalhada)
- Use os dados reais da conta para embasar cada resposta
- Se a pergunta não tiver relação com os dados, diga claramente
- Nunca invente métricas que não estão nos dados
- Use R$ para valores monetários
- Se não houver dados de comparativo semanal, informe que o histórico ainda está sendo acumulado

DADOS DA CONTA (últimos 30 dias):
{metrics_text}

BENCHMARKS RÁPIDOS:
- Frequência saudável: 1.5-3x | Atenção: >3.5x | Crítico: >4.5x
- CTR leads: >1.0% bom | <0.5% revisar
- CPM Brasil: <R$25 bom | >R$50 alto
- Quality ranking: ABOVE=ótimo | AVERAGE=ok | BELOW=problema
"""


def fetch_pixel_quality(pixel_id: str, access_token: str) -> dict[str, Any]:
    """
    Busca dados de qualidade do pixel/dataset CAPI via Graph API.
    Retorna dict com diagnostics, stats e matched_signals (fail open — erros retornam {}).
    """
    if not pixel_id or not access_token:
        return {}

    result: dict[str, Any] = {}

    # 1. Diagnósticos — erros e avisos dos eventos enviados
    try:
        r = _requests.get(
            f"https://graph.facebook.com/v25.0/{pixel_id}/diagnostics",
            params={
                "fields": "type,severity,description,action_required,diagnostics_group",
                "access_token": access_token,
            },
            timeout=10,
        )
        if r.status_code == 200:
            result["diagnostics"] = r.json().get("data", [])
    except Exception as exc:
        logger.debug(f"[PIXEL] diagnostics falhou: {exc}")

    # 2. Stats — eventos recebidos, matched, não matched (últimos 7 dias)
    try:
        r = _requests.get(
            f"https://graph.facebook.com/v25.0/{pixel_id}/stats",
            params={
                "fields": "event_name,event_count,event_match_estimate_rate",
                "start_time": "7_days_ago",
                "access_token": access_token,
            },
            timeout=10,
        )
        if r.status_code == 200:
            result["stats"] = r.json().get("data", [])
    except Exception as exc:
        logger.debug(f"[PIXEL] stats falhou: {exc}")

    return result


def _build_pixel_quality_text(pixel_data: dict[str, Any]) -> str:
    """Formata dados de qualidade do pixel CAPI para o contexto do agente."""
    if not pixel_data:
        return ""

    lines = ["", "=== QUALIDADE DOS EVENTOS CAPI (pixel/dataset) ==="]

    # Stats por evento
    stats = pixel_data.get("stats", [])
    if stats:
        lines.append("Eventos recebidos (últimos 7 dias):")
        for s in stats:
            name  = s.get("event_name", "?")
            count = s.get("event_count", 0)
            rate  = s.get("event_match_estimate_rate")
            rate_str = f" | Match rate: {round(float(rate)*100, 1)}%" if rate is not None else ""
            lines.append(f"  {name}: {count} eventos{rate_str}")
    else:
        lines.append("Nenhum dado de eventos disponível via API.")

    # Diagnósticos
    diags = pixel_data.get("diagnostics", [])
    if diags:
        lines.append("Diagnósticos:")
        for d in diags[:5]:  # máximo 5 para não inflar o contexto
            severity = d.get("severity", "").upper()
            desc     = d.get("description", "")
            action   = d.get("action_required", "")
            lines.append(f"  [{severity}] {desc}")
            if action:
                lines.append(f"    → Ação: {action}")
    else:
        lines.append("Nenhum diagnóstico de problema encontrado.")

    lines.append(
        "Nota: Event Match Quality (EMQ) completo só está disponível no Gerenciador de Eventos da Meta."
    )
    return "\n".join(lines)


def _build_comparison_text(comparison: dict[str, Any]) -> str:
    """Formata comparativo semanal para o contexto do agente."""
    if not comparison:
        return ""
    tw = comparison.get("this_week", {})
    lw = comparison.get("last_week", {})
    d  = comparison.get("delta_pct", {})
    if not tw:
        return "\n=== COMPARATIVO SEMANAL ===\nHistórico ainda sendo acumulado (menos de 7 dias de dados)."

    def pct(val):
        if val is None:
            return "sem dado anterior"
        arrow = "↑" if val > 0 else "↓"
        return f"{arrow} {abs(val):.1f}% vs. semana passada"

    lines = [
        "",
        "=== COMPARATIVO SEMANAL (esta semana vs. semana passada) ===",
        f"Leads:   {tw.get('leads', 0)} ({pct(d.get('leads'))}) | semana passada: {lw.get('leads', '—')}",
        f"CPL:     R$ {tw.get('cpl') or 0:.2f} ({pct(d.get('cpl'))}) | semana passada: R$ {lw.get('cpl') or 0:.2f}",
        f"Spend:   R$ {tw.get('spend', 0):.2f} ({pct(d.get('spend'))}) | semana passada: R$ {lw.get('spend', 0):.2f}",
        f"CTR:     {tw.get('ctr', 0):.2f}% ({pct(d.get('ctr'))}) | semana passada: {lw.get('ctr', 0):.2f}%",
        f"CPM:     R$ {tw.get('cpm', 0):.2f} ({pct(d.get('cpm'))}) | semana passada: R$ {lw.get('cpm', 0):.2f}",
        f"Cliques: {tw.get('clicks', 0)} ({pct(d.get('clicks'))}) | semana passada: {lw.get('clicks', '—')}",
    ]
    return "\n".join(lines)


def _build_ads_text(ads: list[dict[str, Any]]) -> str:
    """Formata dados de anúncios individuais para o contexto do agente."""
    if not ads:
        return ""
    lines = ["", "=== ANÚNCIOS INDIVIDUAIS (top 20, últimos 30 dias) ==="]
    for i, a in enumerate(ads[:20], 1):
        cpl_str = f"R$ {a['cpl']:.2f}" if a.get("cpl") else "—"
        quality = a.get("quality_ranking") or "N/A"
        title   = a.get("creative_title") or ""
        body    = a.get("creative_body") or ""
        ctype   = a.get("creative_type", "unknown")
        tipo    = "Vídeo" if ctype == "video" else "Imagem" if ctype == "image" else "Criativo"
        criativo = f" | Título: {title}" if title else ""
        criativo += f" | Texto: {body[:60]}..." if body else ""
        lines.append(
            f"\n#{i} [{tipo}] {a.get('ad_name', '?')} [{a.get('campaign_name', '?')}]"
            f"\n  Leads: {a['leads']} | CTR: {a.get('ctr', 0):.2f}% | CPL: {cpl_str}"
            f"\n  Gasto: R$ {a['spend']:.2f} | CPM: R$ {a.get('cpm', 0):.2f} | Freq: {a.get('frequency', 0):.1f}x"
            f"\n  Quality: {quality}{criativo}"
        )
    return "\n".join(lines)


def chat_meta_ads(
    messages: list[dict[str, Any]],
    metrics: dict[str, Any],
    ai_settings: dict[str, Any],
    ads: list[dict[str, Any]] | None = None,
    comparison: dict[str, Any] | None = None,
    pixel_data: dict[str, Any] | None = None,
) -> str:
    """
    Responde perguntas sobre as métricas Meta Ads em formato conversacional.

    Parâmetros:
        messages:    histórico [{"role": "user"|"assistant", "content": str}]
        metrics:     retorno de _fetch_campaign_metrics()
        ai_settings: retorno de _resolve_ai_settings()
        ads:         opcional — retorno de get_meta_ads_by_ad() para contexto de criativos

    Retorna: string com a resposta do agente (não JSON).
    """
    if not ai_settings.get("api_key"):
        raise ValueError("api_key_missing")

    provider  = ai_settings.get("provider", "groq")
    model     = ai_settings.get("model", "openai/gpt-oss-120b")
    api_key   = ai_settings["api_key"]

    ads_text        = _build_ads_text(ads) if ads else ""
    comparison_text = _build_comparison_text(comparison) if comparison else ""
    pixel_text      = _build_pixel_quality_text(pixel_data) if pixel_data else ""
    system          = _CHAT_SYSTEM_PREFIX.format(metrics_text=_build_metrics_text(metrics) + ads_text + comparison_text + pixel_text)

    from openai import OpenAI
    from services.openai_service import create_chat_completion
    is_groq = provider == "groq"
    kwargs = {"base_url": "https://api.groq.com/openai/v1"} if is_groq else {}
    client = OpenAI(api_key=api_key, timeout=60, max_retries=1, **kwargs)
    resp = create_chat_completion(
        client, is_groq, tokens=600,
        model=model,
        messages=[{"role": "system", "content": system}] + messages,
        temperature=0.5,
    )
    return resp.choices[0].message.content.strip()
