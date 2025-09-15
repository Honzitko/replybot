
import importlib.util
import pathlib
import sys
import threading

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("x", root / "x.py")
x = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = x
spec.loader.exec_module(x)

PostScheduler = x.PostScheduler

def test_post_scheduler_pauses_and_resumes():
    pause = threading.Event()
    stop = threading.Event()
    calls = []
    def cb():
        calls.append(pause.is_set())
    ps = PostScheduler(1, pause, stop, cb)
    ps._trigger_post()
    assert calls == [True]
    assert not pause.is_set()

def test_post_scheduler_handles_cancel():
    pause = threading.Event()
    stop = threading.Event()
    def cb():
        raise RuntimeError("cancelled")
    ps = PostScheduler(1, pause, stop, cb)
    ps._trigger_post()
    assert not pause.is_set()

