"""Persistent bounded JSON-lines worker. Images remain in memory."""
import base64,io,json,queue,sys,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from rules import Position,Move
from screen_follow.capture import WindowCapture
from screen_follow.tracker import MoveTracker

MAX=12*1024*1024

def main():
    commands=queue.Queue(maxsize=8)
    def read():
        while True:
            line=sys.stdin.buffer.readline(MAX+1)
            if not line:commands.put({"op":"stop"});return
            if len(line)>MAX:commands.put({"op":"stop"});return
            try:
                command=json.loads(line);commands.put(command)
                if command.get("op")=="stop":return
            except ValueError:commands.put({"op":"stop"});return
    threading.Thread(target=read,daemon=True).start()
    def emit(**data):
        sys.stdout.buffer.write((json.dumps(data,ensure_ascii=False)+"\n").encode());sys.stdout.buffer.flush()
    model=None;models={};image=None;capture=None;tracker=None;running=False;version=0;epoch="";waiting=False;capture_ready_at=0
    heartbeat=time.monotonic();last_tracking=None;progress_at=0;next_capture_at=0;allow_submit=True
    while True:
        if running and time.monotonic()-heartbeat>1:
            emit(kind="heartbeat",epoch=epoch,version=version);heartbeat=time.monotonic()
        delay=max(0,min(.05,max(capture_ready_at,next_capture_at)-time.monotonic())) if running and not waiting else .3
        try:cmd=commands.get(timeout=delay)
        except queue.Empty:cmd=None
        if cmd:
            op=cmd.get("op");ident=cmd.get("id")
            try:
                if op=="stop":return
                if op=="recognize":
                    image=Image.open(io.BytesIO(base64.b64decode(cmd["image"],validate=True)))
                    if image.width*image.height>16000000:raise ValueError("图片尺寸超限")
                    key=cmd.get("model","compatible")
                    if key not in ("compatible","tchess"):raise ValueError("未知识别模型")
                    if key not in models:
                        if key=="compatible":
                            from board_recognizer import BoardRecognizer
                            models[key]=BoardRecognizer()
                        else:
                            from screen_follow.tchess import TChessRecognizer
                            models[key]=TChessRecognizer()
                    model=models[key]
                    result=model.recognize_image(image,cmd.get("corners"))
                    emit(id=ident,kind="recognized",result=result)
                elif op=="start":
                    epoch=cmd["epoch"];version=cmd["version"]
                    capture=WindowCapture(cmd["hwnd"],cmd["roi"])
                    if tuple(cmd["geometry"])!=capture.baseline:raise ValueError("窗口已缩放，请重新采集")
                    capture.set_grid(cmd["corners"])
                    tracker=MoveTracker(Position(cmd["fen"]),image,cmd["corners"],cmd["red_bottom"],
                        piece_verifier=getattr(model,"_classify_recheck",None))
                    epoch=cmd["epoch"];version=cmd["version"];running=True;waiting=False
                    last_tracking=None;progress_at=0;allow_submit=True
                    capture_ready_at=time.monotonic()+.4
                    emit(kind="started",epoch=epoch,version=version)
                elif op=="accept":
                    if allow_submit and waiting and cmd["epoch"]==epoch and cmd["version"]==version+1:
                        tracker.accept(Move.from_ucci(cmd["move"]));version+=1;waiting=False
                elif op=="freeze_sync":
                    if cmd.get("epoch")==epoch:allow_submit=False;waiting=False
                elif op=="pause":running=False
            except Exception as e:
                running=False;emit(id=ident,kind="error",message=str(e)[:1500],epoch=epoch,version=version)
        if running and not waiting and time.monotonic()>=capture_ready_at:
            try:
                if time.monotonic()<next_capture_at:continue
                next_capture_at=time.monotonic()+.05
                frame=capture.grab()
                try:result=tracker.observe(frame)
                finally:frame.close()
                if result["kind"]=="move" and allow_submit:
                    waiting=True;emit(**result,epoch=epoch,version=version)
                else:
                    if result["kind"]=="resync_required":
                        if allow_submit:emit(**result,epoch=epoch,version=version)
                        allow_submit=False
                    if not allow_submit:
                        result={**result,"kind":"resync_required","reason":"已确认历史无法衔接，需要重新同步；仍在采集"}
                    elif result["kind"]=="uncertain":
                        # Legacy/injected uncertain results are visual progress,
                        # never a capture failure or an inactivity timeout.
                        result={**result,"kind":"waiting_clear","reason":"等待画面清晰，仍在监听"}
                    # Uncertain animation is not a terminal failure. Progress is
                    # bounded and continues alongside the independent heartbeat.
                    now=time.monotonic()
                    signature=(result["kind"],result.get("candidate_count",0),result.get("changed_cells",0))
                    if signature!=last_tracking or now-progress_at>=.5:
                        state=result["kind"];data={k:v for k,v in result.items() if k!="kind"}
                        emit(kind="tracking",state=state,**data,epoch=epoch,version=version)
                        last_tracking=signature;progress_at=now
            except Exception as e:
                running=False;emit(kind="error",message=str(e)[:1500],epoch=epoch,version=version)

if __name__=="__main__":main()
