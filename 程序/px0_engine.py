"""Px0 UCI adapter. Shares process transport, never Pikafish's NNUE options.

Protocol reference: official-pikafish/px0 src/chess/uciloop.cc and
src/neural/shared_params.cc. Runtime option declarations remain authoritative.
"""
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from engine import Pikafish, Analysis, Candidate
from rules import Position, Move, legal_moves, START_FEN

LOG = logging.getLogger("xiangqi.engine")


class UnsupportedFeature(RuntimeError):
    pass


class SearchCancelled(RuntimeError):
    pass


class NoLegalMoves(RuntimeError):
    pass


def parse_option(line):
    match = re.fullmatch(r"option name (.+?) type (\w+)(.*)", line)
    if not match:
        return None
    name, kind, tail = match.groups()
    result = {"name": name, "type": kind, "var": []}
    for item in re.finditer(r"\s(default|min|max|var)\s(.*?)(?=\s(?:default|min|max|var)\s|$)", tail):
        key, value = item.groups()
        if key == "var":
            result[key].append(value)
        else:
            result[key] = value
    return result


class Px0(Pikafish):
    """Reuse tested stdin/stdout queue, locks, termination and move data types."""
    def __init__(self, root, settings=None):
        super().__init__(root)
        self.settings = dict(settings or {})
        self.name = "Px0"
        self.exe = self._path(self.settings.get("enginePath", "engine/px0/px0.exe"))
        self.network = self._path(self.settings.get("networkPath", "engine/px0/network.pb.gz"))
        self.backend = self.settings.get("backend", "").strip()
        self.threads = int(self.settings.get("threads", 2))
        self.start_timeout = float(self.settings.get("startupTimeout", 60))
        self.search_timeout = float(self.settings.get("searchTimeout", 30))
        if not 1 <= self.start_timeout <= 300 or not 1 <= self.search_timeout <= 300:
            raise ValueError("Px0 timeout must be between 1 and 300 seconds")
        self.options = {}
        self.handshake = []
        self.engine_id = ""
        self.cp_enabled = False

    def _path(self, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.root / path).resolve()

    def installed(self):
        return self.exe.is_file() and self.network.is_file()

    def _send(self, command):
        if "\n" in command or "\r" in command:
            raise ValueError("UCI command contains a newline")
        LOG.info("[Engine] %s command: %s", self.name, command)
        super()._send(command)

    def _read_line(self, timeout, p=None):
        try:
            line = super()._read_line(timeout, p)
        except (RuntimeError, TimeoutError) as exc:
            raise type(exc)(str(exc).replace("Pikafish", "Px0")) from exc
        stripped = line.strip()
        if re.match(r"(?:error\b|exception\b|fatal\b|CRITICAL ERROR)", stripped, re.I):
            raise RuntimeError("Px0: " + stripped)
        return line

    def _option(self, name, value, required=True):
        option = self.options.get(name)
        if option is None:
            if required:
                raise UnsupportedFeature(f"Px0 feature unsupported: {name}")
            return False
        value = str(value)
        if option["type"] == "spin":
            number = int(value)
            if not int(option.get("min", number)) <= number <= int(option.get("max", number)):
                raise ValueError(f"Px0 {name} 超出引擎声明的范围")
        if option["type"] == "combo" and value not in option["var"]:
            raise ValueError(f"Px0 {name} 不支持 {value}；可选：{', '.join(option['var'])}")
        self._send(f"setoption name {name} value {value}")
        return True

    def start(self):
        with self.lock:
            if self.ready and self.p and self.p.poll() is None:
                return
            if not self.exe.is_file():
                raise FileNotFoundError(f"Px0 executable not found: {self.exe}")
            if not self.network.is_file():
                raise FileNotFoundError(f"Px0 neural network not found: {self.network}")
            self._terminate_process()
            self.options = {}
            self.handshake = []
            LOG.info("[Engine] Starting Px0: %s", self.exe)
            self.p = subprocess.Popen(
                [str(self.exe)], cwd=str(self.exe.parent), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._ensure_reader()
            try:
                self._send("uci")
                deadline = time.monotonic() + self.start_timeout
                while True:
                    line = self._read_line(deadline - time.monotonic()).strip()
                    self.handshake.append(line)
                    if line.startswith("id name "):
                        self.engine_id = line[8:]
                    option = parse_option(line)
                    if option:
                        self.options[option["name"]] = option
                    if line == "uciok":
                        break
                if "px0" not in self.engine_id.lower():
                    raise RuntimeError(f"Expected Px0, received {self.engine_id!r}; 不接受国际象棋 Lc0")
                # The engine uses narrow file APIs on Windows. Prefer a relative
                # path so a Chinese project-directory name never reaches fopen.
                network_arg = os.path.relpath(self.network, self.exe.parent) if self.network.drive == self.exe.drive else str(self.network)
                if os.name == "nt" and not network_arg.isascii():
                    import ctypes
                    buffer = ctypes.create_unicode_buffer(32768)
                    if ctypes.windll.kernel32.GetShortPathNameW(str(self.network), buffer, len(buffer)):
                        network_arg = buffer.value
                    if not network_arg.isascii():
                        raise ValueError("Px0 模型路径含不支持的字符，请将模型放到引擎目录并使用英文文件名")
                self._option("WeightsFile", network_arg)
                if self.backend:
                    self._option("Backend", self.backend)
                self._option("Threads", self.threads, required=False)
                backend_options = self.settings.get("backendOptions", "").strip()
                if backend_options:
                    self._option("BackendOptions", backend_options)
                score_type = self.options.get("ScoreType", {})
                self.cp_enabled = "centipawn" in score_type.get("var", [])
                if self.cp_enabled:
                    self._option("ScoreType", "centipawn")
                # Px0 loads the backend on SetPosition, not on isready alone.
                self._send("position fen " + START_FEN)
                self._send("isready")
                self._read_until("readyok", self.start_timeout)
                self.current_multipv = None
                self.ready = True
                LOG.info("[Engine] Px0 ready: %s; options=%s", self.engine_id, self.options)
            except Exception:
                self._terminate_process()
                raise

    def configure(self, threads=None, hash_mb=None):
        if hash_mb is not None:
            raise UnsupportedFeature("Px0 feature unsupported: Pikafish Hash MB；请使用 backend 配置")
        with self.lock:
            if threads is not None:
                self.threads = int(threads)
                if self.ready:
                    self._option("Threads", self.threads)
            self.cache.clear()

    def analyze(self, fen, movetime=10000, multipv=3, searchmoves=None):
        return self.analyze_limit(fen, "time", movetime, multipv, searchmoves)

    def analyze_limit(self, fen, mode="time", value=700, multipv=3, searchmoves=None):
        if mode not in ("time", "depth", "nodes"):
            raise ValueError("mode must be time/depth/nodes")
        position = Position(fen)
        legal = legal_moves(position)
        if not legal:
            raise NoLegalMoves("棋局已结束：当前一方没有合法着法")
        allowed = None
        if searchmoves is not None:
            requested = [searchmoves] if isinstance(searchmoves, str) else list(searchmoves)
            if not requested or any(Move.from_ucci(m) not in legal for m in requested):
                raise ValueError("searchmoves contains an illegal move")
            allowed = {Move.from_ucci(m) for m in requested}
        with self.lock:
            self.start()
            count = max(1, min(3, int(multipv)))
            if "MultiPV" in self.options:
                count = min(count, int(self.options["MultiPV"].get("max", count)))
                if self.current_multipv != count:
                    self._option("MultiPV", count)
                    self.current_multipv = count
            else:
                count = 1  # Honest single-PV degradation; never invent alternatives.
            value = max(1, int(value))
            command = f"go {'movetime' if mode == 'time' else mode} {value}"
            if allowed is not None:
                command += " searchmoves " + " ".join(m.ucci() for m in sorted(allowed, key=lambda m:m.ucci()))
            generation = self.cancel_generation
            started = time.monotonic()
            deadline = started + (value / 1000 if mode == "time" else 0) + self.search_timeout
            latest = {}
            depth = 0
            try:
                self._send("position fen " + fen)
                self._send(command)
                cancel_deadline = None
                while True:
                    if generation != self.cancel_generation:
                        if cancel_deadline is None:
                            cancel_deadline = time.monotonic() + 0.75
                        if time.monotonic() >= cancel_deadline:
                            self._terminate_process()
                            raise SearchCancelled("Px0 未响应 stop，已终止搜索进程")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Px0 分析超时，已终止无响应的引擎")
                    try:
                        line = self._read_line(min(.2, remaining)).strip()
                    except TimeoutError:
                        continue
                    if line.startswith("bestmove "):
                        best = Move.from_ucci(line.split()[1])
                        LOG.info("[Engine] Px0 %s", line)
                        break
                    if not line.startswith("info ") or line.startswith("info string "):
                        continue
                    dep = self._parse_int(line, "depth")
                    depth = max(depth, dep)
                    pv_match = re.search(r"\bpv\s+(.+)", line)
                    if not pv_match:
                        continue
                    # Validate the full PV against the same rules used by the GUI.
                    probe = position.copy()
                    pv = []
                    for token in pv_match[1].split():
                        move = Move.from_ucci(token)
                        if move is None or move not in legal_moves(probe):
                            break
                        pv.append(move.ucci())
                        probe.apply_unchecked(move)
                    if not pv:
                        continue
                    move = Move.from_ucci(pv[0])
                    if allowed is not None and move not in allowed:
                        continue
                    rank = self._parse_int(line, "multipv", 1)
                    cp = re.search(r"\bscore cp (-?\d+)\b", line)
                    mate = re.search(r"\bscore mate (-?\d+)\b", line)
                    latest[rank] = Candidate(rank, move, pv, dep,
                        int(cp[1]) if cp and self.cp_enabled else None,
                        int(mate[1]) if mate else None)
                if generation != self.cancel_generation:
                    raise SearchCancelled("Px0 search cancelled")
                if best not in legal or (allowed is not None and best not in allowed):
                    raise RuntimeError("Px0 返回非法或空 bestmove，已拒绝落子")
                candidates = [latest[k] for k in sorted(latest)]
                first = next((c for c in candidates if c.move == best), None)
                if first is None:
                    first = Candidate(1, best, [best.ucci()], depth, None, None)
                candidates = [first] + [c for c in candidates if c.move != best]
                candidates = candidates[:count]
                for rank, candidate in enumerate(candidates, 1):
                    candidate.multipv = rank
                return Analysis(best, candidates, depth, time.monotonic() - started)
            except SearchCancelled:
                raise
            except Exception:
                self._terminate_process()
                raise

    def new_game(self):
        with self.lock:
            self.start()
            try:
                self._send("ucinewgame")
                self._send("isready")
                self._read_until("readyok", self.start_timeout)
            except Exception:
                self._terminate_process()
                raise

    def close(self):
        super().close()
        LOG.info("[Engine] Px0 stopped")
