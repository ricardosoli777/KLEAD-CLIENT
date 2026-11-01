import logging

logger = logging.getLogger("klead.openai")

import time
from datetime import datetime, timedelta, timezone

from openai import OpenAI

BRAZIL_TZ = timezone(timedelta(hours=-3))
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_PREFIXES = ("llama", "mixtral", "gemma", "meta-llama/", "qwen/", "openai/gpt-oss", "groq/")


def _saudacao() -> str:
    hora = datetime.now(BRAZIL_TZ).hour
    if 5 <= hora < 12:
        return "bom dia"
    if 12 <= hora < 18:
        return "boa tarde"
    return "boa noite"


def _contact_context(name: str, phone: str = "", contact: dict | None = None) -> str:
    fields = {
        "nome": name if name and name != "Contato" else "amigo(a)",
        "telefone": phone or "",
    }
    if contact:
        for key, value in contact.items():
            if key in ("name", "number", "phone"):
                continue
            if value is not None and str(value).strip():
                fields[str(key)] = str(value).strip()
    return "\n".join(f"- {key}: {value}" for key, value in fields.items() if value)


def _clean_message(text: str | None) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    for prefix in ("Mensagem:", "Texto:", "Resposta:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    return text


def create_chat_completion(client: OpenAI, use_groq: bool, tokens: int = 220, **kwargs):
    """
    Wrapper com fallback automatico para incompatibilidades comuns entre
    modelos/providers OpenAI-compatible:
    - nome do parametro de limite de tokens (max_tokens vs max_completion_tokens)
    - modelos que so aceitam temperature padrao (ex: familia gpt-5.x/o-series
      rejeita qualquer valor de temperature diferente de 1)
    Tenta de novo removendo/trocando o que a API reclamar, sem precisar saber
    de antemao quais restricoes cada modelo tem.
    """
    token_param = "max_tokens" if use_groq else "max_completion_tokens"
    attempt_kwargs = dict(kwargs)
    for _ in range(3):
        try:
            return client.chat.completions.create(**attempt_kwargs, **{token_param: tokens})
        except Exception as exc:
            message = str(exc)
            changed = False
            if "Unsupported parameter" in message and token_param in message:
                token_param = "max_completion_tokens" if token_param == "max_tokens" else "max_tokens"
                changed = True
            if "temperature" in attempt_kwargs and "temperature" in message.lower() and (
                "unsupported_value" in message or "does not support" in message
            ):
                attempt_kwargs.pop("temperature", None)
                changed = True
            if not changed:
                raise
    raise RuntimeError("create_chat_completion: esgotou tentativas de fallback")


# Alias interno mantido para não quebrar chamadas existentes neste arquivo.
_create_chat_completion = create_chat_completion


def generate_message(
    openai_key: str,
    prompt: str,
    name: str,
    context: str,
    phone: str = "",
    contact: dict | None = None,
    model: str = "openai/gpt-oss-120b",
    provider: str = "groq",
) -> str | None:
    """
    Gera mensagem personalizada para campanha.

    Usa o parametro de limite de tokens compativel com o provedor/modelo ativo.
    """
    name_part = name if name and name != "Contato" else "amigo(a)"
    processed_prompt = (
        (prompt or "")
        .replace("{nome}", name_part)
        .replace("{NOME}", name_part)
        .replace("{telefone}", phone or "")
        .replace("{TELEFONE}", phone or "")
    )
    saudacao = _saudacao()
    user_content = (
        "DADOS DO CONTATO\n"
        f"{_contact_context(name_part, phone, contact)}\n\n"
        "DADOS DA CAMPANHA\n"
        f"- saudacao_do_momento: {saudacao}\n"
        f"- contexto: {(context or '').strip()}\n\n"
        "TAREFA\n"
        "Gere uma mensagem unica para WhatsApp usando o system prompt como regra principal.\n"
        "Obedeca exatamente a oferta, tom, objetivo, restricoes e CTA descritos no prompt e no contexto.\n"
        "Nao invente dados, valores, prazos, descontos, nomes de produto ou promessas.\n"
        "Nao mencione que voce e IA, nao explique o raciocinio e nao crie assunto de email.\n"
        "Retorne apenas o texto final da mensagem, sem aspas e sem prefixos."
    )
    last_err = None

    for attempt in range(3):
        try:
            selected_model = model or ("openai/gpt-oss-120b" if provider == "groq" else "gpt-5.5")

            # OpenAI / Groq
            use_groq = provider == "groq" or any(selected_model.startswith(prefix) for prefix in GROQ_PREFIXES)
            client = OpenAI(
                api_key=openai_key,
                timeout=60,
                max_retries=1,
                **({"base_url": GROQ_BASE_URL} if use_groq else {}),
            )
            resp = _create_chat_completion(
                client,
                use_groq,
                model=selected_model,
                messages=[
                    {"role": "system", "content": processed_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.55,
            )
            message = _clean_message(resp.choices[0].message.content)
            return message or None
        except Exception as exc:
            last_err = exc
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning(f"[OpenAI] Tentativa {attempt + 1} falhou: {exc}. Aguardando {wait}s...")
                time.sleep(wait)

    logger.warning(f"[OpenAI] Erro apos 3 tentativas: {last_err}")
    return None
