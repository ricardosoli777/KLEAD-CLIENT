"""
Scheduler único para tarefas recorrentes em background, substituindo as
threads soltas com `while True: time.sleep(N)` espalhadas pelo backend.

Um heap de deadlines + 1 thread worker única. Cada tarefa roda isolada num
try/except: erro numa tarefa não derruba as outras nem o scheduler.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("scheduler")


@dataclass(order=True)
class _Task:
    next_run: float
    seq: int
    name: str = field(compare=False)
    interval: float = field(compare=False)
    func: Callable[[], None] = field(compare=False)


class Scheduler:
    def __init__(self):
        self._heap: list[_Task] = []
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def schedule_recurring(self, name: str, interval_seconds: float, func: Callable[[], None], initial_delay: float = 0.0) -> None:
        """Registra uma tarefa recorrente. Pode ser chamado antes ou depois de start()."""
        task = _Task(next_run=time.time() + initial_delay, seq=next(self._seq), name=name, interval=interval_seconds, func=func)
        with self._lock:
            heapq.heappush(self._heap, task)
        self._wakeup.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="scheduler-worker")
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stopped.set()
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stopped.is_set():
            with self._lock:
                if not self._heap:
                    wait = 1.0
                else:
                    wait = max(self._heap[0].next_run - time.time(), 0.0)
            if self._wakeup.wait(timeout=wait):
                self._wakeup.clear()
            if self._stopped.is_set():
                return

            with self._lock:
                if not self._heap or self._heap[0].next_run > time.time():
                    continue
                task = heapq.heappop(self._heap)

            try:
                task.func()
            except Exception:
                logger.warning(f"[Scheduler] Tarefa '{task.name}' falhou", exc_info=True)

            task.next_run = time.time() + task.interval
            with self._lock:
                heapq.heappush(self._heap, task)


scheduler = Scheduler()
