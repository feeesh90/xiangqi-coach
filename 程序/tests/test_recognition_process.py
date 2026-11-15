import sys
import threading
import time
import pytest
from recognition_process import RecognitionJob, RecognitionCancelled
from rules import Position
import json


def command(code):return [sys.executable,"-X","utf8","-c",code]


def test_valid_bounded_protocol():
    response=json.dumps({"ok":True,"result":{"board":Position().b,"uncertain":[]}})
    job=RecognitionJob(command=command("import sys;sys.stdin.read();print("+repr(response)+")"))
    assert job.run("中文图片 (1).png")["board"]==Position().b
    assert job.finished.is_set() and job.process is None


def test_oversized_request_finishes_without_child():
    job=RecognitionJob()
    with pytest.raises(ValueError,match="过长"):job.run("x"*20000)
    assert job.finished.is_set() and job.process is None


def test_long_request_to_child_not_reading_stdin_still_times_out():
    job=RecognitionJob(timeout=.25,command=command("import time;time.sleep(30)"))
    started=time.monotonic()
    with pytest.raises(RuntimeError,match="超时"):job.run("x"*12000)
    assert time.monotonic()-started<3
    assert job.finished.is_set() and job.process is None


@pytest.mark.parametrize("code,message",[
    ("print('invalid')","无效数据"),
    ("import sys;sys.exit(5)","无效数据"),
    ("import json; print(json.dumps({'ok':False,'error':'模型损坏'}));raise SystemExit(1)","模型损坏"),
    ("print('x'*1000000)","输出过长"),
    ("import time;time.sleep(30)","超时"),
])
def test_bad_or_stuck_child_reaped(code,message):
    job=RecognitionJob(timeout=.25,command=command(code))
    with pytest.raises(RuntimeError,match=message):job.run("test.png")
    assert job.process is None and job.finished.is_set()


def test_cancel_before_and_during_spawn():
    job=RecognitionJob(command=command("import time;time.sleep(30)"))
    job.cancel()
    with pytest.raises(RecognitionCancelled):job.run("test.png")
    job=RecognitionJob(command=command("import time;time.sleep(30)"))
    errors=[]
    def run():
        try:job.run("test.png")
        except Exception as e:errors.append(e)
    t=threading.Thread(target=run);t.start()
    deadline=time.monotonic()+2
    while job.process is None and time.monotonic()<deadline:time.sleep(.01)
    child=job.process
    job.cancel();t.join(3)
    assert not t.is_alive() and child.poll() is not None
    assert isinstance(errors[0],RecognitionCancelled)
    assert job.finished.is_set() and job.process is None
