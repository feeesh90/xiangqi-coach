"""Actual Tk startup callbacks with controlled engine adapters."""
import gc
import json

from app import App
from test_board_orientation import gui, wait
from test_engine_memory import factory

startup_check=App._engine_check
schedule_opponent=App._schedule_opponent


def test_startup_uses_remembered_engine_even_when_placeholder_is_not_installed(factory,gui,monkeypatch):
    make,trace,_,_,_=factory
    a=gui
    a.engine.preferred="px0"
    monkeypatch.setattr(a.engine,"installed",lambda:False)
    startup_check(a)
    assert a.busy
    wait(a,lambda:not a.busy)
    assert a.engine.selected=="px0" and a.engine.ready
    assert "Px0 已就绪" in a.engine_badge.cget("text")
    assert [item for item in trace if item[0]=="start"]==[("start","px0")]
    assert json.loads(a.engine.config_path.read_text(encoding="utf-8"))["selected"]=="px0"
    b=App(preferences_path=a.ui_preferences.path,engine_config_path=a.engine.config_path)
    try:
        b.update();b.withdraw()
        assert b.engine.preferred=="px0" and b.human_red and b.board_view_mode=="auto"
        assert b._view_flipped() and "黑方在下" in b.sim_label.cget("text")
        item,=b.canvas.find_withtag("piece:9:4")
        assert b.canvas.coords(item)[1]<b.canvas.winfo_height()/2
        startup_check(b)
        wait(b,lambda:not b.busy)
        assert b.engine.selected=="px0" and b.engine.ready
    finally:
        b._close();b.engine.close();b.__dict__.clear();gc.collect()


def test_startup_fallback_and_save_warning_are_visible(factory,gui,monkeypatch):
    _,_,faults,_,_=factory
    a=gui
    a.engine.preferred="px0";faults["px0"]="model"
    startup_check(a);wait(a,lambda:not a.busy)
    assert a.engine.selected=="pikafish" and a.engine.ready
    assert "Pikafish 已就绪" in a.engine_badge.cget("text")
    assert "恢复 Px0 失败" in a.coach_text.get("1.0","end")
    assert "已回退 Pikafish" in a.status.cget("text")
    faults.clear()
    def denied(*args):raise PermissionError("denied")
    monkeypatch.setattr(a.engine,"_stage_settings",denied)
    a._switch_engine("px0",{})
    wait(a,lambda:not a.busy)
    assert a.engine.selected=="px0" and a.engine.ready
    assert "未能保存" in a.status.cget("text") and "未能保存" in a.coach_text.get("1.0","end")


def test_unavailable_startup_keeps_ui_usable_and_does_not_schedule_retry(factory,gui):
    _,trace,faults,_,_=factory
    a=gui
    a.engine.preferred="px0";faults.update(px0="model",pikafish="start")
    startup_check(a);wait(a,lambda:not a.busy)
    assert a.engine.startup_failed and not a.engine.ready
    assert a.engine_badge.cget("text")=="离线引擎不可用"
    before=list(trace)
    a.human_red=False
    schedule_opponent(a)
    assert a.opponent_job is None and trace==before
    a.flip_board_button.invoke()
    a._set_mode("pvp")
    assert a.mode=="pvp" and not a.busy


def test_close_cancels_startup_and_periodic_book_callbacks(factory,gui):
    a=gui
    a._refresh_book_badge()
    owned=[job for job in (a.engine_check_after,a.book_refresh_after,a.ui_pump_after) if job]
    assert owned
    a._close();a.engine.close()
    pending=a.tk.call("after","info")
    assert all(job not in pending for job in owned)
