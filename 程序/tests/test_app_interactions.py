import queue
import threading

from app import App
from engine import Analysis, Candidate
from rules import Move, Position


class DummyWidget:
    def __init__(self):
        self.values = {}

    def configure(self, **values):
        self.values.update(values)


class DisabledApi:
    settings = {"auto_explain": False}

    def configured(self):
        return False


def bare_app():
    app = App.__new__(App)
    app.sim = Position()
    app.official = Position()
    app.mode = "ai"
    app.busy = False
    app.task_token = 0
    app.api_request_token = 0
    app.api_last_feedback = ""
    app.closing = False
    app.ui_queue = queue.Queue()
    app.manual_takeover = False
    app.opponent_job = None
    app.sim_history = []
    app.sim_move_history = []
    app.move_history = []
    app.last = None
    app.reco = None
    app.selected = None
    app.targets = []
    app.playback_moves = []
    app.playback_index = 0
    app.playback_after = None
    app.playback_paused = False
    app.playback_explanations = []
    app.playback_back = DummyWidget()
    app.playback_pause = DummyWidget()
    app.playback_next = DummyWidget()
    app.status = DummyWidget()
    app.api_explain_btn = DummyWidget()
    app.api = DisabledApi()
    app.analysis_snapshot = None
    app.analysis_result = None
    app.active_plan = 0
    app.draw = lambda: None
    app._update_turn = lambda: None
    app._set_coach = lambda *args: None
    app._clear_plan_buttons = lambda: None
    app._cancel_opponent_job = lambda: None
    app.callbacks = []
    app.after = lambda delay, callback: app.callbacks.append(callback) or len(app.callbacks)
    app.after_cancel = lambda job: None
    return app


def test_four_ply_simulation_plays_one_step_at_a_time():
    app = bare_app()
    pv = ["h2e2", "h9g7", "h0g2", "i9h9"]
    first = Move.from_ucci(pv[0])
    analysis = Analysis(first, [Candidate(1, first, pv, 12)], 12, 0.2)

    app._apply_pv_four(analysis, app.sim.copy())

    assert app.sim_move_history == []
    assert len(app.callbacks) == 1
    app.callbacks.pop(0)()
    assert app.sim_move_history == ["h2e2"]
    assert app.playback_index == 1


def test_scheduled_ai_does_not_move_after_manual_takeover():
    app = bare_app()
    app.sim = None
    app.official.apply_unchecked(Move.from_ucci("h2e2"))
    app.manual_takeover = True
    calls = []
    app.opponent_move = lambda: calls.append("ai")

    app._run_scheduled_opponent()

    assert calls == []


def test_api_guided_take_uses_deepseek_choice_from_pikafish_candidates():
    app = bare_app()
    snap = app.sim.copy()
    first = Move.from_ucci("h2e2")
    second = Move.from_ucci("c3c4")
    analysis = Analysis(first, [
        Candidate(1, first, ["h2e2", "h9g7"], 15, 18, None),
        Candidate(2, second, ["c3c4", "h9g7"], 15, 12, None),
    ], 15, 0.4)

    class Engine:
        def analyze(self, fen, ms, multipv):
            assert fen == snap.fen()
            assert multipv == 3
            return analysis

    class GuidedApi:
        def configured(self):
            return True

        def choose_move(self, context, allowed):
            assert allowed == ["h2e2", "c3c4"]
            assert context["candidates"][0]["pv"] == "h2e2 h9g7"
            return {"move":"c3c4", "reason":"DeepSeek选择更灵活的兵七进一"}

    app.engine = Engine()
    app.api = GuidedApi()
    result = app._api_guided_take_work(snap, 1300)

    assert result["move"] == second
    assert result["source"] == "api"
    assert "更灵活" in result["reason"]


def test_api_guided_take_falls_back_to_pikafish_on_api_failure():
    app = bare_app()
    snap = app.sim.copy()
    best = Move.from_ucci("h2e2")
    analysis = Analysis(best, [Candidate(1, best, ["h2e2"], 12)], 12, 0.3)

    class Engine:
        def analyze(self, fen, ms, multipv):
            return analysis

    class FailedApi:
        def choose_move(self, context, allowed):
            raise RuntimeError("API timeout")

    app.engine = Engine()
    app.api = FailedApi()
    result = app._api_guided_take_work(snap, 1300)

    assert result["move"] == best
    assert result["source"] == "pikafish_fallback"
    assert "timeout" in result["reason"]


def test_api_explanation_from_old_position_is_discarded_after_move():
    app = bare_app()
    started = threading.Event()
    release = threading.Event()

    class SlowApi:
        def configured(self):
            return True

        def explain_xiangqi(self, context):
            started.set()
            assert release.wait(1)
            return "这是旧局面的解释"

    app.api = SlowApi()
    appended = []
    app._append_coach = appended.append
    app._request_api_context({"fen": app.sim.fen()})
    assert started.wait(1)

    app.make_move(Move.from_ucci("h2e2"), auto_ai=False)
    release.set()
    callback = app.ui_queue.get(timeout=1)
    callback()

    assert appended == []
    assert app.api_last_feedback == ""
    assert app.api_explain_btn.values["state"] == "normal"
