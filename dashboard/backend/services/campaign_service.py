"""
Motor de envio por instância.
Cada instância roda em thread separada com isolamento total.
"""

import json
import logging
import os
import time

logger = logging.getLogger("klead.campaign")
import random
from pathlib import Path
from datetime import datetime, timedelta
from services.outbound_routing import send_outbound_text
from services.openai_service import generate_message
from utils.instance_id import is_safe_instance_id

# ── Limites ────────────────────────────────────────────────
DAILY_MIN = 200
DAILY_MAX = 300
HOURLY_MIN = 25
HOURLY_MAX = 38
DELAY_MIN = 95
DELAY_MAX = 145


def _resolve_generation_key(inst: dict) -> str:
    provider = inst.get("llm_provider", "groq")
    provider_key_map = {"openai": "key_openai", "anthropic": "key_anthropic", "groq": "key_groq"}
    active_key_field = provider_key_map.get(provider, "key_openai")
    return inst.get(active_key_field) or inst.get("llm_api_key") or inst.get("key_openai") or inst.get("openai_key") or ""

# Pausa longa a cada ~20 envios
LONG_PAUSE_EVERY = 20
LONG_PAUSE_MIN = 300   # 5 min
LONG_PAUSE_MAX = 900   # 15 min

# Micro-pausa aleatória
MICRO_PAUSE_MIN = 30
MICRO_PAUSE_MAX = 60

# ── Paths de persistência ──────────────────────────────────
_BASE = Path(__file__).parent.parent / "data"
_BASE.mkdir(parents=True, exist_ok=True)
PROGRESS_DIR  = _BASE / "progress"
HISTORY_FILE  = _BASE / "campaign_history.json"
def _safe_instance_id(instance_id: str) -> str:
    if not is_safe_instance_id(instance_id):
        raise ValueError("instance_id inválido")
    return instance_id


# ── Persistência de progresso ──────────────────────────────

def _progress_file(instance_id: str) -> Path:
    PROGRESS_DIR.mkdir(exist_ok=True)
    return PROGRESS_DIR / f"{_safe_instance_id(instance_id)}_sent.json"


def _queue_file(instance_id: str) -> Path:
    PROGRESS_DIR.mkdir(exist_ok=True)
    return PROGRESS_DIR / f"{_safe_instance_id(instance_id)}_queue.json"


def _invalid_file(instance_id: str) -> Path:
    PROGRESS_DIR.mkdir(exist_ok=True)
    return PROGRESS_DIR / f"{_safe_instance_id(instance_id)}_invalid.json"


def _date_file(instance_id: str) -> Path:
    PROGRESS_DIR.mkdir(exist_ok=True)
    return PROGRESS_DIR / f"{_safe_instance_id(instance_id)}_campaign_date.json"


def load_campaign_day(instance_id: str) -> dict:
    """Retorna {"date": "YYYY-MM-DD", "sent_today": N} do último envio."""
    try:
        f = _date_file(instance_id)
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"date": "", "sent_today": 0}


def save_campaign_day(instance_id: str, date_str: str, sent_today: int):
    try:
        f = _date_file(instance_id)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": date_str, "sent_today": sent_today}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except Exception:
        pass


def load_invalid_numbers(instance_id: str) -> set:
    f = _invalid_file(instance_id)
    if f.exists():
        try:
            return set(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_invalid_numbers(instance_id: str, invalid: set):
    try:
        f = _invalid_file(instance_id)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(invalid), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except Exception:
        pass


def clear_invalid_numbers(instance_id: str):
    f = _invalid_file(instance_id)
    if f.exists():
        f.unlink(missing_ok=True)


def load_sent_numbers(instance_id: str) -> set:
    f = _progress_file(instance_id)
    if f.exists():
        try:
            return set(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_sent_numbers(instance_id: str, sent: set):
    try:
        f = _progress_file(instance_id)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(sent), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except Exception:
        pass


def clear_sent_numbers(instance_id: str):
    f = _progress_file(instance_id)
    if f.exists():
        f.unlink(missing_ok=True)


def save_queue(instance_id: str, queue: list):
    """Persiste a fila de mensagens pré-geradas em disco."""
    try:
        f = _queue_file(instance_id)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)
    except Exception:
        pass


def load_queue(instance_id: str) -> list:
    """Restaura a fila do disco (sobrevive a restarts do backend)."""
    f = _queue_file(instance_id)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def clear_queue(instance_id: str):
    f = _queue_file(instance_id)
    if f.exists():
        f.unlink(missing_ok=True)


def purge_instance_data(instance_id: str):
    """Remove todo o estado persistido ligado a uma instancia."""
    clear_sent_numbers(instance_id)
    clear_invalid_numbers(instance_id)
    clear_queue(instance_id)
    _date_file(instance_id).unlink(missing_ok=True)

    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            filtered = [item for item in history if item.get("instance_id") != instance_id]
            tmp = HISTORY_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, HISTORY_FILE)
        except Exception:
            pass


