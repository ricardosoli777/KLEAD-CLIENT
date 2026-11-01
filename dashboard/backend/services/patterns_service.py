"""
patterns_service.py — Memória adaptativa do SDR.

Registra padrões de interação (o que o lead disse → intent → resultado)
e os injeta como contexto antes de cada resposta.

Totalmente fail-safe: qualquer erro silencia sem afetar o SDR.
Persistência em Postgres via klead_settings (key='patterns_{instance_id}').
"""

import logging
import time

from services import settings_pg_service
from utils.instance_id import is_safe_instance_id

logger = logging.getLogger("klead.patterns")

# Máximo de padrões por instância (evita crescimento infinito)
_MAX_PATTERNS = 200
# Quantos padrões injetar no knowledge (os mais relevantes)
_TOP_K = 5
def _safe_instance_id(instance_id: str) -> str:
    if not is_safe_instance_id(instance_id):
        raise ValueError("instance_id inválido")
    return instance_id


def _pg_key(instance_id: str) -> str:
    return f"patterns_{_safe_instance_id(instance_id)}"


def _load_patterns(instance_id: str) -> list:
    try:
        ok, data = settings_pg_service.load_setting(_pg_key(instance_id))
        if ok and isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_patterns(instance_id: str, patterns: list):
    try:
        settings_pg_service.save_setting(_pg_key(instance_id), patterns)
    except Exception as e:
        logger.warning(f"[patterns] Falha ao salvar padrões para {instance_id}: {e}")


def purge_patterns(instance_id: str):
    try:
        settings_pg_service.delete_setting(_pg_key(instance_id))
    except Exception:
        pass


def record_pattern(instance_id: str, lead_message: str, sdr_response: str, action: str, score: int, score_reason: str):
    """
    Registra um padrão de interação após cada resposta do SDR.
    action: reply | handoff | disqualify
    """
    try:
        patterns = _load_patterns(instance_id)

        outcome_map = {
            "handoff":    "lead_qualificado",
            "disqualify": "lead_descartado",
            "reply":      "conversa_continua",
        }
        outcome = outcome_map.get(action, "continua")

        pattern = {
            "lead_message":  lead_message[:300],
            "sdr_response":  sdr_response[:500],
            "action":        action,
            "score":         score,
            "score_reason":  score_reason,
            "outcome":       outcome,
            "timestamp":     int(time.time()),
        }

        patterns.append(pattern)

        if len(patterns) > _MAX_PATTERNS:
            patterns = patterns[-_MAX_PATTERNS:]

        _save_patterns(instance_id, patterns)
    except Exception:
        pass  # nunca quebra o SDR


def build_knowledge_from_patterns(instance_id: str) -> str:
    """
    Monta um bloco de texto com os padrões mais relevantes para injetar
    no knowledge do SDR antes de responder.
    Retorna string vazia se não houver padrões suficientes.
    """
    try:
        patterns = _load_patterns(instance_id)
        if len(patterns) < 3:
            return ""

        priority = {"lead_qualificado": 0, "lead_descartado": 1, "conversa_continua": 2}
        sorted_patterns = sorted(patterns, key=lambda p: (priority.get(p["outcome"], 2), -p["timestamp"]))

        top = sorted_patterns[:_TOP_K]

        lines = ["=== Padrões aprendidos com leads anteriores ==="]
        lines.append("Use esses exemplos como referência, mas NUNCA copie as respostas literalmente.\n")

        for i, p in enumerate(top, 1):
            lines.append(f"Exemplo {i}:")
            lines.append(f"  Lead disse: \"{p['lead_message']}\"")
            lines.append(f"  SDR respondeu: \"{p['sdr_response']}\"")
            lines.append(f"  Resultado: {p['outcome']} (score: {p['score']})")
            lines.append(f"  Motivo: {p['score_reason']}\n")

        return "\n".join(lines)
    except Exception:
        return ""
