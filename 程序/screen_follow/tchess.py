"""Evaluation-only TChess YOLO11 adapter; upstream labels and letterboxing."""
import hashlib,time
import cv2,numpy as np,onnxruntime as ort
from PIL import Image
from board_recognizer import find_grid,validate_corners,MODEL_DIR,MODEL_HASHES,CLASS_PIECES

class TChessRecognizer:
    def __init__(self,path=None):
        if path is None:
            from pathlib import Path
            path=Path(__file__).resolve().parents[1]/"models/board_recognition/tchess_yolov11.onnx"
            if not path.is_file():raise ValueError("JJ 截图候选模型缺失，请使用兼容模型或恢复 models/board_recognition/tchess_yolov11.onnx")
            if hashlib.sha256(path.read_bytes()).hexdigest()!="099c4ef0cbfbd07f680037bb1aabf59024f5c0243964b36aeec7c7a57f7213e1":raise ValueError("TChess 模型校验失败")
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(path),sess_options=options,providers=["CPUExecutionProvider"])
        self.labels="nbakrcpRNAKBCP0"
    def _detect(self,rgb,threshold=.5):
        h,w=rgb.shape[:2];rate=min(640/w,640/h)
        nw,nh=round(w*rate),round(h*rate);ox,oy=(640-nw)//2,(640-nh)//2
        padded=np.full((640,640,3),114,np.uint8)
        padded[oy:oy+nh,ox:ox+nw]=cv2.resize(rgb,(nw,nh))
        tensor=np.ascontiguousarray(padded.transpose(2,0,1)[None],dtype=np.float32)/255
        records=self.session.run(None,{self.session.get_inputs()[0].name:tensor})[0][0].T
        result=[]
        for label in range(14):
            boxes=[];scores=[]
            for record in records:
                cls=int(record[4:].argmax());score=float(record[4+cls])
                if cls!=label or score<threshold:continue
                x,y,bw,bh=record[:4]
                boxes.append([float(x-bw/2),float(y-bh/2),float(bw),float(bh)]);scores.append(score)
            for idx in np.asarray(cv2.dnn.NMSBoxes(boxes,scores,threshold,.45)).reshape(-1):
                x,y,bw,bh=boxes[int(idx)]
                result.append(((x+bw/2-ox)/rate,(y+bh/2-oy)/rate,scores[int(idx)],self.labels[label]))
        return result

    def _classify_recheck(self,rgb,x,y):
        if not hasattr(self,"_piece_classifier"):
            path=MODEL_DIR/"online_xiangqi_classifier.onnx"
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_HASHES[path.name]:return None
            options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
            self._piece_classifier=ort.InferenceSession(str(path),sess_options=options,providers=["CPUExecutionProvider"])
        roi=rgb[max(0,y-32):y+33,max(0,x-32):x+33]
        tensor=np.ascontiguousarray(cv2.resize(roi,(64,64)).transpose(2,0,1)[None],dtype=np.float32)/255
        scores=self._piece_classifier.run(None,{self._piece_classifier.get_inputs()[0].name:tensor})[0].reshape(-1)
        ranked=np.argsort(scores)
        if float(scores[ranked[-1]])<.95 or float(scores[ranked[-1]]-scores[ranked[-2]])<.3:return None
        ink=rgb[max(0,y-17):y+18,max(0,x-17):x+18].astype(np.int16)
        rr,gg,bb=ink[:,:,0],ink[:,:,1],ink[:,:,2]
        red=int(((rr>gg+35)&(rr>bb+35)&(rr>gg*1.45)).sum())
        black=int((((rr+gg+bb)/3<100)&(abs(rr-gg)<25)&(abs(gg-bb)<25)).sum())
        if max(red,black)<10 or max(red,black)<1.5*min(red,black):return None
        kind=CLASS_PIECES[int(ranked[-1])]
        return kind if red>black else kind.lower(),float(scores[ranked[-1]])

    def recognize_image(self,image,corners=None):
        started=time.monotonic();rgb=np.array(image.convert("RGB"));h,w=rgb.shape[:2]
        scale=min(1,1600/max(w,h))
        grid=np.array(validate_corners(corners,w,h) if corners is not None else
            np.array(find_grid(cv2.resize(rgb,(round(w*scale),round(h*scale)))))/scale,np.float32)
        matrix=cv2.getPerspectiveTransform(grid,np.array([[40,40],[552,40],[552,616],[40,616]],np.float32))
        warp=cv2.warpPerspective(rgb,matrix,(592,656))
        board=[[""]*9 for _ in range(10)];confidence=[[None]*9 for _ in range(10)];uncertain=set()
        weak={}
        for cx,cy,score,piece in self._detect(warp,.18):
            c,r=round((cx-40)/64),round((cy-40)/64)
            if not (0<=r<10 and 0<=c<9):continue
            error=((cx-40-c*64)**2+(cy-40-r*64)**2)**.5
            if score<.5:
                if error<16:weak[(r,c)]=max(score,weak.get((r,c),0))
                continue
            if board[r][c]:uncertain.add((r,c));continue
            board[r][c]=piece;confidence[r][c]=score
            if score<.8 or error>20:uncertain.add((r,c))
        # Independent occupancy evidence: a missing detection is not an empty
        # square measurement. Circular piece bodies nominate local rechecks.
        circles=cv2.HoughCircles(cv2.GaussianBlur(cv2.cvtColor(warp,cv2.COLOR_RGB2GRAY),(5,5),0),
            cv2.HOUGH_GRADIENT,1,40,param1=100,param2=25,minRadius=14,maxRadius=32)
        suspect=set(weak);round_objects=set()
        if circles is not None:
            for cx,cy,radius in circles[0]:
                c,r=round((cx-40)/64),round((cy-40)/64)
                if 0<=r<10 and 0<=c<9 and ((cx-40-c*64)**2+(cy-40-r*64)**2)**.5<15:
                    suspect.add((r,c));round_objects.add((r,c))
        suspect={v for v in suspect if not board[v[0]][v[1]]}
        recovered=[];local_checks=[]
        for index,(r,c) in enumerate(sorted(suspect)):
            uncertain.add((r,c))
            if index>=8:continue
            x,y=40+c*64,40+r*64
            l,t=max(0,x-120),max(0,y-120);rr,bb=min(592,x+120),min(656,y+120)
            candidates=[(score,piece) for cx,cy,score,piece in self._detect(warp[t:bb,l:rr],.5)
                        if ((cx+l-x)**2+(cy+t-y)**2)**.5<15]
            candidates.sort(reverse=True)
            local_checks.append({"row":r,"col":c,"candidates":[[float(score),piece] for score,piece in candidates[:3]]})
            matched=False
            if candidates and candidates[0][0]>=.8:
                score,piece=candidates[0]
                if len(candidates)<2 or candidates[1][1]==piece or score-candidates[1][0]>.15:
                    board[r][c]=piece;confidence[r][c]=score;recovered.append([r,c,piece]);matched=True
            if not matched and (r,c) in round_objects:
                secondary=self._classify_recheck(warp,x,y)
                if secondary:
                    piece,score=secondary
                    board[r][c]=piece;recovered.append([r,c,piece])
                    local_checks[-1]["classifier_piece"]=piece
                    local_checks[-1]["classifier_confidence"]=score
                    # This is not a detector confidence or a confirmed cell.
                    confidence[r][c]=None
        if not any(piece for row in board for piece in row) and not uncertain:
            raise ValueError("未检测到棋子，请检查采集画面、棋盘范围或标出四角后重试")
        return dict(board=board,confidence=confidence,uncertain=[list(v) for v in sorted(uncertain)],
            corners=grid.tolist(),image_size=[w,h],recovered=recovered,local_checks=local_checks,
            warnings=(["疑似漏子格已标黄；局部复检结果仍需对照原图确认。"] if suspect else []),
            recognition_seconds=round(time.monotonic()-started,3))

