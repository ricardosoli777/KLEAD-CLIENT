"""Modelos de enquete e envios do Inbox, persistidos no banco do KLEAD."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from psycopg2.extras import Json, RealDictCursor

from services import crm_pg_service


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS inbox_poll_templates (
    id uuid PRIMARY KEY,
    owner_key varchar(255) NOT NULL,
    name varchar(255) NOT NULL,
    question varchar(255) NOT NULL,
    options_json jsonb NOT NULL,
    questions_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    allow_multiple_answers boolean NOT NULL DEFAULT false,
    is_default boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_by varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
ALTER TABLE inbox_poll_templates
    ADD COLUMN IF NOT EXISTS questions_json jsonb NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS inbox_poll_templates_owner_idx
    ON inbox_poll_templates (owner_key, active, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS inbox_poll_templates_one_default_idx
    ON inbox_poll_templates (owner_key) WHERE is_default AND active;

CREATE TABLE IF NOT EXISTS inbox_poll_sends (
    id uuid PRIMARY KEY,
    owner_key varchar(255) NOT NULL,
    template_id uuid NULL,
    instance_id varchar(128) NOT NULL,
    conversation_key varchar(180) NOT NULL,
    provider varchar(32) NOT NULL,
    provider_message_id varchar(255) NOT NULL DEFAULT '',
    request_id uuid NOT NULL,
    batch_id uuid NULL,
    question_index integer NOT NULL DEFAULT 1,
    question_count integer NOT NULL DEFAULT 1,
    status varchar(32) NOT NULL DEFAULT 'sending',
    question_snapshot varchar(255) NOT NULL,
    options_snapshot_json jsonb NOT NULL,
    allow_multiple_snapshot boolean NOT NULL,
    error_detail text NOT NULL DEFAULT '',
    created_by varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    sent_at timestamptz NULL
);
ALTER TABLE inbox_poll_sends ADD COLUMN IF NOT EXISTS batch_id uuid NULL;
ALTER TABLE inbox_poll_sends ADD COLUMN IF NOT EXISTS question_index integer NOT NULL DEFAULT 1;
ALTER TABLE inbox_poll_sends ADD COLUMN IF NOT EXISTS question_count integer NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX IF NOT EXISTS inbox_poll_sends_request_idx
    ON inbox_poll_sends (owner_key, request_id);
CREATE INDEX IF NOT EXISTS inbox_poll_sends_message_idx
    ON inbox_poll_sends (instance_id, provider_message_id)
    WHERE provider_message_id <> '';
CREATE INDEX IF NOT EXISTS inbox_poll_sends_conversation_idx
    ON inbox_poll_sends (owner_key, instance_id, conversation_key, created_at DESC);

CREATE TABLE IF NOT EXISTS inbox_poll_batches (
    id uuid PRIMARY KEY,
    owner_key varchar(255) NOT NULL,
    template_id uuid NOT NULL,
    instance_id varchar(128) NOT NULL,
    conversation_key varchar(180) NOT NULL,
    questions_snapshot_json jsonb NOT NULL,
    allow_multiple_snapshot boolean NOT NULL DEFAULT false,
    status varchar(32) NOT NULL DEFAULT 'sending',
    created_by varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
ALTER TABLE inbox_poll_batches
    ADD COLUMN IF NOT EXISTS allow_multiple_snapshot boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS inbox_poll_batches_conversation_idx
    ON inbox_poll_batches (owner_key, instance_id, conversation_key, created_at DESC);

CREATE TABLE IF NOT EXISTS inbox_poll_votes (
    send_id uuid NOT NULL REFERENCES inbox_poll_sends(id),
    voter_key varchar(255) NOT NULL,
    voter_name varchar(255) NOT NULL DEFAULT '',
    selected_options_json jsonb NOT NULL,
    vote_message_id varchar(255) NOT NULL DEFAULT '',
    voted_at timestamptz NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (send_id, voter_key)
);
"""


def _now():
    return datetime.now(timezone.utc)


