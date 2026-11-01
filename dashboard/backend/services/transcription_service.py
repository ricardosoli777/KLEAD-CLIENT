import logging

logger = logging.getLogger("klead.transcription")

import io


def transcribe_audio(audio_bytes: bytes, filename: str, llm_provider: str, llm_api_key: str, openai_key: str = "") -> str:
    """
    Transcreve áudio usando Groq Whisper (gratuito) ou OpenAI Whisper (fallback).
    Retorna texto transcrito ou string vazia em caso de falha.
    """
    # Groq Whisper — gratuito, até 7.200 min/dia
    if llm_provider == "groq" and llm_api_key:
        try:
            from groq import Groq
            client = Groq(api_key=llm_api_key)
            buf = io.BytesIO(audio_bytes)
            buf.name = filename or "audio.ogg"
            result = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=buf,
                language="pt",
            )
            text = (result.text or "").strip()
            if text:
                logger.info(f"[Transcription] Groq: {text[:80]}")
                return text
        except Exception as e:
            logger.info(f"[Transcription] Groq falhou: {e}")

    # OpenAI Whisper — fallback pago
    key = openai_key or (llm_api_key if llm_provider == "openai" else "")
    if key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, timeout=120, max_retries=1)
            buf = io.BytesIO(audio_bytes)
            buf.name = filename or "audio.ogg"
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=buf,
                language="pt",
            )
            text = (result.text or "").strip()
            if text:
                logger.info(f"[Transcription] OpenAI: {text[:80]}")
                return text
        except Exception as e:
            logger.info(f"[Transcription] OpenAI falhou: {e}")

    return ""
