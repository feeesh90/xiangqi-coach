"""Bounded, cancellable local child process, independent of engine tasks."""
import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class RecognitionCancelled(RuntimeError):
    pass


class RecognitionJob:
    def __init__(self, timeout=30, command=None):
        python = ROOT/".recognition-venv"/("Scripts/python.exe" if sys.platform=="win32" else "bin/python")
        self.command = command or [str(python) if python.is_file() else sys.executable,
                                   "-X","utf8",str(ROOT/"recognition_worker.py")]
        self.timeout = timeout
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.finished = threading.Event()

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            p = self.process
            if p is not None and p.poll() is None:
                try:p.kill()
                except OSError:pass

    def run(self, image, corners=None):
        buffers = [bytearray(),bytearray()]
        overflow = threading.Event()
        p = None
        threads = []
        try:
            request = json.dumps({"image":str(image),"corners":corners},ensure_ascii=False).encode("utf-8")
            if len(request)>16_384:
                raise ValueError("图片路径或四角数据过长。")
            with self.lock:
                if self.cancelled.is_set():
                    raise RecognitionCancelled("识别已取消。")
                p = self.process = subprocess.Popen(self.command,stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT,
                            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            def drain(stream,buffer,maximum):
                try:
                    while True:
                        chunk = stream.read(4096)
                        if not chunk:break
                        room = maximum-len(buffer)
                        buffer.extend(chunk[:room])
                        if len(chunk)>room:
                            overflow.set()
                            try:p.kill()
                            except OSError:pass
                finally:
                    stream.close()
            for stream,buffer,maximum in ((p.stdout,buffers[0],65_536),(p.stderr,buffers[1],8192)):
                thread = threading.Thread(target=drain,args=(stream,buffer,maximum),daemon=True)
                threads.append(thread);thread.start()
            def send_request():
                # A hung child may never read stdin. Writing a long path must
                # not prevent the controlling thread from enforcing timeout.
                try:p.stdin.write(request)
                except OSError:pass
                finally:
                    try:p.stdin.close()
                    except OSError:pass
            sender=threading.Thread(target=send_request,daemon=True)
            threads.append(sender);sender.start()
            try:p.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                p.kill();p.wait(timeout=3)
                raise RuntimeError("图片识别超时，请检查图片范围后重试。")
            for t in threads:t.join(timeout=3)
            if self.cancelled.is_set():raise RecognitionCancelled("识别已取消。")
            if overflow.is_set():raise RuntimeError("识别程序输出过长，已停止。")
            try:payload = json.loads(buffers[0])
            except (ValueError,UnicodeError):
                raise RuntimeError("识别程序异常退出或返回了无效数据，请检查识别资源。")
            if not isinstance(payload,dict):raise RuntimeError("识别结果格式错误。")
            if p.returncode!=0 or payload.get("ok") is not True:
                raise RuntimeError(str(payload.get("error","识别程序异常退出。"))[:2000])
            from position_import import BoardDraft
            BoardDraft.from_result(payload.get("result"))  # validate untrusted result
            return payload["result"]
        finally:
            if p is not None:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=3)
                for t in threads:t.join(timeout=3)
                if p.stdin and not p.stdin.closed:
                    try:p.stdin.close()
                    except OSError:pass
            with self.lock:self.process = None
            self.finished.set()
