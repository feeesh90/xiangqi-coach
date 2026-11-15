"""Bounded JSON framing and per-connection authenticated LAN messages.

Messages are authenticated, not encrypted. Room secrets never travel on the wire.
No pickle, remote code, arbitrary FEN mutation or file operations are accepted.
"""
import hashlib
import hmac
import json
import re
import socket
import struct
import threading
import time

VERSION = 2
PORT = 39741
MAX_FRAME = 65536
MAX_PLIES = 2048


class ProtocolError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def send_frame(sock, value):
    raw = canonical(value)
    if len(raw) > MAX_FRAME:
        raise ProtocolError("联机消息过大")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def _exact(sock, size, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        if time.monotonic() >= deadline:
            raise TimeoutError("对方长时间没有响应")
        try:
            part = sock.recv(size - len(chunks))
        except socket.timeout:
            continue
        if not part:
            raise ConnectionError("对方已断开连接")
        chunks.extend(part)
    return bytes(chunks)


def receive_frame(sock, timeout=12):
    deadline = time.monotonic() + timeout
    size = struct.unpack("!I", _exact(sock, 4, deadline))[0]
    if not 1 <= size <= MAX_FRAME:
        raise ProtocolError("无效的联机消息长度")
    try:
        value = json.loads(_exact(sock, size, deadline).decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("联机消息必须是有效 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("联机消息必须是对象")
    return value


def proof(code, nonce, token):
    return hmac.new(code.encode("ascii"),
                    ("join:" + nonce + ":" + token).encode("ascii"),
                    hashlib.sha256).hexdigest()


class Channel:
    """Separate sequence counters and keys prevent replay and reflection."""
    def __init__(self, sock, code, nonce, server=False):
        self.sock = sock
        key = hmac.new(code.encode("ascii"), nonce.encode("ascii"), hashlib.sha256).digest()
        self.txkey = hmac.digest(key, b"server" if server else b"client", "sha256")
        self.rxkey = hmac.digest(key, b"client" if server else b"server", "sha256")
        self.tx = self.rx = 0
        self.lock = threading.Lock()

    def send(self, payload):
        with self.lock:
            body = {"seq": self.tx, "payload": payload}
            send_frame(self.sock, {**body, "mac": hmac.digest(self.txkey, canonical(body), "sha256").hex()})
            self.tx += 1

    def receive(self):
        envelope = receive_frame(self.sock)
        seq, payload, mac = (envelope.get(k) for k in ("seq", "payload", "mac"))
        if (type(seq) is not int or seq != self.rx or not isinstance(payload, dict)
                or not isinstance(mac, str) or not re.fullmatch(r"[0-9a-f]{64}", mac)):
            raise ProtocolError("消息序号或格式无效")
        body = {"seq": seq, "payload": payload}
        expected = hmac.digest(self.rxkey, canonical(body), "sha256").hex()
        if not hmac.compare_digest(expected, mac):
            raise ProtocolError("消息身份校验失败")
        self.rx += 1
        return payload


def close_socket(sock):
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


class StateVerifier:
    """Validate host snapshots on the network worker, incrementally replaying moves."""
    def __init__(self):
        from rules import Position
        self.moves = []
        self.positions = [Position()]
        self.revision = -1
        self.identity = None

    def check(self, state):
        import re
        from rules import Position, Move, legal_moves
        from repetition import advance, blocked_cycle_move
        moves = state.get("moves")
        if (not isinstance(state.get("game"), str) or not re.fullmatch(r"[0-9a-f]{24}", state["game"])
                or type(state.get("revision")) is not int or state["revision"] < self.revision
                or any(type(state.get(k)) is not bool for k in ("host_red", "allow_guidance", "connected", "ever_joined"))
                or not isinstance(moves, list) or len(moves) > MAX_PLIES
                or any(not isinstance(m, str) or not re.fullmatch(r"[a-i][0-9][a-i][0-9]", m) for m in moves)
                or not isinstance(state.get("result"), str) or len(state["result"]) > 100):
            raise ProtocolError("房主棋局快照格式无效")
        identity = (state["host_red"], state["allow_guidance"])
        if self.identity is not None and self.identity != identity:
            raise ProtocolError("房主不能中途改变执方或指导规则")
        pending = state.get("pending")
        if pending is not None and (not isinstance(pending, dict)
                or pending.get("action") not in ("undo", "draw", "restart")
                or pending.get("by") not in ("host", "guest")
                or not isinstance(pending.get("id"), str)
                or not re.fullmatch(r"[0-9a-f]{24}", pending["id"])):
            raise ProtocolError("协商请求无效")
        common = 0
        for old, new in zip(self.moves, moves):
            if old != new:
                break
            common += 1
        positions = self.positions[:common + 1]
        position = positions[-1].copy()
        context = ()
        for index in range(max(0, common-7), common):
            context = advance(context, positions[index], Move.from_ucci(moves[index]))
        for value in moves[common:]:
            move = Move.from_ucci(value)
            if move not in legal_moves(position):
                raise ProtocolError("房主返回了不合法的走棋历史")
            if move == blocked_cycle_move(position, context):
                raise ProtocolError("房主返回了不允许的双方往返历史，请双方更新软件")
            context = advance(context, position, move)
            position.apply_unchecked(move)
            positions.append(position.copy())
        if position.fen() != state.get("fen"):
            raise ProtocolError("棋盘与走棋历史不一致")
        self.identity, self.revision = identity, state["revision"]
        self.moves, self.positions = list(moves), positions
        return state
