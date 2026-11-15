"""One active engine, serialized calls and generation-bound worker sessions."""
import copy
from contextlib import contextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import tempfile

from api_client import CONFIG_DIR
from engine import Pikafish
from px0_engine import Px0, SearchCancelled, NoLegalMoves
from rules import Position, Move, legal_moves

DEFAULTS = {
    "pikafish": {"threads": None, "hash_mb": 512},
    "px0": {"enginePath": "engine/px0/px0.exe",
            "networkPath": "engine/px0/network.pb.gz", "backend": "",
            "backendOptions": "", "threads": 2,
            "startupTimeout": 60, "searchTimeout": 30},
}


class EngineManager:
    def __init__(self, root, config_path=None):
        self.root = Path(root)
        self.config_path = Path(config_path) if config_path else CONFIG_DIR / "engine_settings.json"
        self.settings = copy.deepcopy(DEFAULTS)
        self.config_error = ""
        self.preferred = "pikafish"
        self.startup_notice = ""
        self.save_error = ""
        self.startup_failed = False
        try:
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("引擎配置必须是 JSON 对象")
            for key in DEFAULTS:
                profile = saved.get(key, {})
                if isinstance(profile, dict):
                    self.settings[key].update(profile)
                else:
                    self.config_error += f"{key} 参数无效，使用默认参数。"
            preferred = saved.get("selected", "pikafish")
            if isinstance(preferred, str) and preferred in DEFAULTS:
                self.preferred = preferred
            else:
                self.config_error += "保存的引擎选择无效，使用 Pikafish。"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.config_error = f"引擎配置读取失败，使用默认值：{exc}"
            self.settings = copy.deepcopy(DEFAULTS)
        self._lock = threading.RLock()
        # Only short publication/file-replace operations hold this lock.
        # stop() must never wait for model loading or an ongoing search.
        self._state_lock = threading.RLock()
        self._local = threading.local()
        self._epoch = 0
        self._closed = False
        self._pending = None
        self.selected = "pikafish"  # Inert placeholder; no process starts here.
        try:
            self._engine = self._create(self.selected, self.settings[self.selected])
        except (ValueError, TypeError) as exc:
            self.config_error = f"Pikafish 配置无效，使用默认值：{exc}"
            self.settings["pikafish"] = copy.deepcopy(DEFAULTS["pikafish"])
            self._engine = self._create(self.selected, self.settings[self.selected])
        self._setup_logging()

    def _setup_logging(self):
        logger = logging.getLogger("xiangqi.engine")
        logger.setLevel(logging.INFO)
        path = self.root / "data" / "logs" / "engine.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not any(getattr(h, "baseFilename", None) == str(path.resolve()) for h in logger.handlers):
                handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())

    def _create(self, key, settings):
        if key == "px0":
            return Px0(self.root, settings)
        if key != "pikafish":
            raise ValueError("Unknown engine: " + key)
        engine = Pikafish(self.root)
        engine.configure(settings.get("threads"), settings.get("hash_mb", 512))
        return engine

    @property
    def name(self):
        return self._engine.name

    @property
    def threads(self):
        return self._engine.threads

    @property
    def hash_mb(self):
        return self._engine.hash_mb

    @property
    def ready(self):
        return self._engine.ready

    def installed(self):
        return self._engine.installed()

    def ticket(self):
        return self._epoch

    @contextmanager
    def session(self, ticket):
        previous = getattr(self._local, "ticket", None)
        self._local.ticket = ticket
        try:
            self._check(ticket)
            yield
        finally:
            self._local.ticket = previous

    def _check(self, ticket):
        if self._closed or ticket != self._epoch:
            raise SearchCancelled("引擎任务已取消或引擎已切换")

    def _invoke(self, method, *args, **kwargs):
        ticket = getattr(self._local, "ticket", None)
        if ticket is None:
            ticket = self.ticket()
        with self._lock:
            self._check(ticket)
            logging.getLogger("xiangqi.engine").info("[Engine] %s %s %s", self.name, method, args)
            result = getattr(self._engine, method)(*args, **kwargs)
            self._check(ticket)
            return result

    def start(self):
        return self._invoke("start")

    def restore_startup(self):
        """Worker entry: validate the remembered engine, with one Px0 fallback."""
        ticket = getattr(self._local, "ticket", None)
        if ticket is None:
            ticket = self.ticket()
        target = self.preferred
        self.startup_notice = ""
        try:
            return self.switch(target, ticket=ticket)
        except SearchCancelled:
            raise
        except Exception as exc:
            self._check(ticket)
            if target != "px0":
                self.startup_failed = True
                raise RuntimeError(f"Pikafish 启动失败：{exc}。请检查引擎设置；可使用本地双人和创造模式。") from exc
            self.startup_notice = f"恢复 Px0 失败：{exc}。已回退 Pikafish。"
            logging.getLogger("xiangqi.engine").warning(self.startup_notice)
            try:
                return self.switch("pikafish", ticket=ticket)
            except SearchCancelled:
                raise
            except Exception as fallback:
                self._check(ticket)
                self.startup_failed = True
                self.startup_notice = ""
                raise RuntimeError(f"恢复 Px0 失败：{exc}；Pikafish 也无法启动：{fallback}。"
                                   "请检查引擎设置；可使用本地双人和创造模式。") from fallback

    def analyze(self, fen, movetime=10000, multipv=3, searchmoves=None):
        return self.analyze_limit(fen, "time", movetime, multipv, searchmoves)

    def analyze_limit(self, fen, mode="time", value=700, multipv=3, searchmoves=None):
        legal = legal_moves(Position(fen))
        if not legal:
            raise NoLegalMoves("棋局已结束：当前一方没有合法着法")
        allowed = set(legal)
        if searchmoves is not None:
            requested = [searchmoves] if isinstance(searchmoves, str) else list(searchmoves)
            if not requested:
                raise ValueError("searchmoves 不能为空")
            if any(not isinstance(value,str) for value in requested):
                raise ValueError("searchmoves 必须使用 UCCI 字符串")
            parsed = [Move.from_ucci(value) for value in requested]
            if any(move not in allowed for move in parsed):
                raise ValueError("searchmoves 包含不合法的着法")
            allowed = set(parsed)
            searchmoves = sorted(move.ucci() for move in allowed)
        result = self._invoke("analyze_limit", fen, mode, value, multipv, searchmoves)
        if result.best not in allowed:
            raise RuntimeError(f"{self.name} 返回非法或候选集合外的着法，已拒绝落子")
        # The adapter may have cached this object. Never edit its candidates.
        result = copy.deepcopy(result)
        result.candidates = [c for c in result.candidates if c.move in allowed]
        for rank, candidate in enumerate(result.candidates, 1):
            candidate.multipv = rank
        return result

    def analyze_coach(self, fen, movetime=1500, with_alts=True):
        return self.analyze(fen, movetime, 3 if with_alts else 1)

    def new_game(self):
        return self._invoke("new_game")

    def configure(self, threads=None, hash_mb=None):
        return self._invoke("configure", threads, hash_mb)

    def stop(self):
        with self._state_lock:
            self._epoch += 1
            engine, pending = self._engine, self._pending
        engine.stop()
        if pending:
            pending.stop()
            pending._terminate_process()

    def _stage_settings(self, settings, selected):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=self.config_path.parent, prefix=self.config_path.name+".",
                    suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({**settings, "selected": selected}, stream, ensure_ascii=False, indent=2)
            return temporary
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise

    def switch(self, key, settings=None, ticket=None):
        """Run on worker. Failed/cancelled startup rolls back; old engine stays usable."""
        if ticket is None:
            self.stop()
            ticket = self.ticket()
        with self._lock:
            self._check(ticket)
            profile = dict(self.settings.get(key, {}))
            profile.update(settings or {})
            candidate = self._create(key, profile)
            old = self._engine
            temporary = None
            try:
                with self._state_lock:
                    self._check(ticket)
                    self._pending = candidate
                old.close()
                candidate.start()
                self._check(ticket)
                # A real search also validates backend inference and coordinate format.
                trial = candidate.analyze(Position().fen(), 100, 1)
                if trial.best not in legal_moves(Position()):
                    raise RuntimeError("引擎启动试算返回非法着法")
                self._check(ticket)
                new_settings = copy.deepcopy(self.settings)
                new_settings[key] = profile
                save_error = ""
                try:
                    temporary = self._stage_settings(new_settings, key)
                except (OSError, ValueError, TypeError) as exc:
                    save_error = f"引擎已启用，但未能保存，下次可能无法恢复：{exc}"
                # Linearize cancellation, persistence and active-engine publication.
                # A cancelled candidate never replaces either disk or memory state.
                with self._state_lock:
                    self._check(ticket)
                    if temporary is not None:
                        try:
                            temporary.replace(self.config_path)
                        except OSError as exc:
                            save_error = f"引擎已启用，但未能保存，下次可能无法恢复：{exc}"
                    self._engine = candidate
                    self.selected = self.preferred = key
                    self.settings = new_settings
                    self.save_error = save_error
                    self.startup_failed = False
                logging.getLogger("xiangqi.engine").info("[Engine] Selected %s", candidate.name)
                if save_error:
                    logging.getLogger("xiangqi.engine").warning(save_error)
                return candidate.name
            except Exception:
                candidate.close()
                raise
            finally:
                with self._state_lock:
                    if self._pending is candidate:
                        self._pending = None
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def close(self):
        with self._state_lock:
            self._closed = True
        self.stop()
        self._engine.close()
        pending = self._pending
        if pending:
            pending.close()
        # Wait for any startup already in flight, then reap once more.
        with self._lock:
            self._engine.close()
