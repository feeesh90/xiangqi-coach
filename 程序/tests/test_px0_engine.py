"""Real subprocess protocol tests; fixture results are never real Px0 acceptance."""
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from engine_manager import EngineManager
from px0_engine import Px0, NoLegalMoves, SearchCancelled, UnsupportedFeature, parse_option
from rules import Position, Move, legal_moves, in_check

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def px(tmp_path, monkeypatch):
    exe = tmp_path / "px0.exe"
    exe.touch()
    network = tmp_path / "network.pb.gz"
    network.write_bytes(b"test fixture only")
    trace = tmp_path / "protocol.log"
    monkeypatch.setenv("FAKE_PX0_TRACE", str(trace))
    real_popen = subprocess.Popen
    processes = []

    def launch(command, **kwargs):
        if str(command[0]) == str(exe):
            command = [sys.executable, "-u", str(ROOT / "tests" / "fake_px0.py")]
        process = real_popen(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    settings = {"enginePath": str(exe), "networkPath": str(network),
                "backend": "cpu", "threads": 2, "startupTimeout": 2, "searchTimeout": 1}
    engine = Px0(ROOT, settings)
    yield engine, settings, trace, processes
    engine.close()
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


def test_options_network_backend_and_repeat_start(px):
    engine, _, trace, processes = px
    engine.start()
    pid = engine.p.pid
    engine.start()
    assert engine.p.pid == pid
    assert engine.cp_enabled
    result = engine.analyze(Position().fen(), 30, 3)
    assert result.best in legal_moves(Position())
    assert len(result.candidates) == 3
    assert result.candidates[0].score_cp == 19
    commands = trace.read_text(encoding="utf-8")
    assert "setoption name WeightsFile value " in commands
    assert "setoption name Backend value cpu" in commands
    assert "setoption name ScoreType value centipawn" in commands
    assert "EvalFile" not in commands and "name Hash" not in commands
    engine.close()
    assert all(p.poll() is not None for p in processes)


def test_option_parser_with_spaces():
    option = parse_option("option name Backend type combo default onnx cpu var onnx cpu var cuda")
    assert option["default"] == "onnx cpu"
    assert option["var"] == ["onnx cpu", "cuda"]


def test_mate_score_preserved(px, monkeypatch):
    engine, _, _, _ = px
    monkeypatch.setenv("FAKE_PX0_MODE", "mate")
    candidate = engine.analyze(Position().fen(), 20).candidates[0]
    assert candidate.mate == -3 and candidate.score_cp is None


def test_bad_saved_configuration_does_not_break_startup(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"pikafish":{"threads":"bad"}}', encoding="utf-8")
    manager = EngineManager(ROOT, path)
    try:
        assert manager.name == "Pikafish" and manager.config_error
    finally:
        manager.close()


@pytest.mark.parametrize("mode", ["time", "depth", "nodes"])
def test_search_modes_and_forced_move(px, mode):
    engine, _, trace, _ = px
    result = engine.analyze_limit(Position().fen(), mode, 20, 1, ["h2e2"])
    assert result.best.ucci() == "h2e2"
    command = "movetime" if mode == "time" else mode
    assert f"go {command} 20 searchmoves h2e2" in trace.read_text(encoding="utf-8")


def test_unknown_backend_rejected(px):
    engine, _, _, _ = px
    engine.backend = "not-a-backend"
    with pytest.raises(ValueError, match="Backend"):
        engine.start()
    assert engine.p is None


@pytest.mark.parametrize("mode", ["wrongengine", "badnet", "crash", "illegal", "hang"])
def test_bad_engine_or_search_terminates_process(px, monkeypatch, mode):
    engine, _, _, processes = px
    monkeypatch.setenv("FAKE_PX0_MODE", mode)
    with pytest.raises((RuntimeError, TimeoutError)):
        engine.analyze(Position().fen(), 20)
    assert engine.p is None
    assert all(p.poll() is not None for p in processes)


def test_absent_files_do_not_spawn(px):
    engine, _, _, processes = px
    engine.network = Path("definitely-missing-px0-network")
    with pytest.raises(FileNotFoundError, match="neural network not found"):
        engine.start()
    assert not processes


def test_missing_scores_are_not_fabricated(px, monkeypatch):
    engine, _, _, _ = px
    monkeypatch.setenv("FAKE_PX0_MODE", "noscore")
    result = engine.analyze(Position().fen(), 20)
    assert len(result.candidates) == 1
    assert result.candidates[0].score_cp is None
    assert result.candidates[0].mate is None


def test_single_pv_degrades_without_setting_unsupported_option(px, monkeypatch):
    engine, _, trace, _ = px
    monkeypatch.setenv("FAKE_PX0_MODE", "single")
    result = engine.analyze(Position().fen(), 20, 3)
    assert len(result.candidates) == 1
    assert "setoption name MultiPV" not in trace.read_text(encoding="utf-8")
    with pytest.raises(UnsupportedFeature):
        engine.configure(hash_mb=128)


def test_cancel_then_search_has_no_stale_bestmove(px, monkeypatch):
    engine, _, _, _ = px
    monkeypatch.setenv("FAKE_PX0_MODE", "slow")
    errors = []
    engine.start()

    def work():
        try:
            engine.analyze(Position().fen(), 100)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    time.sleep(.15)
    engine.stop()
    worker.join(3)
    assert not worker.is_alive()
    assert isinstance(errors[0], SearchCancelled)
    engine.close()
    monkeypatch.setenv("FAKE_PX0_MODE", "normal")
    position = Position()
    position.apply(Move.from_ucci("h2e2"))
    assert engine.analyze(position.fen(), 20).best in legal_moves(position)


def test_sixty_plies_both_sides_and_fen_roundtrip(px):
    engine, _, _, _ = px
    position = Position()
    sides = set()
    for _ in range(60):
        sides.add(position.red)
        assert Position(position.fen()).b == position.b
        result = engine.analyze(position.fen(), 20, 1)
        assert position.apply(result.best)
    assert sides == {True, False}
    engine.new_game()
    assert engine.analyze(Position().fen(), 20).best in legal_moves(Position())


@pytest.mark.parametrize("fen", [
    "3kr4/9/9/9/9/9/9/9/R8/4K4 w - - 0 1",
    "3k5/9/9/9/4p4/9/9/9/9/4K4 w - - 0 1",
    "3k5/9/9/9/4p4/9/9/9/9/4K4 b - - 0 1",
])
def test_endgame_and_check_escape(px, fen):
    engine, _, _, _ = px
    position = Position(fen)
    side = position.red
    result = engine.analyze(fen, 20)
    assert position.apply(result.best)
    assert not in_check(position, side)


def test_no_general_and_no_legal_move_never_search(px):
    engine, _, _, processes = px
    fen = "3k5/9/9/9/9/9/9/9/9/9 w - - 0 1"
    with pytest.raises(NoLegalMoves):
        engine.analyze(fen, 20)
    assert not processes


def test_manager_failed_switch_and_cancelled_session(px, tmp_path):
    _, settings, _, processes = px
    manager = EngineManager(ROOT, tmp_path / "settings.json")
    try:
        manager.switch("px0", settings)
        previous = manager._engine
        assert manager.name == "Px0"
        ticket = manager.ticket()
        manager.stop()
        with pytest.raises(SearchCancelled):
            with manager.session(ticket):
                manager.analyze(Position().fen())
        with pytest.raises(FileNotFoundError):
            manager.switch("px0", {"networkPath": "missing"})
        assert manager._engine is previous
        assert manager.analyze(Position().fen(), 20).best in legal_moves(Position())
        manager.switch("pikafish", {"threads": 1, "hash_mb": 16})
        assert manager.name == "Pikafish"
        assert manager.analyze(Position().fen(), 50, 1).best in legal_moves(Position())
        assert previous.p is None
    finally:
        manager.close()
    assert all(p.poll() is not None for p in processes)
    with pytest.raises(SearchCancelled):
        manager.start()


def test_close_during_search_never_respawns(px, tmp_path, monkeypatch):
    _, settings, _, processes = px
    manager = EngineManager(ROOT, tmp_path / "settings.json")
    manager.switch("px0", settings)
    manager._engine.close()
    monkeypatch.setenv("FAKE_PX0_MODE", "slow")
    errors = []
    def search():
        try:
            manager.analyze(Position().fen(), 5000)
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=search)
    worker.start()
    time.sleep(.15)
    manager.close()
    worker.join(3)
    assert not worker.is_alive()
    assert errors
    assert all(p.poll() is not None for p in processes)


