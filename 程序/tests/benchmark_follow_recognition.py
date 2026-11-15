"""Run with the isolated vision interpreter; source groups are explicit."""
import sys,json,hashlib,time,importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from rules import Position
from position_import import normalize_board
from board_recognizer import BoardRecognizer,MODEL_DIR
from screen_follow.tchess import TChessRecognizer,LayoutRecognizer
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/"data/screen_follow_verification/20261028";out.mkdir(parents=True,exist_ok=True)
board=[[""]*9 for _ in range(10)]
for r,c,p in [(0,2,"b"),(0,3,"a"),(0,4,"k"),(0,5,"a"),(0,7,"R"),(3,0,"C"),(3,4,"p"),(3,8,"p"),(4,3,"n"),(5,6,"B"),(6,0,"c"),(6,4,"P"),(6,8,"P"),(8,6,"R"),(9,2,"r"),(9,3,"A"),(9,4,"K"),(9,5,"A"),(9,7,"N")]:board[r][c]=p
source=out/"jj_original.jpg"
(out/"jj_manual_label.json").write_text(json.dumps(dict(source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),board=board,group="single_real_JJ_source",note="Manually transcribed; transformations are not independent samples"),ensure_ascii=False,indent=2),encoding="utf-8")
backups=sorted((ROOT/"data/backups").glob("*_screen_follow/board_recognizer.py"))
spec=importlib.util.spec_from_file_location("old_recognizer",backups[-1]);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
models={"original":old.BoardRecognizer(MODEL_DIR),"updated":BoardRecognizer(),"tchess_yolo11":TChessRecognizer(out/"yolov11.onnx"),"layout_nano":LayoutRecognizer(out/"nano_v3-0319.onnx")}
samples=[("JJ_original",source,board,True)]
labels=json.loads((ROOT/"data/endgame_verification/samples/labels.json").read_text(encoding="utf-8"))
for i,s in enumerate(labels[2:10],2):samples.append(("generated_"+str(i),ROOT/"data/endgame_verification/samples"/s["file"],Position(s["fen"]).b,s["red_bottom"]))
external=ROOT/"data/endgame_verification/external"
e=json.loads((external/"verification.json").read_text(encoding="utf-8"))
samples.append(("external_lecture",external/"page_11_image_00_Im6.png",Position(e["fen"]).b,True))
report={"layout_model_source":"https://huggingface.co/spaces/yolo12138/Chinese_Chess_Recognition/tree/main/onnx", "layout_sha256":hashlib.sha256((out/"nano_v3-0319.onnx").read_bytes()).hexdigest(),"model_source":"https://github.com/sojourners/public-Xiangqi", "weight_sha256":hashlib.sha256((out/"yolov11.onnx").read_bytes()).hexdigest(),"cases":[]}
for name,path,expected,red_bottom in samples:
    for model_name,model in models.items():
        start=time.monotonic()
        try:
            result=model.recognize(str(path)) if model_name=="original" else model.recognize_image(Image.open(path))
            actual=normalize_board(result["board"],red_bottom)
            uncertain={tuple(p) for p in result["uncertain"]}
            if not red_bottom:uncertain={(9-r,8-c) for r,c in uncertain}
            errors=[(r,c) for r in range(10) for c in range(9) if actual[r][c]!=expected[r][c]]
            item=dict(sample=name,model=model_name,errors=errors,unflagged_errors=[v for v in errors if v not in uncertain],uncertain=len(uncertain),seconds=round(time.monotonic()-start,3))
        except Exception as exc:item=dict(sample=name,model=model_name,error=str(exc))
        report["cases"].append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
(out/"recognition_comparison.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
