"""
Testes para o responder_service refatorado.
Cobre: roteamento por provider (OpenAI/Groq), limite de turnos,
       API key ausente, parse de resultado.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.responder_service import (
    qualify_lead,
    generate_followup,
    _parse_tool_result,
    _extract_json,
    _build_system_prompt,
    _HANDOFF_FALLBACK,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

HISTORY = [{"role": "user", "content": "Olá, tenho interesse"}]
KNOWLEDGE = "Preço: R$497\nParcelamento: 12x\nGarantia: 30 dias"


# ═══════════════════════════════════════════════════════════════════════════════
# Limite de turnos
# ═══════════════════════════════════════════════════════════════════════════════

class TestTurnLimit:
    def test_returns_handoff_when_max_turns_reached(self):
        result = qualify_lead(
            openai_key="key", qualification_prompt="", history=HISTORY,
            turn_count=8, max_ai_turns=8,
        )
        assert result == _HANDOFF_FALLBACK

    def test_returns_handoff_when_over_limit(self):
        result = qualify_lead(
            openai_key="key", qualification_prompt="", history=HISTORY,
            turn_count=99, max_ai_turns=8,
        )
        assert result["action"] == "handoff"

    def test_proceeds_when_under_limit(self):
        with patch("services.responder_service._qualify_openai") as mock:
            mock.return_value = {"message": "Oi", "action": "reply", "score": 50, "score_reason": "ok"}
            result = qualify_lead(
                openai_key="key", qualification_prompt="", history=HISTORY,
                turn_count=3, max_ai_turns=8,
            )
        assert result["action"] == "reply"


# ═══════════════════════════════════════════════════════════════════════════════
# API key ausente
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiKeyMissing:
    def test_returns_handoff_when_no_key(self):
        result = qualify_lead(
            openai_key="", llm_api_key="", qualification_prompt="",
            history=HISTORY, turn_count=0, max_ai_turns=8,
        )
        assert result["action"] == "handoff"
        assert "API key" in result["score_reason"]

    def test_llm_api_key_takes_precedence_over_openai_key(self):
        with patch("services.responder_service._qualify_openai") as mock:
            mock.return_value = {"message": "Ok", "action": "reply", "score": 60, "score_reason": "ok"}
            qualify_lead(
                openai_key="old-key", llm_api_key="new-key",
                qualification_prompt="", history=HISTORY,
                turn_count=0, max_ai_turns=8,
            )
        args = mock.call_args
        assert args[0][0] == "new-key"  # api_key passada é a llm_api_key


# ═══════════════════════════════════════════════════════════════════════════════
# Roteamento por provider
# ═══════════════════════════════════════════════════════════════════════════════

class TestProviderRouting:
    def test_routes_to_openai(self):
        with patch("services.responder_service._qualify_openai") as mock:
            mock.return_value = {"message": "Oi", "action": "reply", "score": 50, "score_reason": "ok"}
            qualify_lead(
                openai_key="gpt-key", llm_provider="openai",
                model="gpt-4.1-mini", qualification_prompt="",
                history=HISTORY, turn_count=0, max_ai_turns=8,
            )
        mock.assert_called_once()

    def test_routes_groq_to_openai_fn(self):
        with patch("services.responder_service._qualify_openai") as mock:
            mock.return_value = {"message": "Ok", "action": "reply", "score": 40, "score_reason": "ok"}
            qualify_lead(
                openai_key="", llm_api_key="groq-key", llm_provider="groq",
                model="llama3-8b-8192", qualification_prompt="",
                history=HISTORY, turn_count=0, max_ai_turns=8,
            )
        mock.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_tool_result
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseResult:
    def test_valid_result(self):
        r = _parse_tool_result({"message": "Olá", "action": "handoff", "score": 80, "score_reason": "qualificado"})
        assert r == {"message": "Olá", "action": "handoff", "score": 80, "score_reason": "qualificado"}

    def test_score_clamped(self):
        r = _parse_tool_result({"message": "", "action": "reply", "score": -5, "score_reason": ""})
        assert r["score"] == 0

    def test_invalid_action_defaults(self):
        r = _parse_tool_result({"message": "", "action": "invalid", "score": 50, "score_reason": ""})
        assert r["action"] == "reply"


# ═══════════════════════════════════════════════════════════════════════════════
# _extract_json
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractJson:
    def test_clean_json(self):
        assert _extract_json('{"action": "reply"}') == {"action": "reply"}

    def test_json_with_surrounding_text(self):
        assert _extract_json('Aqui está: {"action": "reply"} fim') == {"action": "reply"}

    def test_invalid_returns_empty(self):
        assert _extract_json("não é json") == {}


# ═══════════════════════════════════════════════════════════════════════════════
# _build_system_prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildSystemPrompt:
    def test_openai_includes_knowledge_in_prompt(self):
        prompt = _build_system_prompt("roteiro", KNOWLEDGE, "")
        assert "R$497" in prompt

    def test_scoring_criteria_included(self):
        prompt = _build_system_prompt("roteiro", "", "Score alto se tiver urgência")
        assert "urgência" in prompt

    def test_default_prompt_used_when_empty(self):
        prompt = _build_system_prompt("", "", "")
        assert "assistente de vendas" in prompt.lower() or "qualifique" in prompt.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Retry com backoff
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetry:
    def test_retries_3_times_on_failure(self):
        with patch("services.responder_service._qualify_openai", side_effect=Exception("timeout")) as mock, \
             patch("time.sleep"):
            result = qualify_lead(
                openai_key="key", qualification_prompt="", history=HISTORY,
                turn_count=0, max_ai_turns=8, llm_provider="openai",
            )
        assert mock.call_count == 3
        assert result["action"] == "handoff"
        assert "3 tentativas" in result["score_reason"]

    def test_succeeds_on_second_attempt(self):
        success = {"message": "Ok", "action": "reply", "score": 60, "score_reason": "ok"}
        with patch("services.responder_service._qualify_openai", side_effect=[Exception("fail"), success]) as mock, \
             patch("time.sleep"):
            result = qualify_lead(
                openai_key="key", qualification_prompt="", history=HISTORY,
                turn_count=0, max_ai_turns=8, llm_provider="openai",
            )
        assert mock.call_count == 2
        assert result["action"] == "reply"


# ═══════════════════════════════════════════════════════════════════════════════
# generate_followup
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerateFollowup:
    def test_returns_empty_without_api_key(self):
        result = generate_followup("", "gpt-4.1-mini", "openai", "", "roteiro", HISTORY)
        assert result == ""

    def test_uses_openai_by_default(self):
        with patch("services.responder_service._qualify_openai") as mock:
            mock.return_value = {"message": "Oi!", "action": "reply", "score": 0, "score_reason": ""}
            result = generate_followup("gpt-key", "gpt-4.1-mini", "openai", "", "roteiro", HISTORY)
        assert result == "Oi!"
        mock.assert_called_once()

    def test_returns_empty_on_exception(self):
        with patch("services.responder_service._qualify_openai", side_effect=Exception("erro")):
            result = generate_followup("key", "gpt-4.1-mini", "openai", "", "roteiro", HISTORY)
        assert result == ""
