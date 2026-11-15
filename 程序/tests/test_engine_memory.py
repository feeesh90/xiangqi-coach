"""Activation persistence, startup fallback and cancellation without real engines."""
import json
from pathlib import Path
import threading

import pytest

from engine import Analysis, Candidate
from engine_manager import EngineManager, DEFAULTS
from px0_engine import SearchCancelled
from rules import Move, Position


class StubEngine:
    def __init__(self,key,profile,trace,fault=None,probe=None):
        self.key=key;self.name={"pikafish":"Pikafish","px0":"Px0"}[key]
        self.profile=dict(profile);self.trace=trace;self.fault=fault;self.probe=probe
        self.ready=False;self.p=None;self.threads=profile.get("threads") or 2;self.hash_mb=16
    def installed(self):return True
    def start(self):
        self.trace.append(("start",self.key))
        if self.fault=="cancel":raise SearchCancelled("cancelled")
        if self.fault=="start":raise RuntimeError("missing executable")
        self.ready=True
    def analyze(self,*args):
        self.trace.append(("search",self.key))
        if self.probe:self.probe()
        if self.fault=="model":raise RuntimeError("invalid neural network or backend")
        move=Move.from_ucci("b0c2")
        return Analysis(move,[Candidate(1,move,[move.ucci()],1)],1,.01)
    def stop(self):self.trace.append(("stop",self.key))
    def _terminate_process(self):self.close()
    def close(self):self.ready=False;self.trace.append(("close",self.key))


@pytest.fixture
def factory(tmp_path,monkeypatch):
    trace=[];faults={};probes={};instances=[];managers=[]
    def create(manager,key,profile):
        if key not in DEFAULTS:raise ValueError("unknown engine")
        if faults.get(key)=="construct":raise ValueError("bad profile")
        engine=StubEngine(key,profile,trace,faults.get(key),probes.get(key))
        instances.append(engine)
        return engine
    monkeypatch.setattr(EngineManager,"_create",create)
    monkeypatch.setattr(EngineManager,"_setup_logging",lambda self:None)
    def make(saved=None,path=None):
        path=path or tmp_path/"engines.json"
        if saved is not None:
            path.write_text(json.dumps(saved),encoding="utf-8")
        manager=EngineManager(tmp_path,config_path=path)
        managers.append(manager)
        return manager
    yield make,trace,faults,probes,instances
    for manager in managers:manager.close()


@pytest.mark.parametrize("selected",["pikafish","px0"])
def test_successful_activation_is_restored_by_a_new_manager(factory,selected):
    make,trace,_,_,_=factory
    manager=make({"pikafish":{"threads":4},"px0":{"threads":3,"backend":"fixture"}})
    manager.switch(selected)
    saved=json.loads(manager.config_path.read_text(encoding="utf-8"))
    assert saved["selected"]==selected
    assert saved["pikafish"]["threads"]==4 and saved["px0"]["backend"]=="fixture"
    manager.close();trace.clear()
    reopened=make(path=manager.config_path)
    assert reopened.preferred==selected and not trace  # Reading starts no process.
    before=reopened.config_path.read_bytes()
    assert before==manager.config_path.read_bytes()
    reopened.restore_startup()
    assert reopened.selected==selected and reopened.ready
    assert [x for x in trace if x[0]=="start"]==[("start",selected)]
    assert ("search",selected) in trace


@pytest.mark.parametrize("saved",[
    {},{"selected":"unknown"},{"selected":[]},{"selected":True},
])
def test_legacy_or_invalid_selection_keeps_profiles_and_does_not_write(factory,saved):
    make,trace,_,_,_=factory
    data={**saved,"pikafish":{"threads":6},"px0":{"threads":3}}
    manager=make(data)
    before=manager.config_path.read_bytes()
    assert manager.preferred=="pikafish" and manager.settings["pikafish"]["threads"]==6
    assert manager.settings["px0"]["threads"]==3
    assert manager.config_path.read_bytes()==before and not trace


@pytest.mark.parametrize("content",[b"{",b"[]",b"null",b"\xff"])
def test_malformed_or_bad_encoding_configuration_is_safe(factory,content,tmp_path):
    make,trace,_,_,_=factory
    path=tmp_path/"malformed.json";path.write_bytes(content)
    manager=make(path=path)
    assert manager.preferred=="pikafish" and manager.config_error
    assert path.read_bytes()==content and not trace


def test_missing_and_unreadable_config_does_not_break_construction(factory,monkeypatch):
    make,trace,_,_,_=factory
    manager=make()
    assert not manager.config_path.exists() and manager.preferred=="pikafish"
    original=Path.read_text
    def read(path,*args,**kwargs):
        if path==manager.config_path:raise PermissionError("denied")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",read)
    denied=make(path=manager.config_path)
    assert denied.preferred=="pikafish" and denied.config_error and not trace