# ── Histórico de campanhas ─────────────────────────────────

def save_history(instance_id: str, started_at: str, status: str, total: int, enviados: int, falhos: int):
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({
        "instance_id": instance_id,
        "started_at":  started_at,
        "ended_at":    datetime.now().isoformat(),
        "status":      status,
        "total":       total,
        "enviados":    enviados,
        "falhos":      falhos,
    })
    try:
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        pass


# ── Helpers ────────────────────────────────────────────────

def _add_log(instances: dict, instance_id: str, name: str, phone: str, message: str, status: str):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "name":      name,
        "phone":     phone,
        "message":   message,
        "status":    status,
    }
    instances[instance_id]["logs"].append(entry)


def _hourly_count(instances: dict, instance_id: str) -> int:
    cutoff = datetime.now() - timedelta(hours=1)
    fresh = [ts for ts in instances[instance_id]["envios_hora"] if datetime.fromisoformat(ts) > cutoff]
    instances[instance_id]["envios_hora"] = fresh
    return len(fresh)


def _sleep_interruptible(seconds: int, inst: dict):
    for _ in range(seconds):
        if inst["stop_flag"]:
            return
        time.sleep(1)


# ── Motor principal ────────────────────────────────────────

def run_campaign(instance_id: str, instances: dict, context: str, resume: bool = False, daily_limit: int = 0, timing: dict = None, conversations: dict = None, save_conversations=None):
    inst       = instances[instance_id]
    started_at = datetime.now().isoformat()
    t = timing or {}

    # Resolve parâmetros de timing (0 = usar padrão)
    _hourly_max      = t.get("hourly_limit") or HOURLY_MAX
    _delay_min       = t.get("delay_min") or DELAY_MIN
    _delay_max       = t.get("delay_max") or DELAY_MAX
    _lp_every        = t.get("long_pause_every") or LONG_PAUSE_EVERY
    _lp_min          = t.get("long_pause_min") or LONG_PAUSE_MIN
    _lp_max          = t.get("long_pause_max") or LONG_PAUSE_MAX
    if _delay_max < _delay_min: _delay_max = _delay_min
    if _lp_max < _lp_min:       _lp_max = _lp_min

    # Progresso
    today_str       = datetime.now().strftime("%Y-%m-%d")
    invalid_numbers = load_invalid_numbers(instance_id)
    if resume:
        sent_numbers = load_sent_numbers(instance_id)
        day_data     = load_campaign_day(instance_id)
        is_new_day   = day_data.get("date", "") != today_str
        # Novo dia: zera contador diário mas mantém histórico de enviados (não reenviar)
        # Mesmo dia: restaura exatamente quantos foram enviados hoje
        sent_today   = 0 if is_new_day else day_data.get("sent_today", 0)
        log_msg = (f"CAMPANHA_RETOMADA_NOVO_DIA | ja_enviados_total={len(sent_numbers)} | enviados_hoje=0"
                   if is_new_day else
                   f"CAMPANHA_RETOMADA | ja_enviados_total={len(sent_numbers)} | enviados_hoje={sent_today}")
        _add_log(instances, instance_id, "", "", "", log_msg)
    else:
        sent_numbers = set()
        sent_today   = 0
        clear_sent_numbers(instance_id)

    save_campaign_day(instance_id, today_str, sent_today)

    daily_limit = daily_limit if daily_limit > 0 else max(1, random.randint(DAILY_MIN, DAILY_MAX))
    inst["daily_limit"] = daily_limit
    contacts = list(inst["contacts"])
    pending  = [c for c in contacts if c["number"] not in sent_numbers]

    send_counter = 0
    falhos       = 0

    _add_log(instances, instance_id, "", "", "",
             f"CAMPANHA_INICIADA | limite_dia={daily_limit} | pendentes={len(pending)} | total={len(contacts)}")

    for contact in pending:
        name  = contact.get("name", "")
        phone = contact.get("number", "")

        # ── Verificações de parada ──
        if inst["stop_flag"]:
            inst["status"] = "stopped"
            _add_log(instances, instance_id, "", "", "", "CAMPANHA_PARADA_PELO_USUARIO")
            save_sent_numbers(instance_id, sent_numbers)
            save_history(instance_id, started_at, "stopped", len(contacts), sent_today, falhos)
            return

        if sent_today >= daily_limit:
            inst["status"] = "completed"
            _add_log(instances, instance_id, "", "", "", f"LIMITE_DIARIO_ATINGIDO | enviados={sent_today}")
            save_sent_numbers(instance_id, sent_numbers)
            save_history(instance_id, started_at, "limit_reached", len(contacts), sent_today, falhos)
            return

        # ── Controle de limite por hora ──
        while _hourly_count(instances, instance_id) >= _hourly_max:
            if inst["stop_flag"]:
                inst["status"] = "stopped"
                save_sent_numbers(instance_id, sent_numbers)
                return
            _add_log(instances, instance_id, "", "", "", "AGUARDANDO_LIMITE_HORARIO")
            time.sleep(60)

        # ── Gera mensagem via OpenAI ──
        message = generate_message(
            openai_key=_resolve_generation_key(inst),
            prompt=inst["prompt"],
            name=name,
            phone=phone,
            contact=contact,
            context=context,
            model=inst.get("model", "openai/gpt-oss-120b"),
            provider=inst.get("llm_provider", "groq"),
        )
        if not message:
            _add_log(instances, instance_id, name, phone, "", "ERRO_OPENAI")
            falhos += 1
            continue

        # ── Envia pelo canal de saída preferencial ──
        success, detail, _channel = send_outbound_text(inst, instance_id, phone, message)

        not_on_whatsapp = not success and "not registered" in detail.lower()
        if not_on_whatsapp:
            status = "INVALIDO"
            invalid_numbers.add(phone)
            save_invalid_numbers(instance_id, invalid_numbers)
        else:
            status = "ENVIADO" if success else "FALHOU"
        _add_log(instances, instance_id, name, phone, message, status)

        # Marca sempre como processado (evita retentar em resume)
        sent_numbers.add(phone)

        if success:
            sent_today   += 1
            send_counter += 1
            inst["envios_hoje"] = sent_today
            inst["envios_hora"].append(datetime.now().isoformat())
            save_campaign_day(instance_id, today_str, sent_today)
            if send_counter % 10 == 0:
                save_sent_numbers(instance_id, sent_numbers)
            # Registra mensagem de saída como primeiro turno da conversa SDR
            if conversations is not None and message:
                seeded = _seed_conversation(conversations, instance_id, phone, name, message)
                if seeded and save_conversations:
                    # save_conversations é o saver incremental (instance_id, phone)
                    save_conversations(instance_id, phone)
        else:
            falhos += 1
            if falhos % 5 == 0:
                save_sent_numbers(instance_id, sent_numbers)

        # Números inválidos: sem delay (não é um envio real)
        if not_on_whatsapp:
            continue

        # ── Pausa longa ──
        if send_counter > 0 and send_counter % _lp_every == 0:
            pause = random.randint(_lp_min, _lp_max)
            _add_log(instances, instance_id, "", "", "", f"PAUSA_LONGA | {pause}s")
            _sleep_interruptible(pause, inst)
            if inst["stop_flag"]:
                inst["status"] = "stopped"
                save_sent_numbers(instance_id, sent_numbers)
                return
            micro = random.randint(MICRO_PAUSE_MIN, MICRO_PAUSE_MAX)
            _sleep_interruptible(micro, inst)
        else:
            delay = random.randint(_delay_min, _delay_max)
            _add_log(instances, instance_id, "", "", "", f"DELAY | {delay}s")
            _sleep_interruptible(delay, inst)

        if inst["stop_flag"]:
            inst["status"] = "stopped"
            save_sent_numbers(instance_id, sent_numbers)
            save_history(instance_id, started_at, "stopped", len(contacts), sent_today, falhos)
            return

    save_sent_numbers(instance_id, sent_numbers)
    inst["status"] = "completed"
    _add_log(instances, instance_id, "", "", "", f"CAMPANHA_CONCLUIDA | enviados={sent_today}")
    save_history(instance_id, started_at, "completed", len(contacts), sent_today, falhos)