def test_cancel_unresponsive_search_is_bounded(px, monkeypatch):
    engine, _, trace, _ = px
    monkeypatch.setenv("FAKE_PX0_MODE", "hang")
    engine.search_timeout = 30
    engine.start()
    errors = []
    def search():
        try:engine.analyze(Position().fen(), 5000)
        except Exception as exc:errors.append(exc)
    worker = threading.Thread(target=search)
    worker.start()
    deadline = time.monotonic() + 2
    while "go movetime" not in trace.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline
        time.sleep(.01)
    started = time.monotonic()
    engine.stop()
    worker.join(2)
    assert not worker.is_alive()
    assert time.monotonic() - started < 2
    assert isinstance(errors[0], SearchCancelled)
    assert engine.p is None


def test_cancel_switch_before_commit_retains_original(px, tmp_path, monkeypatch):
    _, settings, trace, processes = px
    monkeypatch.setenv("FAKE_PX0_MODE", "slow")
    manager = EngineManager(ROOT, tmp_path / "settings.json")
    ticket = manager.ticket()
    errors = []
    def switching():
        try:manager.switch("px0", settings, ticket)
        except Exception as exc:errors.append(exc)
    worker = threading.Thread(target=switching)
    try:
        worker.start()
        deadline = time.monotonic() + 2
        while not trace.exists() or "go movetime" not in trace.read_text(encoding="utf-8"):
            assert time.monotonic() < deadline
            time.sleep(.01)
        manager.stop()
        worker.join(3)
        assert not worker.is_alive()
        assert errors and manager.name == "Pikafish"
        assert not manager.config_path.exists()
        assert all(p.poll() is not None for p in processes)
    finally:
        manager.close()


@pytest.mark.parametrize("fen", [
    # Checkmate: black rooks cover every red king escape square.
    "3k5/9/9/9/9/9/9/9/3rrr3/4K4 w - - 0 1",
    # Stalemate: no check, but the three palace exits are covered.
    "3k5/9/9/9/9/9/9/3r1r3/r8/4K4 w - - 0 1",
])
def test_terminal_positions_never_spawn(px, fen):
    engine, _, _, processes = px
    # Both are terminal under the application's rules; no bestmove is invented.
    assert not legal_moves(Position(fen))
    with pytest.raises(NoLegalMoves):engine.analyze(fen, 20)
    assert not processes