def _row(row):
    if not row:
        return None
    result = dict(row)
    for key in ("id", "template_id", "request_id", "batch_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


def _connect():
    return crm_pg_service._connect()


def ensure_schema() -> tuple[bool, str]:
    if not crm_pg_service.pg_configured():
        return False, "postgres_not_configured"
    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        cur.close()
        return True, ""
    except Exception as exc:
        if conn:
            conn.rollback()
        return False, str(exc)
    finally:
        if conn:
            conn.close()


def validate_template(name: str, question: str, options: list[str]) -> tuple[str, str, list[str]]:
    clean_name = str(name or "").strip()
    clean_question = str(question or "").strip()
    if not clean_name or len(clean_name) > 255:
        raise ValueError("Informe um nome interno de até 255 caracteres.")
    if not clean_question or len(clean_question) > 255:
        raise ValueError("Informe uma pergunta de até 255 caracteres.")
    if not isinstance(options, list) or not 2 <= len(options) <= 12:
        raise ValueError("Informe de duas a doze opções.")
    clean_options = [str(value or "").strip() for value in options]
    if any(not value or len(value) > 100 for value in clean_options):
        raise ValueError("Cada opção deve ter entre 1 e 100 caracteres.")
    if len({value.casefold() for value in clean_options}) != len(clean_options):
        raise ValueError("As opções não podem se repetir.")
    return clean_name, clean_question, clean_options


def validate_questions(questions: list[dict]) -> list[dict]:
    if not isinstance(questions, list) or not questions:
        raise ValueError("Adicione pelo menos uma pergunta.")
    clean_questions = []
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"A pergunta {index} está inválida.")
        _, question, options = validate_template("Enquete", item.get("question"), item.get("options"))
        clean_questions.append({"question": question, "options": options})
    compose_poll("Enquete", clean_questions)
    return clean_questions


def compose_poll(name: str, questions: list[dict]) -> dict:
    """Agrupa as opções em uma única enquete nativa do WhatsApp."""
    options = [option for item in questions for option in item["options"]]
    _, question, options = validate_template(name, name, options)
    return {"question": question, "options": options}


def _with_questions(row: dict | None) -> dict | None:
    if not row:
        return None
    result = _row(row)
    questions = result.get("questions_json") or []
    if not questions:
        questions = [{"question": result["question"], "options": result["options_json"]}]
    result["questions"] = questions
    result["question_count"] = len(questions)
    return result


def _lock_owner(cur, owner_key: str):
    if not owner_key:
        raise ValueError("Tenant obrigatório.")
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("inbox-polls:" + owner_key,))


