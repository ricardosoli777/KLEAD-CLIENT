"""Validation shared by instance-id persistence and path helpers."""

import re

_SAFE_INSTANCE_ID_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._ \-]{0,126}[A-Za-z0-9])?$"
)


def is_safe_instance_id(instance_id: str) -> bool:
    return bool(_SAFE_INSTANCE_ID_RE.fullmatch(instance_id or ""))
