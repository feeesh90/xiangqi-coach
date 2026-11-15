import queue
import subprocess
import sys
import threading
import time

import pytest

from engine import Pikafish


def test_silent_engine_has_a_real_timeout_and_is_terminated():
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    engine = Pikafish.__new__(Pikafish)
    engine.p = process
    engine.ready = True
    engine.current_multipv = None
    engine._stdout_queue = queue.Queue()
    engine._reader_thread = None
    engine._reader_process = None

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="已终止无响应的引擎"):
        engine._read_until("readyok", 0.15)

    assert time.monotonic() - started < 1
    assert process.poll() is not None
    assert engine.p is None


def test_close_does_not_wait_for_analysis_lock():
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    engine = Pikafish.__new__(Pikafish)
    engine.p = process
    engine.ready = True
    engine.current_multipv = None
    engine.cancel_generation = 0
    engine.lock = threading.Lock()
    engine.write_lock = threading.Lock()
    engine.lock.acquire()

    closer = threading.Thread(target=engine.close)
    closer.start()
    closer.join(timeout=2)
    engine.lock.release()

    assert not closer.is_alive()
    assert process.poll() is not None