def stop_campaign(instance_id: str, instances: dict):
    if instance_id in instances:
        instances[instance_id]["stop_flag"] = True
        instances[instance_id]["status"]    = "stopping"


# ── Campanha a partir de fila pré-gerada ───────────────────

def _seed_conversation(conversations: dict, instance_id: str, phone: str, name: str, message: str) -> bool:
    """Cria a entrada de conversa SDR com a mensagem de saída da campanha como primeiro turno.
    Retorna True se criou uma nova conversa, False se já existia."""
    if instance_id not in conversations:
        conversations[instance_id] = {}
    if phone not in conversations[instance_id]:
        now = datetime.now().isoformat()
        conversations[instance_id][phone] = {
            "name":             name or phone,
            "stage":            "qualifying",
            "messages":         [{"role": "assistant", "content": message, "timestamp": now}],
            "ai_paused":        False,
            "score":            None,
            "score_reason":     "",
            "turn_count":       0,
            "followup_count":   0,
            "last_followup_at": None,
            "created_at":       now,
            "last_activity":    now,
        }
        return True
    return False


def run_campaign_from_queue(instance_id: str, instances: dict, queue_items: list, daily_limit: int = 0, timing: dict = None, conversations: dict = None, save_conversations=None):
    inst       = instances[instance_id]
    started_at = datetime.now().isoformat()
    t = timing or {}

    _hourly_max = t.get("hourly_limit") or HOURLY_MAX
    _delay_min  = t.get("delay_min") or DELAY_MIN
    _delay_max  = t.get("delay_max") or DELAY_MAX
    _lp_every   = t.get("long_pause_every") or LONG_PAUSE_EVERY
    _lp_min     = t.get("long_pause_min") or LONG_PAUSE_MIN
    _lp_max     = t.get("long_pause_max") or LONG_PAUSE_MAX
    if _delay_max < _delay_min: _delay_max = _delay_min
    if _lp_max < _lp_min:       _lp_max = _lp_min

    daily_limit = daily_limit if daily_limit > 0 else max(1, random.randint(DAILY_MIN, DAILY_MAX))
    inst["daily_limit"] = daily_limit

    sent_numbers = set()
    sent_today   = 0
    send_counter = 0
    falhos       = 0

    _add_log(instances, instance_id, "", "", "",
             f"CAMPANHA_INICIADA | limite_dia={daily_limit} | fila={len(queue_items)}")

    for item in queue_items:
        name    = item["name"]
        phone   = item["phone"]
        message = item["message"]

        if inst["stop_flag"]:
            inst["status"] = "stopped"
            _add_log(instances, instance_id, "", "", "", "CAMPANHA_PARADA_PELO_USUARIO")
            save_sent_numbers(instance_id, sent_numbers)
            save_history(instance_id, started_at, "stopped", len(queue_items), sent_today, falhos)
            return

        if sent_today >= daily_limit:
            inst["status"] = "completed"
            _add_log(instances, instance_id, "", "", "", f"LIMITE_DIARIO_ATINGIDO | enviados={sent_today}")
            save_sent_numbers(instance_id, sent_numbers)
            save_history(instance_id, started_at, "limit_reached", len(queue_items), sent_today, falhos)
            return

        while _hourly_count(instances, instance_id) >= _hourly_max:
            if inst["stop_flag"]:
                inst["status"] = "stopped"
                save_sent_numbers(instance_id, sent_numbers)
                return
            _add_log(instances, instance_id, "", "", "", "AGUARDANDO_LIMITE_HORARIO")
            time.sleep(60)

        success, _, _channel = send_outbound_text(inst, instance_id, phone, message)

        status = "ENVIADO" if success else "FALHOU"
        _add_log(instances, instance_id, name, phone, message, status)

        # Marca sempre como processado (evita retentar em resume)
        sent_numbers.add(phone)

        if success:
            sent_today   += 1
            send_counter += 1
            inst["envios_hoje"] = sent_today
            inst["envios_hora"].append(datetime.now().isoformat())
            if send_counter % 10 == 0:
                save_sent_numbers(instance_id, sent_numbers)
            # Registra mensagem de saída como primeiro turno da conversa SDR
            if conversations is not None:
                seeded = _seed_conversation(conversations, instance_id, phone, name, message)
                if seeded and save_conversations:
                    # save_conversations é o saver incremental (instance_id, phone)
                    save_conversations(instance_id, phone)
        else:
            falhos += 1
            if falhos % 5 == 0:
                save_sent_numbers(instance_id, sent_numbers)

        if send_counter > 0 and send_counter % _lp_every == 0:
            pause = random.randint(_lp_min, _lp_max)
            _add_log(instances, instance_id, "", "", "", f"PAUSA_LONGA | {pause}s")
            _sleep_interruptible(pause, inst)
            if inst["stop_flag"]:
                inst["status"] = "stopped"
                save_sent_numbers(instance_id, sent_numbers)
                return
            micro = random.randint(MICRO_PAUSE_MIN, MICRO_PAUSE_MAX)
            _sleep_interruptible(micro, inst)
        else:
            delay = random.randint(_delay_min, _delay_max)
            _add_log(instances, instance_id, "", "", "", f"DELAY | {delay}s")
            _sleep_interruptible(delay, inst)

        if inst["stop_flag"]:
            inst["status"] = "stopped"
            save_sent_numbers(instance_id, sent_numbers)
            save_history(instance_id, started_at, "stopped", len(queue_items), sent_today, falhos)
            return

    save_sent_numbers(instance_id, sent_numbers)
    inst["status"] = "completed"
    _add_log(instances, instance_id, "", "", "", f"CAMPANHA_CONCLUIDA | enviados={sent_today}")
    save_history(instance_id, started_at, "completed", len(queue_items), sent_today, falhos)