def list_templates(owner_key: str) -> list[dict]:
    if not owner_key:
        raise ValueError("Tenant obrigatório.")
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT * FROM inbox_poll_templates WHERE owner_key=%s AND active=true
               ORDER BY is_default DESC, updated_at DESC""",
            (owner_key,),
        )
        return [_with_questions(item) for item in cur.fetchall()]
    finally:
        conn.close()


def get_template(template_id: str, owner_key: str) -> dict | None:
    if not owner_key:
        raise ValueError("Tenant obrigatório.")
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM inbox_poll_templates WHERE id=%s AND owner_key=%s AND active=true",
            (template_id, owner_key),
        )
        return _with_questions(cur.fetchone())
    finally:
        conn.close()


def create_template(owner_key: str, name: str, question: str, options: list[str],
                    allow_multiple_answers: bool, is_default: bool, created_by: str,
                    questions: list[dict] | None = None) -> dict:
    name = str(name or "").strip()
    if not name or len(name) > 255:
        raise ValueError("Informe um nome interno de até 255 caracteres.")
    if questions is None:
        name, question, options = validate_template(name, question, options)
        clean_questions = [{"question": question, "options": options}]
    else:
        clean_questions = validate_questions(questions)
        question, options = clean_questions[0]["question"], clean_questions[0]["options"]
    template_id = str(uuid.uuid4())
    now = _now()
    conn = _connect()
    try:
        cur = conn.cursor()
        _lock_owner(cur, owner_key)
        if is_default:
            cur.execute("UPDATE inbox_poll_templates SET is_default=false, updated_at=%s WHERE owner_key=%s AND is_default=true", (now, owner_key))
        cur.execute(
            """INSERT INTO inbox_poll_templates
               (id, owner_key, name, question, options_json, questions_json, allow_multiple_answers,
                is_default, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (template_id, owner_key, name, question, Json(options), Json(clean_questions),
             bool(allow_multiple_answers), bool(is_default), created_by, now, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_template(template_id, owner_key)


def update_template(template_id: str, owner_key: str, fields: dict) -> dict | None:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _lock_owner(cur, owner_key)
        cur.execute(
            "SELECT * FROM inbox_poll_templates WHERE id=%s AND owner_key=%s AND active=true FOR UPDATE",
            (template_id, owner_key),
        )
        current = cur.fetchone()
        if not current:
            conn.rollback()
            return None
        name = str(fields.get("name", current["name"]) or "").strip()
        if not name or len(name) > 255:
            raise ValueError("Informe um nome interno de até 255 caracteres.")
        old_questions = current.get("questions_json") or [{
            "question": current["question"], "options": current["options_json"],
        }]
        if "questions" in fields:
            clean_questions = validate_questions(fields["questions"])
        elif "question" in fields or "options" in fields:
            _, question, options = validate_template(
                name, fields.get("question", old_questions[0]["question"]),
                fields.get("options", old_questions[0]["options"]),
            )
            clean_questions = [{"question": question, "options": options}]
        else:
            clean_questions = validate_questions(old_questions)
        question, options = clean_questions[0]["question"], clean_questions[0]["options"]
        multiple = bool(fields.get("allow_multiple_answers", current["allow_multiple_answers"]))
        default = bool(fields.get("is_default", current["is_default"]))
        now = _now()
        if default:
            cur.execute(
                "UPDATE inbox_poll_templates SET is_default=false, updated_at=%s WHERE owner_key=%s AND id<>%s AND is_default=true",
                (now, owner_key, template_id),
            )
        cur.execute(
            """UPDATE inbox_poll_templates SET name=%s, question=%s, options_json=%s,
               questions_json=%s, allow_multiple_answers=%s, is_default=%s, updated_at=%s
               WHERE id=%s AND owner_key=%s""",
            (name, question, Json(options), Json(clean_questions), multiple, default, now, template_id, owner_key),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_template(template_id, owner_key)


def duplicate_template(template_id: str, owner_key: str, created_by: str) -> dict | None:
    source = get_template(template_id, owner_key)
    if not source:
        return None
    return create_template(
        owner_key, (source["name"][:247] + " (cópia)") if len(source["name"]) > 247 else source["name"] + " (cópia)",
        source["question"], source["options_json"], source["allow_multiple_answers"], False, created_by,
        source.get("questions"),
    )


def deactivate_template(template_id: str, owner_key: str) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        _lock_owner(cur, owner_key)
        cur.execute(
            """UPDATE inbox_poll_templates SET active=false, is_default=false, updated_at=%s
               WHERE id=%s AND owner_key=%s AND active=true""",
            (_now(), template_id, owner_key),
        )
        changed = cur.rowcount > 0
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_batch(owner_key: str, template: dict, instance_id: str, conversation_key: str,
                 request_id: str, created_by: str) -> tuple[dict, bool]:
    if not owner_key or template["owner_key"] != owner_key:
        raise ValueError("Modelo de outro tenant.")
    batch_id = str(uuid.UUID(request_id))
    snapshot = validate_questions(template.get("questions") or [{
        "question": template["question"], "options": template["options_json"],
    }])
    now = _now()
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """INSERT INTO inbox_poll_batches
               (id, owner_key, template_id, instance_id, conversation_key,
                questions_snapshot_json, allow_multiple_snapshot, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING RETURNING *""",
            (batch_id, owner_key, template["id"], instance_id, conversation_key,
             Json(snapshot), bool(template["allow_multiple_answers"]), created_by, now, now),
        )
        row = cur.fetchone()
        created = row is not None
        if not row:
            cur.execute("SELECT * FROM inbox_poll_batches WHERE id=%s", (batch_id,))
            row = cur.fetchone()
        if not row or row["owner_key"] != owner_key or str(row["template_id"]) != str(template["id"]) \
                or row["instance_id"] != instance_id or row["conversation_key"] != conversation_key:
            conn.rollback()
            raise ValueError("Identificador de envio já utilizado em outra enquete ou conversa.")
        conn.commit()
        return _row(row), created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_legacy_batch_sends(batch_id: str, owner_key: str, request_id: str) -> bool:
    """Impede que uma sequência enviada pela versão anterior seja reenviada como enquete única."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM inbox_poll_sends WHERE batch_id=%s AND owner_key=%s AND request_id<>%s LIMIT 1",
            (batch_id, owner_key, request_id),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def mark_batch(batch_id: str, owner_key: str, status: str) -> None:
    if status not in {"sending", "partial", "sent", "failed"}:
        raise ValueError("Status de sequência inválido.")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE inbox_poll_batches SET status=%s, updated_at=%s WHERE id=%s AND owner_key=%s",
                    (status, _now(), batch_id, owner_key))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_send(owner_key: str, template: dict, instance_id: str, conversation_key: str,
                provider: str, request_id: str, created_by: str, question: dict | None = None,
                batch_id: str | None = None, question_index: int = 1,
                question_count: int = 1) -> tuple[dict, bool]:
    if not owner_key or template["owner_key"] != owner_key:
        raise ValueError("Modelo de outro tenant.")
    request_id = str(uuid.UUID(request_id))
    question = question or {"question": template["question"], "options": template["options_json"]}
    question_text, options = validate_template(template["name"], question.get("question"), question.get("options"))[1:]
    send_id = str(uuid.uuid4())
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """INSERT INTO inbox_poll_sends
               (id, owner_key, template_id, instance_id, conversation_key, provider,
                request_id, batch_id, question_index, question_count,
                question_snapshot, options_snapshot_json, allow_multiple_snapshot,
                created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (owner_key, request_id) DO NOTHING RETURNING *""",
            (send_id, owner_key, template["id"], instance_id, conversation_key, provider,
             request_id, batch_id, question_index, question_count, question_text, Json(options),
             template["allow_multiple_answers"], created_by, _now()),
        )
        row = cur.fetchone()
        created = row is not None
        if not created:
            cur.execute("SELECT * FROM inbox_poll_sends WHERE owner_key=%s AND request_id=%s", (owner_key, request_id))
            row = cur.fetchone()
        conn.commit()
        return _row(row), created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_send(send_id: str, owner_key: str, status: str, provider_message_id: str = "",
              error_detail: str = "") -> dict | None:
    if status not in {"sent", "failed"}:
        raise ValueError("Status de envio inválido.")
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE inbox_poll_sends SET status=%s, provider_message_id=%s,
               error_detail=%s, sent_at=%s WHERE id=%s AND owner_key=%s AND status='sending'
               RETURNING *""",
            (status, provider_message_id[:255], error_detail[:1000],
             _now() if status == "sent" else None, send_id, owner_key),
        )
        row = cur.fetchone()
        conn.commit()
        return _row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retry_failed_send(send_id: str, owner_key: str) -> dict | None:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE inbox_poll_sends SET status='sending', error_detail='', sent_at=NULL
               WHERE id=%s AND owner_key=%s AND status='failed' RETURNING *""",
            (send_id, owner_key),
        )
        row = cur.fetchone()
        conn.commit()
        return _row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_send(send_id: str, owner_key: str, instance_id: str, conversation_key: str) -> dict | None:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT * FROM inbox_poll_sends WHERE id=%s AND owner_key=%s
               AND instance_id=%s AND conversation_key=%s AND status='sent'""",
            (send_id, owner_key, instance_id, conversation_key),
        )
        return _row(cur.fetchone())
    finally:
        conn.close()


def save_votes(send_id: str, votes: list[dict]) -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        for vote in votes:
            cur.execute(
                """INSERT INTO inbox_poll_votes
                   (send_id, voter_key, voter_name, selected_options_json, vote_message_id, voted_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (send_id, voter_key) DO UPDATE SET
                   voter_name=EXCLUDED.voter_name, selected_options_json=EXCLUDED.selected_options_json,
                   vote_message_id=EXCLUDED.vote_message_id, voted_at=EXCLUDED.voted_at,
                   updated_at=EXCLUDED.updated_at""",
                (send_id, vote["voter_key"], vote["voter_name"], Json(vote["selected_options"]),
                 vote["vote_message_id"], vote["voted_at"], _now()),
            )
        cur.execute(
            "DELETE FROM inbox_poll_votes WHERE send_id=%s AND NOT (voter_key = ANY(%s))",
            (send_id, [vote["voter_key"] for vote in votes]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_votes(send_id: str) -> list[dict]:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT voter_key, voter_name, selected_options_json, vote_message_id, voted_at
               FROM inbox_poll_votes WHERE send_id=%s ORDER BY voted_at NULLS LAST, voter_key""",
            (send_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
