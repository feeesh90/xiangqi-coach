import os, queue, re, subprocess, threading, time
from dataclasses import dataclass
from pathlib import Path
from rules import Move

@dataclass
class Candidate:
    multipv:int
    move:Move
    pv:list
    depth:int
    score_cp:int|None=None
    mate:int|None=None

@dataclass
class Analysis:
    best:Move
    candidates:list
    depth:int
    elapsed:float

class Pikafish:
    def __init__(self, root):
        self.root=Path(root)
        self.exe=self._find_engine()
        self.nnue=self._find_nnue()
        self.p=None
        self.lock=threading.RLock()
        self.write_lock=threading.Lock()
        self.name="Pikafish"
        self.ready=False
        self.current_multipv=None
        self.cache={}
        self.cancel_generation=0
        self._stdout_queue=queue.Queue()
        self._reader_thread=None
        self._reader_process=None
        self.threads=max(1,min(12,(os.cpu_count() or 4)-1))
        self.hash_mb=512

    def _find_engine(self):
        names=["pikafish.exe","Pikafish-Windows-x86-64-universal.exe","pikafish"]
        for n in names:
            p=self.root/"engine"/n
            if p.is_file():return p
        return self.root/"engine"/"pikafish.exe"

    def _find_nnue(self):
        p=self.root/"engine"/"pikafish.nnue"
        return p

    def installed(self):
        return self.exe.is_file() and self.nnue.is_file()

    def _send(self,s):
        # Search can be running on another thread. A separate write lock lets
        # STOP be sent immediately without waiting for the long analyze lock.
        with self.write_lock:
            p=self.p
            if not p or p.poll() is not None:
                raise RuntimeError("Pikafish 进程未运行")
            p.stdin.write(s+"\n")
            p.stdin.flush()

    def _ensure_reader(self):
        """Read engine output on a helper thread so timeouts are real."""
        p=self.p
        if not p:return
        thread=getattr(self,"_reader_thread",None)
        if getattr(self,"_reader_process",None) is p and thread and thread.is_alive():return
        if not hasattr(self,"_stdout_queue"):
            self._stdout_queue=queue.Queue()
        self._reader_process=p
        def read_output():
            try:
                for line in p.stdout:
                    self._stdout_queue.put((p,line))
            finally:
                self._stdout_queue.put((p,None))
        self._reader_thread=threading.Thread(target=read_output,daemon=True)
        self._reader_thread.start()

    def _terminate_process(self,p=None):
        p=p or self.p
        if not p:return
        try:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=1)
        except Exception:
            pass
        if self.p is p:
            self.p=None
            self.ready=False
            self.current_multipv=None

    def _read_line(self,timeout,p=None):
        p=p or self.p
        if not p:raise RuntimeError("Pikafish 进程未运行")
        self._ensure_reader()
        end=time.monotonic()+max(0,float(timeout))
        while True:
            remaining=end-time.monotonic()
            if remaining<=0:
                raise TimeoutError("等待 Pikafish 输出超时")
            try:
                source,line=self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                if p.poll() is not None:raise RuntimeError("Pikafish 进程已经退出")
                raise TimeoutError("等待 Pikafish 输出超时")
            if source is not p:
                continue
            if line is None:
                raise RuntimeError("Pikafish 进程已经退出")
            return line

    def _read_until(self,token,timeout):
        p=self.p
        end=time.monotonic()+timeout
        try:
            while True:
                line=self._read_line(end-time.monotonic(),p).strip()
                if "CRITICAL ERROR" in line:raise RuntimeError(line)
                if line==token:return
        except TimeoutError:
            self._terminate_process(p)
            raise TimeoutError("等待 "+token+" 超时，已终止无响应的引擎")

    def start(self):
        with self.lock:
            if self.ready and self.p and self.p.poll() is None:
                self._ensure_reader()
                return
            if not self.installed():
                raise FileNotFoundError("未找到 engine/pikafish.exe 或 engine/pikafish.nnue")
            if self.p and self.p.poll() is None:
                self._terminate_process(self.p)
            flags=0
            if os.name=="nt":
                flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
            self.p=subprocess.Popen(
                [str(self.exe)],cwd=str(self.root/"engine"),
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                text=True,encoding="utf-8",errors="replace",bufsize=1,
                creationflags=flags
            )
            self._ensure_reader()
            try:
                self._send("uci")
                self._read_until("uciok",10)
                self._send(f"setoption name Threads value {self.threads}")
                self._send(f"setoption name Hash value {self.hash_mb}")
                self._send(f"setoption name EvalFile value {self.nnue.resolve()}")
                self._send("isready")
                self._read_until("readyok",20)
            except Exception:
                self._terminate_process(self.p)
                raise
            self.current_multipv=None
            self.ready=True

    @staticmethod
    def _parse_int(line,key,default=0):
        m=re.search(r"(?:^|\s)"+re.escape(key)+r"\s+(\d+)",line)
        return int(m.group(1)) if m else default

    def configure(self,threads=None,hash_mb=None):
        """Runtime engine configuration, similar to mature UCI GUIs."""
        with self.lock:
            if threads is not None:
                self.threads=max(1,min(64,int(threads)))
            if hash_mb is not None:
                self.hash_mb=max(16,min(4096,int(hash_mb)))
            if self.ready and self.p and self.p.poll() is None:
                self._send(f"setoption name Threads value {self.threads}")
                self._send(f"setoption name Hash value {self.hash_mb}")
                self._send("isready")
                self._read_until("readyok",5)
            self.cache.clear()

    def analyze_limit(self,fen,mode="time",value=700,multipv=3,searchmoves=None):
        """UCI analysis using fixed time, depth or node count."""
        mode=str(mode).lower()
        if mode=="time":
            return self.analyze(fen,int(value),multipv,searchmoves)
        return self._analyze_go(fen,mode,value,multipv,searchmoves)

    def _analyze_go(self,fen,mode,value,multipv=3,searchmoves=None):
        multipv=max(1,min(3,int(multipv)))
        if mode not in ("depth","nodes"):
            raise ValueError("mode must be time/depth/nodes")
        if searchmoves:
            if isinstance(searchmoves,str):searchmoves=[searchmoves]
            searchmoves=tuple(m for m in searchmoves if re.fullmatch(r"[a-i][0-9][a-i][0-9]",m or ""))
        else:searchmoves=()
        value=max(1,int(value))
        with self.lock:
            self.start()
            if self.current_multipv!=multipv:
                self._send(f"setoption name MultiPV value {multipv}")
                self.current_multipv=multipv
            self._send(f"position fen {fen}")
            started=time.time()
            generation=self.cancel_generation
            go=f"go {mode} {value}"
            if searchmoves:go+=" searchmoves "+" ".join(searchmoves)
            self._send(go)
            latest={};best=None;max_depth=0
            p=self.p
            deadline=time.monotonic()+60
            while True:
                try:line=self._read_line(deadline-time.monotonic(),p)
                except TimeoutError:
                    self._terminate_process(p)
                    raise TimeoutError("Pikafish 分析超时，已终止无响应的引擎")
                line=line.strip()
                if "CRITICAL ERROR" in line:raise RuntimeError(line)
                if line.startswith("info "):
                    dep=self._parse_int(line,"depth",0);mp=self._parse_int(line,"multipv",1)
                    max_depth=max(max_depth,dep)
                    k=line.find(" pv ")
                    if k>=0:
                        pv=line[k+4:].split()
                        mv=Move.from_ucci(pv[0]) if pv else None
                        cp=None;mate=None
                        sm=re.search(r"(?:^|\s)score\s+cp\s+(-?\d+)",line)
                        mm=re.search(r"(?:^|\s)score\s+mate\s+(-?\d+)",line)
                        if sm:
                            try:cp=int(sm.group(1))
                            except:pass
                        if mm:
                            try:mate=int(mm.group(1))
                            except:pass
                        if mv:latest[mp]=Candidate(mp,mv,[x.lower() for x in pv],dep,cp,mate)
                elif line.startswith("bestmove "):
                    a=line.split()
                    if len(a)>=2:best=Move.from_ucci(a[1].lower())
                    break
            if not best:raise RuntimeError("Pikafish 没有返回 bestmove")
            cs=[latest[k] for k in sorted(latest)]
            if not cs:cs=[Candidate(1,best,[best.ucci()],max_depth,None,None)]
            if cs and cs[0].move!=best:
                match=next((c for c in cs if c.move==best),None)
                if match:cs=[match]+[c for c in cs if c is not match]
                else:cs.insert(0,Candidate(1,best,[best.ucci()],max_depth,None,None))
            for i,c in enumerate(cs[:3],1):c.multipv=i
            out=Analysis(best,cs[:3],max_depth,time.time()-started)
            return out

    def analyze(self,fen,movetime=10000,multipv=3,searchmoves=None):
        """Fast synchronous UCI search.

        V7 optimizations:
        - cache repeated analysis of the exact same position;
        - do not send isready before every search;
        - change MultiPV only when needed;
        - keep one engine process warm for the whole game.
        """
        multipv=max(1,min(3,int(multipv)))
        if searchmoves:
            if isinstance(searchmoves,str): searchmoves=[searchmoves]
            searchmoves=tuple(m for m in searchmoves if re.fullmatch(r"[a-i][0-9][a-i][0-9]",m or ""))
        else:
            searchmoves=()
        movetime=max(50,int(movetime))
        key=(fen,multipv,searchmoves)

        cached=self.cache.get(key)
        if cached:
            cached_ms,analysis,stamp=cached
            # Reuse if the previous search was at least as deep in time and is recent.
            if cached_ms>=movetime and time.time()-stamp<900:
                return analysis

        with self.lock:
            self.start()
            # Re-check cache after waiting for the engine lock.
            cached=self.cache.get(key)
            if cached:
                cached_ms,analysis,stamp=cached
                if cached_ms>=movetime and time.time()-stamp<900:
                    return analysis

            if self.current_multipv!=multipv:
                self._send(f"setoption name MultiPV value {multipv}")
                self.current_multipv=multipv

            self._send(f"position fen {fen}")
            started=time.time()
            generation=self.cancel_generation
            go=f"go movetime {movetime}"
            if searchmoves:
                go += " searchmoves " + " ".join(searchmoves)
            self._send(go)

            latest={}
            best=None; max_depth=0
            p=self.p
            deadline=time.monotonic()+max(8,movetime/1000+6)
            while True:
                try:line=self._read_line(deadline-time.monotonic(),p)
                except TimeoutError:
                    self._terminate_process(p)
                    raise TimeoutError("Pikafish 分析超时，已终止无响应的引擎")
                line=line.strip()
                if "CRITICAL ERROR" in line:raise RuntimeError(line)
                if line.startswith("info "):
                    dep=self._parse_int(line,"depth",0)
                    mp=self._parse_int(line,"multipv",1)
                    max_depth=max(max_depth,dep)
                    k=line.find(" pv ")
                    if k>=0:
                        pv=line[k+4:].split()
                        mv=Move.from_ucci(pv[0]) if pv else None
                        cp=None; mate=None
                        sm=re.search(r"(?:^|\s)score\s+cp\s+(-?\d+)",line)
                        mm=re.search(r"(?:^|\s)score\s+mate\s+(-?\d+)",line)
                        if sm:
                            try: cp=int(sm.group(1))
                            except: cp=None
                        if mm:
                            try: mate=int(mm.group(1))
                            except: mate=None
                        if mv:latest[mp]=Candidate(mp,mv,[x.lower() for x in pv],dep,cp,mate)
                elif line.startswith("bestmove "):
                    a=line.split()
                    if len(a)>=2:best=Move.from_ucci(a[1].lower())
                    break
            if not best:raise RuntimeError("Pikafish 没有返回 bestmove")
            cs=[latest[k] for k in sorted(latest)]
            if not cs:cs=[Candidate(1,best,[best.ucci()],max_depth,None,None)]
            # Ensure candidate #1 follows the actual UCI bestmove.
            if cs and cs[0].move!=best:
                match=next((c for c in cs if c.move==best),None)
                if match:
                    cs=[match]+[c for c in cs if c is not match]
                else:
                    cs.insert(0,Candidate(1,best,[best.ucci()],max_depth,None,None))
            for i,c in enumerate(cs[:3],1):
                c.multipv=i
            out=Analysis(best,cs[:3],max_depth,time.time()-started)
            # Do not cache a search that was manually stopped.
            if generation==self.cancel_generation:
                self.cache[key]=(movetime,out,time.time())
            return out

    def new_game(self):
        """Tell the engine a truly new game has started. Do not do this before every analysis."""
        with self.lock:
            self.start()
            self.cache.clear()
            self._send("ucinewgame")
            self._send("isready")
            self._read_until("readyok",5)

    def analyze_coach(self,fen,movetime=1500,with_alts=True):
        """V7 uses one search only. MultiPV=3 is fast enough on desktop and
        avoids the old primary-search + second-search wait."""
        return self.analyze(fen,movetime,3 if with_alts else 1)

    def stop(self):
        """
        Interrupt the current UCI search immediately.

        Important: do NOT acquire self.lock here. analyze() owns that lock for
        the entire search, which made V5's stop effectively useless.
        """
        try:
            self.cancel_generation+=1
            p=self.p
            if p and p.poll() is None:
                self._send("stop")
        except Exception:
            pass

    def close(self):
        # Do not wait for the analysis lock: a stalled search may own it.
        self.cancel_generation+=1
        p=self.p
        if p and p.poll() is None:
            try:
                self._send("quit")
                p.wait(timeout=1)
            except Exception:
                self._terminate_process(p)
        if self.p is p:self.p=None
        self.ready=False
        self.current_multipv=None