class LayoutRecognizer:
    """Evaluation only: known grid replaces pose, official layout preprocessing."""
    def __init__(self,path):
        options=ort.SessionOptions();options.intra_op_num_threads=2
        self.session=ort.InferenceSession(str(path),sess_options=options,providers=["CPUExecutionProvider"])
    def recognize_image(self,image,corners=None):
        started=time.monotonic();rgb=np.array(image.convert("RGB"));h,w=rgb.shape[:2]
        scale=min(1,1600/max(w,h))
        grid=np.array(corners if corners is not None else np.array(find_grid(cv2.resize(rgb,(round(w*scale),round(h*scale)))))/scale,np.float32)
        matrix=cv2.getPerspectiveTransform(grid,np.array([[50,50],[400,50],[400,450],[50,450]],np.float32))
        aligned=cv2.warpPerspective(rgb,matrix,(450,500))[25:475,25:425]
        tensor=(cv2.resize(aligned,(280,315))-np.array([123.675,116.28,103.53]))/np.array([58.395,57.12,57.375])
        scores=self.session.run(None,{self.session.get_inputs()[0].name:np.ascontiguousarray(tensor.transpose(2,0,1)[None],dtype=np.float32)})[0][0]
        labels=".xKABNRCPkabnrcp";ids=scores.argmax(axis=-1)
        board=[["" if labels[ids[r*9+c]] in ".x" else labels[ids[r*9+c]] for c in range(9)] for r in range(10)]
        uncertain=[[i//9,i%9] for i in range(90) if float(scores[i,ids[i]])<.8 or labels[ids[i]]=="x"]
        return dict(board=board,uncertain=uncertain,corners=grid.tolist(),image_size=[w,h],recognition_seconds=time.monotonic()-started)