# ── Geração de fila em background ─────────────────────────

QUEUE_MAX_SIZE = int(os.getenv("QUEUE_MAX_SIZE", "500"))


def generate_queue_thread(instance_id: str, instances: dict, context: str):
    inst = instances[instance_id]
    try:
        inst["queue"] = []
        inst["queue_progress"] = 0
        clear_queue(instance_id)

        contacts = list(inst["contacts"])
        if len(contacts) > QUEUE_MAX_SIZE:
            logger.warning(
                f"[QUEUE] {instance_id}: {len(contacts)} contatos excedem o limite de {QUEUE_MAX_SIZE}. "
                f"Gerando apenas os primeiros {QUEUE_MAX_SIZE}."
            )
            contacts = contacts[:QUEUE_MAX_SIZE]

        for i, contact in enumerate(contacts):
            if inst.get("queue_stop_flag"):
                inst["queue_status"] = "idle"
                return

            name = contact.get("name", "")
            phone = contact.get("number", "")

            message = generate_message(
                openai_key=_resolve_generation_key(inst),
                prompt=inst["prompt"],
                name=name,
                phone=phone,
                contact=contact,
                context=context,
                model=inst.get("model", "gpt-4.1-mini"),
                provider=inst.get("llm_provider", "openai"),
            )

            inst["queue"].append({
                "name": name,
                "phone": phone,
                "message": message or "",
                "skipped": False,
            })
            inst["queue_progress"] = i + 1
            save_queue(instance_id, inst["queue"])

        inst["queue_status"] = "ready"
    except Exception as exc:
        inst["queue_status"] = "idle"
        inst["queue_stop_flag"] = False
        logger.error(f"[QUEUE] Erro ao gerar fila para {instance_id}: {exc}")
    finally:
        save_queue(instance_id, inst.get("queue", []))
