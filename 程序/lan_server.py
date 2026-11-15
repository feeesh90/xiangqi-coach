"""Two-player authoritative room. All game changes hold one room lock."""
from collections import deque
import hmac
import re
import secrets
import socket
import threading
import time

import lan_network
from lan_protocol import (VERSION, PORT, MAX_PLIES, Channel, ProtocolError,
                          close_socket, proof, receive_frame, send_frame)
from rules import Position, Move, legal_moves
from repetition import advance, blocked_cycle_move, MESSAGE


class Room:
    def __init__(self, host_red=True, allow_guidance=False):
        self.host_red = bool(host_red)
        self.allow_guidance = bool(allow_guidance)
        self.connected = False
        self.ever_joined = False
        self.revision = 0
        self.result = ""
        self.pending = None
        self.game = secrets.token_hex(12)
        self.position = Position()
        self.moves = []
        self.positions = [self.position.copy()]

    def snapshot(self):
        return {"type": "state", "game": self.game, "revision": self.revision,
                "fen": self.position.fen(), "moves": list(self.moves),
                "host_red": self.host_red, "allow_guidance": self.allow_guidance,
                "connected": self.connected, "ever_joined": self.ever_joined,
                "result": self.result, "pending": dict(self.pending) if self.pending else None}

    def connection(self, value):
        self.connected = value
        self.ever_joined = self.ever_joined or value
        self.pending = None
        self.revision += 1

    def apply(self, role, command):
        if role not in ("host", "guest"):
            raise ProtocolError("未知玩家")
        if (command.get("game") != self.game or type(command.get("revision")) is not int
                or command["revision"] != self.revision):
            raise ProtocolError("棋局已经更新，请按最新棋盘操作")
        if not self.connected:
            raise ProtocolError("等待对方连接，对局已暂停")
        kind = command.get("type")
        red = self.host_red if role == "host" else not self.host_red
        if kind == "move":
            if self.result or self.pending:
                raise ProtocolError("棋局已结束或正在等待协商")
            if red != self.position.red:
                raise ProtocolError("还没有轮到你行棋")
            value = command.get("move")
            if not isinstance(value, str) or len(value) != 4:
                raise ProtocolError("着法格式无效")
            move = Move.from_ucci(value)
            if move not in legal_moves(self.position):
                raise ProtocolError("不合法的着法")
            context = ()
            for index in range(max(0, len(self.moves)-7), len(self.moves)):
                context = advance(context, self.positions[index], Move.from_ucci(self.moves[index]))
            if move == blocked_cycle_move(self.position, context):
                raise ProtocolError(MESSAGE)
            self.position.apply_unchecked(move)
            self.moves.append(move.ucci())
            self.positions.append(self.position.copy())
            if not legal_moves(self.position):
                self.result = ("红方" if red else "黑方") + "获胜：对方无合法着法"
            elif len(self.moves) >= MAX_PLIES:
                self.result = "达到单局长度上限，对局结束"
        elif kind == "resign":
            if self.result:
                raise ProtocolError("棋局已经结束")
            self.result = ("红方" if red else "黑方") + "认输，" + ("黑方" if red else "红方") + "获胜"
            self.pending = None
        elif kind == "request":
            action = command.get("action")
            if action not in ("undo", "restart", "draw") or self.pending:
                raise ProtocolError("无效申请或已有申请等待回复")
            if self.result and action != "restart":
                raise ProtocolError("本局已结束，只能申请新局")
            if action == "undo" and not self.moves:
                raise ProtocolError("没有可以撤销的着法")
            self.pending = {"id": secrets.token_hex(12), "action": action, "by": role}
        elif kind == "reply":
            request = self.pending
            if (not request or request["id"] != command.get("id") or request["by"] == role
                    or type(command.get("accept")) is not bool):
                raise ProtocolError("该申请已失效或不能由自己答复")
            if command["accept"]:
                if request["action"] == "undo":
                    self.moves.pop()
                    self.positions.pop()
                    self.position = self.positions[-1].copy()
                elif request["action"] == "restart":
                    self.game = secrets.token_hex(12)
                    self.position = Position()
                    self.moves = []
                    self.positions = [self.position.copy()]
                    self.result = ""
                else:
                    self.result = "双方同意和棋"
            self.pending = None
        else:
            raise ProtocolError("不支持的房间操作")
        self.revision += 1


