"""
SSE — notificações em tempo real.

Estado compartilhado entre main.py e routers.
Importar `emit` e as variáveis de estado via:
    from core.sse import emit as _sse_emit, _sse_listeners, _sse_lock
"""
import json
import threading
import queue as _queue_mod  # re-exportado para uso nos endpoints SSE

_sse_listeners: dict[str, list] = {}
_sse_lock = threading.Lock()


def emit(instance_id: str, event: str, data: dict):
    """Emite evento SSE para todos os listeners de uma instância."""
    with _sse_lock:
        listeners = _sse_listeners.get(instance_id, [])
        payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        dead = []
        for q in listeners:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            listeners.remove(q)
