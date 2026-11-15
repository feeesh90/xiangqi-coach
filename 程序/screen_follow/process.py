"""Own a single local recognition worker, bounded messages and deterministic close."""
import base64,io,json,subprocess,sys,threading,uuid
from time import monotonic
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MAX=12*1024*1024

class FollowProcess:
    def __init__(self,callback):
        python=ROOT/".recognition-venv/Scripts/python.exe"
        if not python.is_file():
            # Portable builds ship the recognition dependencies inside the
            # launcher runtime instead of a per-machine virtual environment.
            # A windowed interpreter cannot back a piped worker, so prefer the
            # console interpreter next to pythonw.exe.
            python=Path(sys.executable)
            if python.name.lower()=="pythonw.exe":
                console=python.with_name("python.exe")
                if console.is_file():python=console
        self.callback=callback;self.closed=False;self.context={};self.lock=threading.Lock();self.heartbeat=None
        self.pending_recognition=None
        self.p=subprocess.Popen([str(python),"-X","utf8",str(ROOT/"screen_follow/worker.py")],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            cwd=ROOT,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.thread=threading.Thread(target=self._read,daemon=True);self.thread.start()

    def _read(self):
        failure="识别进程已退出，请重新同步"
        try:
            while not self.closed:
                line=self.p.stdout.readline(MAX+1)
                if not line:break
                if len(line)>MAX:raise ValueError("识别输出超限")
                result=json.loads(line)
                if result.get("id")==getattr(self,"pending_recognition",None) and result.get("kind") in ("recognized","error"):
                    self.pending_recognition=None
                if (not self.closed and result.get("kind") in ("heartbeat","started")
                        and result.get("epoch")==self.context.get("epoch")):
                    # Record arrival on the reader thread, independent of Tk
                    # dispatch. A delayed GUI callback is not a dead worker.
                    self.heartbeat=(result["epoch"],monotonic())
                if not self.closed:self.callback(result)
        except Exception as e:
            failure=str(e)
        finally:
            if not self.closed:
                event=dict(kind="error",fatal=True,message=failure,**self.context)
                ident=getattr(self,"pending_recognition",None)
                if ident is not None:event["id"]=ident
                self.callback(event)

    def send(self,op,**data):
        if op in ("start","accept"):self.context={k:data[k] for k in ("epoch","version")}
        if op=="start":self.heartbeat=None
        raw=(json.dumps(dict(op=op,**data),ensure_ascii=False)+"\n").encode()
        if len(raw)>MAX:raise ValueError("识别请求超限")
        with self.lock:
            if self.closed or self.p.poll() is not None:raise RuntimeError("识别进程已关闭")
            self.p.stdin.write(raw);self.p.stdin.flush()

    def last_heartbeat_at(self,epoch):
        value=self.heartbeat
        return value[1] if value is not None and value[0]==epoch else None

    def recognize(self,image,corners=None,model="compatible"):
        data=io.BytesIO();image.save(data,format="PNG")
        ident=uuid.uuid4().hex
        self.pending_recognition=ident
        payload=base64.b64encode(data.getvalue()).decode()
        def write():
            try:self.send("recognize",id=ident,image=payload,corners=corners,model=model)
            except Exception as e:
                if not self.closed:self.callback(dict(id=ident,kind="error",message=str(e)))
        threading.Thread(target=write,daemon=True).start()
        return ident

    def close(self):
        if self.closed:return
        self.closed=True
        try:
            self.p.kill() if self.p.poll() is None else None
            self.p.wait(timeout=3)
        except (OSError,subprocess.TimeoutExpired):pass
        if threading.current_thread() is not self.thread:self.thread.join(timeout=1)
        # Release any Tk-owning callback on the caller (UI) thread.
        self.callback=lambda event:None
        for stream in (self.p.stdin,self.p.stdout):
            try:stream.close()
            except OSError:pass