class LanServer:
    def __init__(self, interface, host_red=True, allow_guidance=False, port=PORT, on_event=None):
        self.interface = interface
        self.port = port
        self.on_event = on_event or (lambda event: None)
        self.room = Room(host_red, allow_guidance)
        self.code = secrets.token_urlsafe(12)
        self.resume_token = ""
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.listener = None
        self.peer = None
        self.sockets = set()
        self.threads = []
        self.slots = threading.BoundedSemaphore(4)
        self.attempts = deque(maxlen=100)

    def _thread(self, fn, *args):
        t = threading.Thread(target=fn, args=args, daemon=True)
        # Prune completed handshake threads so repeated attempts cannot grow it forever.
        with self.lock:
            self.threads = [x for x in self.threads if x.is_alive()]
            self.threads.append(t)
            t.start()

    def start(self):
        if self.listener is not None or self.closed.is_set():
            raise RuntimeError("房间不能重复启动")
        if lan_network.current_interface(self.interface.ip) != self.interface:
            raise ValueError("网卡配置已变化，请重新选择")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((self.interface.ip, self.port))
            sock.listen(4)
            sock.settimeout(.5)
            self.port = sock.getsockname()[1]
            self.listener = sock
            self._thread(self._accept)
            self._thread(self._monitor)
        except Exception:
            sock.close()
            raise
        self.on_event(self.room.snapshot())

    def _accept(self):
        while not self.closed.is_set():
            try:
                sock, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            now = time.monotonic()
            recent = [ip for at, ip in self.attempts if now - at < 60]
            if (not self.interface.contains(address[0]) or len(recent) >= 30
                    or recent.count(address[0]) >= 8 or not self.slots.acquire(False)):
                close_socket(sock)
                continue
            self.attempts.append((now, address[0]))
            sock.settimeout(.5)
            with self.lock:
                self.sockets.add(sock)
            self._thread(self._serve, sock)

    def _serve(self, sock):
        channel = None
        try:
            nonce = secrets.token_hex(24)
            send_frame(sock, {"type": "challenge", "version": VERSION, "nonce": nonce})
            message = receive_frame(sock, 5)
            token = message.get("resume", "")
            received = message.get("proof", "")
            if (message.get("version") != VERSION or not isinstance(token, str)
                    or len(token) > 100 or not token.isascii() or not isinstance(received, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", received)
                    or not hmac.compare_digest(proof(self.code, nonce, token), received)):
                raise ProtocolError("房间码或协议版本不正确")
            channel = Channel(sock, self.code, nonce, server=True)
            with self.lock:
                if self.closed.is_set():
                    return
                if self.peer is not None:
                    raise ProtocolError("房间已经有两位玩家")
                if self.resume_token and not hmac.compare_digest(token, self.resume_token):
                    raise ProtocolError("本房间保留给原玩家重连，请让房主重新创建房间")
                if not self.resume_token:
                    self.resume_token = secrets.token_hex(24)
                channel.send({"type": "welcome", "resume": self.resume_token})
                self.peer = channel
                self.room.connection(True)
                self._publish()
            requests = deque(maxlen=120)
            while not self.closed.is_set():
                message = channel.receive()
                now = time.monotonic()
                if len(requests) == 120 and now - requests[0] < 10:
                    raise ProtocolError("操作过于频繁，连接已暂停")
                requests.append(now)
                if message.get("type") == "ping":
                    channel.send({"type": "pong"})
                elif message.get("type") == "leave":
                    return
                else:
                    self.submit(message, "guest", channel)
        except (OSError, ValueError, TimeoutError) as exc:
            if channel:
                try:
                    channel.send({"type": "error", "message": str(exc)[:200]})
                except (OSError, ValueError):
                    pass
        finally:
            with self.lock:
                if channel is not None and self.peer is channel:
                    self.peer = None
                    self.room.connection(False)
                    if not self.closed.is_set():
                        self._publish()
                self.sockets.discard(sock)
            close_socket(sock)
            self.slots.release()

    def _publish(self):
        state = self.room.snapshot()
        self.on_event(state)
        if self.peer:
            try:
                self.peer.send(state)
            except (OSError, ValueError):
                close_socket(self.peer.sock)

    def submit(self, command, role="host", channel=None):
        with self.lock:
            if self.closed.is_set() or (role == "guest" and self.peer is not channel):
                return
            try:
                self.room.apply(role, command)
            except ProtocolError as exc:
                error = {"type": "error", "message": str(exc)}
                if role == "host":
                    self.on_event(error)
                    self.on_event(self.room.snapshot())
                elif channel:
                    channel.send(error)
                    channel.send(self.room.snapshot())
                return
            self._publish()

    def _monitor(self):
        while not self.closed.wait(5):
            try:
                if lan_network.current_interface(self.interface.ip) != self.interface:
                    raise ValueError("网络配置已变化")
            except Exception:
                self.on_event({"type": "closed", "message": "网卡、专用网络或防火墙状态已变化，房间已关闭"})
                self.close()
                return

    def close(self):
        self.closed.set()
        close_socket(self.listener)
        with self.lock:
            peers = list(self.sockets)
            self.peer = None
        for sock in peers:
            close_socket(sock)
        current = threading.current_thread()
        for t in list(self.threads):
            if t is not current:
                t.join(timeout=1)