@pytest.mark.parametrize("fault",["construct","start","model"])
def test_restored_px0_failure_closes_candidate_and_remembers_fallback(factory,fault):
    make,trace,faults,_,instances=factory
    manager=make({"selected":"px0","px0":{"backend":"fixture"}})
    faults["px0"]=fault
    assert manager.restore_startup()=="Pikafish"
    assert manager.ready and manager.selected=="pikafish" and "恢复 Px0 失败" in manager.startup_notice
    assert json.loads(manager.config_path.read_text(encoding="utf-8"))["selected"]=="pikafish"
    assert all(not e.ready for e in instances if e.key=="px0")
    if fault!="construct":
        assert trace.index(("close","px0"))<trace.index(("start","pikafish"))


def test_both_startups_fail_without_overwriting_memory(factory):
    make,trace,faults,_,_=factory
    manager=make({"selected":"px0"})
    before=manager.config_path.read_bytes()
    faults.update(px0="start",pikafish="start")
    with pytest.raises(RuntimeError,match="也无法启动"):
        manager.restore_startup()
    assert manager.startup_failed and not manager.ready
    assert manager.config_path.read_bytes()==before
    assert [x for x in trace if x[0]=="start"]==[("start","px0"),("start","pikafish")]


def test_failed_manual_switch_keeps_actual_engine_and_saved_choice(factory):
    make,_,faults,_,_=factory
    manager=make();manager.switch("px0")
    before=manager.config_path.read_bytes();old=manager._engine
    faults["pikafish"]="model"
    with pytest.raises(RuntimeError):
        manager.switch("pikafish")
    assert manager.selected=="px0" and manager._engine is old
    assert manager.config_path.read_bytes()==before


@pytest.mark.parametrize("when",["stage","replace"])
def test_save_failure_keeps_healthy_engine_and_old_atomic_file(factory,monkeypatch,when):
    make,_,_,_,_=factory
    manager=make({"selected":"pikafish","pikafish":{"threads":4}})
    before=manager.config_path.read_bytes()
    if when=="stage":
        monkeypatch.setattr(manager,"_stage_settings",lambda *args:(_ for _ in ()).throw(PermissionError("denied")))
    else:
        replace=Path.replace
        def fail(path,target):
            if Path(target)==manager.config_path:raise PermissionError("locked")
            return replace(path,target)
        monkeypatch.setattr(Path,"replace",fail)
    assert manager.switch("px0")=="Px0" and manager.ready
    assert manager.selected=="px0" and "未能保存" in manager.save_error
    assert manager.config_path.read_bytes()==before
    assert not list(manager.config_path.parent.glob("*.tmp"))


def test_cancelled_startup_does_not_attempt_fallback(factory):
    make,trace,faults,_,_=factory
    manager=make({"selected":"px0"})
    before=manager.config_path.read_bytes()
    faults["px0"]="cancel"
    with pytest.raises(SearchCancelled):manager.restore_startup()
    assert ("start","pikafish") not in trace
    assert manager.config_path.read_bytes()==before


def test_cancellation_after_staging_before_commit_changes_neither_memory_nor_disk(factory,monkeypatch):
    make,_,_,_,_=factory
    manager=make({"selected":"pikafish"})
    before=manager.config_path.read_bytes();old=manager._engine
    staged=threading.Event();release=threading.Event();errors=[]
    stage=manager._stage_settings
    def wait_before_commit(*args):
        path=stage(*args);staged.set()
        assert release.wait(3)
        return path
    monkeypatch.setattr(manager,"_stage_settings",wait_before_commit)
    def work():
        try:manager.switch("px0")
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=work);worker.start()
    try:
        assert staged.wait(2)
        manager.stop();release.set();worker.join(3)
        assert not worker.is_alive()
        assert errors and isinstance(errors[0],SearchCancelled)
        assert manager._engine is old and manager.selected=="pikafish"
        assert manager.config_path.read_bytes()==before
        assert not list(manager.config_path.parent.glob("*.tmp"))
    finally:release.set();worker.join(3)


def test_close_during_model_probe_never_remembers_or_respawns(factory):
    make,trace,_,probes,instances=factory
    manager=make({"selected":"px0"})
    before=manager.config_path.read_bytes()
    started=threading.Event();release=threading.Event();errors=[]
    def probe():started.set();assert release.wait(3)
    probes["px0"]=probe
    def work():
        try:manager.restore_startup()
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=work);worker.start()
    assert started.wait(2)
    closer=threading.Thread(target=manager.close);closer.start()
    # stop() has published its cancellation before the short probe is released.
    deadline=threading.Event()
    for _ in range(100):
        if manager._closed:break
        deadline.wait(.005)
    release.set();worker.join(3);closer.join(3)
    assert not worker.is_alive() and not closer.is_alive()
    assert errors and isinstance(errors[0],SearchCancelled)
    assert all(not instance.ready for instance in instances)
    assert manager.config_path.read_bytes()==before
    assert ("start","pikafish") not in trace
