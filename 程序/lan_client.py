"""IPv4 LAN client. Reconnection retains a memory-only player identity."""
import re
import socket
import threading

import lan_network
from lan_protocol import VERSION, PORT, Channel, ProtocolError, StateVerifier, close_socket, proof, receive_frame, send_frame


class LanClient:
    def __init__(self, interface, host, code, port=PORT, on_event=None):
        self.interface, self.host, self.code, self.port = interface, host, code.strip(), port
        if not interface.contains(host):
            raise ValueError("房主必须位于所选网卡的同一个私有 IPv4 子网，请填写数字 IP")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16}", self.code):
            raise ValueError("房间码应为房主显示的 16 位字符，区分大小写")
        if not 1024 <= port <= 65535:
            raise ValueError("端口必须在 1024–65535 范围")
        self.on_event = on_event or (lambda event: None)
        self.resume_token = ""
        self.channel = None
        self.sock = None
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.connection_stop = threading.Event()
        self.threads = []
        self.verifier = StateVerifier()

    def connect(self):
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError("房间已退出")
            if self.channel is not None:
                raise RuntimeError("已经连接房间")
            if lan_network.current_interface(self.interface.ip) != self.interface:
                raise ValueError("本地网卡配置已变化，请重新加入房间")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock = sock
            try:
                sock.bind((self.interface.ip, 0))
                sock.settimeout(4)
                sock.connect((self.host, self.port))
                sock.settimeout(.5)
                message = receive_frame(sock, 5)
                nonce = message.get("nonce", "")
                if (message.get("type") != "challenge" or message.get("version") != VERSION
                        or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{48}", nonce)):
                    raise ProtocolError("房主协议版本不兼容，请双方更新软件后重新创建房间")
                send_frame(sock, {"version": VERSION, "resume": self.resume_token,
                                  "proof": proof(self.code, nonce, self.resume_token)})
                channel = Channel(sock, self.code, nonce)
                welcome = channel.receive()
                if welcome.get("type") != "welcome":
                    raise ProtocolError(welcome.get("message", "房间码错误或房间已满"))
                token = welcome.get("resume")
                if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{48}", token):
                    raise ProtocolError("重连身份无效")
                self.resume_token = token
                self.channel = channel
                self.connection_stop = threading.Event()
                self.threads = [t for t in self.threads if t.is_alive()]
                for target in (self._read, self._heartbeat):
                    t = threading.Thread(target=target, args=(channel, self.connection_stop), daemon=True)
                    self.threads.append(t)
                    t.start()
            except Exception:
                close_socket(sock)
                self.sock = None
                raise

    def _read(self, channel, stop):
        message = "连接已中断，棋局暂停。可在局域网窗口重连。"
        try:
            while not self.closed.is_set() and not stop.is_set():
                event = channel.receive()
                if event.get("type") == "pong":
                    continue
                if event.get("type") not in ("state", "error"):
                    raise ProtocolError("房主发送了未知消息")
                if event.get("type") == "state":
                    self.verifier.check(event)
                self.on_event(event)
        except (OSError, ValueError, TimeoutError):
            pass
        finally:
            stop.set()
            close_socket(channel.sock)
            with self.lock:
                if self.channel is channel:
                    self.channel = None
            if not self.closed.is_set():
                self.on_event({"type": "disconnected", "message": message})

    def _heartbeat(self, channel, stop):
        counter = 0
        while not stop.wait(3) and not self.closed.is_set():
            try:
                channel.send({"type": "ping"})
                counter += 1
                if counter % 2 == 0 and lan_network.current_interface(self.interface.ip) != self.interface:
                    raise ValueError("本地网络配置已变化")
            except Exception:
                close_socket(channel.sock)
                return

    def send(self, command):
        channel = self.channel
        if channel is None or self.closed.is_set():
            raise ConnectionError("连接已断开，请先重连")
        channel.send(command)

    def close(self):
        self.closed.set()
        self.connection_stop.set()
        close_socket(self.sock)
        for t in list(self.threads):
            if t is not threading.current_thread():
                t.join(timeout=1)
