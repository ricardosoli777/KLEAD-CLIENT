"""
Testes isolados do scheduler único (item 6 do roadmap de escala): ordem de
disparo por deadline, isolamento de erro entre tarefas, stop() limpo.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.scheduler import Scheduler  # noqa: E402


def test_dispara_na_ordem_dos_deadlines():
    sched = Scheduler()
    order = []
    lock = threading.Lock()
    done = threading.Event()

    def make_task(label):
        def _run():
            with lock:
                order.append(label)
                if len(order) >= 3:
                    done.set()
        return _run

    sched.schedule_recurring("c", 1000, make_task("c"), initial_delay=0.15)
    sched.schedule_recurring("a", 1000, make_task("a"), initial_delay=0.01)
    sched.schedule_recurring("b", 1000, make_task("b"), initial_delay=0.08)
    sched.start()
    try:
        assert done.wait(timeout=2.0)
        assert order == ["a", "b", "c"]
    finally:
        sched.stop()


def test_erro_em_uma_tarefa_nao_derruba_as_outras():
    sched = Scheduler()
    ok_calls = []
    done = threading.Event()

    def failing():
        raise RuntimeError("falha proposital")

    def ok():
        ok_calls.append(time.time())
        if len(ok_calls) >= 2:
            done.set()

    sched.schedule_recurring("falha", 0.03, failing, initial_delay=0.0)
    sched.schedule_recurring("ok", 0.03, ok, initial_delay=0.0)
    sched.start()
    try:
        assert done.wait(timeout=2.0)
        assert len(ok_calls) >= 2
    finally:
        sched.stop()


def test_stop_encerra_thread_worker_sem_deixar_orfa():
    sched = Scheduler()
    sched.schedule_recurring("noop", 1000, lambda: None, initial_delay=0.0)
    sched.start()
    worker = sched._thread
    assert worker is not None and worker.is_alive()

    sched.stop(timeout=2.0)

    assert sched._thread is None
    assert not worker.is_alive()


def test_start_e_idempotente():
    sched = Scheduler()
    sched.start()
    first_thread = sched._thread
    sched.start()
    try:
        assert sched._thread is first_thread
    finally:
        sched.stop()
