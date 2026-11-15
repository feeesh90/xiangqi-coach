"""CPU screenshot recognition. No Tk, network requests, or image writes.

Model preprocessing/class order follows dffge552/xiangqi-pwa-offline at
3ff21f4502a03f30bb0df55db6f3814ceeb989f5 (adapter code MIT).
Model metadata declares AGPL-3.0; PP-OCRv4 is Apache-2.0.
See THIRD_PARTY_NOTICES.md and models/board_recognition/manifest.json.
Grid finding and safe draft handling belong to this application.
"""
import hashlib
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models" / "board_recognition"
MODEL_HASHES = {
    "online_xiangqi_piece_detector.onnx": "843cf2601a6414d83a2fff6ac95cab175fa677d66463928a837fc83686f68fd1",
    "online_xiangqi_classifier.onnx": "f8f41504fb6057a125815e51ccbf9a9640e1161559d4e713e8b531f7c0ae6d29",
    "ch_PP-OCRv4_rec_infer.onnx": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
}
CLASS_PIECES = "ARNPKCB"  # 士、车、马、卒、将、炮、相


def load_image(path):
    from PIL import Image, ImageOps
    p = Path(path)
    if p.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("图片不能超过 20 MB。")
    with Image.open(p) as source:
        if source.format not in ("PNG", "JPEG"):
            raise ValueError("请选择 PNG 或 JPEG 图片。")
        if source.width * source.height > 16_000_000 or min(source.size) < 100:
            raise ValueError("图片过大或过小；请使用清晰的棋盘截图。")
        return ImageOps.exif_transpose(source).convert("RGB")


def _clusters(values, tolerance=4):
    groups = []
    for v in sorted(values):
        if groups and abs(v - sum(groups[-1])/len(groups[-1])) < tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g)/len(g) for g in groups]


def _lattices(values, n, limit):
    found = []
    for a in values:
        for b in values:
            step = (b-a)/(n-1)
            if step < 14 or b-a < limit*.16:
                continue
            count = sum(any(abs(v-(a+i*step)) < max(3, step*.075) for v in values) for i in range(n))
            if count >= n-2:
                found.append((count, a, b, step))
    return sorted(found, reverse=True)[:30]


def find_grid(rgb):
    import cv2
    import numpy as np
    h, w = rgb.shape[:2]
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(grey, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=30,
                            minLineLength=max(35, min(w,h)*.07), maxLineGap=10)
    if lines is None:
        raise ValueError("未找到完整棋盘，请标出四个最外侧交叉点后重新识别。")
    xs, ys = [], []
    for x1, y1, x2, y2 in lines.reshape(-1,4):
        if abs(x1-x2) <= 2:
            xs.append((x1+x2)/2)
        if abs(y1-y2) <= 2:
            ys.append((y1+y2)/2)
    # A board often has both end ranks covered by pieces. Include plausible
    # one-pitch extensions, verified later by line support and board aspect.
    xc, yc = _clusters(xs), _clusters(ys)
    xv, yv = _lattices(xc, 9, w), _lattices(yc, 10, h)
    hsv = cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv,(10,45,70),(35,255,255))
    contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    # Warm rectangular board bounds are a fallback for line occlusion.
    warm_candidates=[]
    warm_bounds=[]
    for contour in contours:
        x,y,bw,bh = cv2.boundingRect(contour)
        if bw>140 and bh>160 and .7<bw/bh<1.05 and cv2.contourArea(contour)>bw*bh*.75:
            warm_bounds.append((x,y,bw,bh))
            dx,dy = bw/9,bh/10
            left,right,top,bottom = x+dx/2,x+bw-dx/2,y+dy/2,y+bh-dy/2
            nx = sum(any(abs(v-(left+i*dx))<max(3,dx*.12) for v in xc) for i in range(9))
            ny = sum(any(abs(v-(top+i*dy))<max(3,dy*.12) for v in yc) for i in range(10))
            if nx>=6 and ny>=6:
                xv.append((nx,left,right,dx))
                yv.append((ny,top,bottom,dy))
                warm_candidates.append((nx+ny,bw*bh,left,top,right,bottom))
    if warm_candidates:
        _,_,l,t,r,b=max(warm_candidates)
        return [[l,t],[r,t],[r,b],[l,b]]
    candidates = []
    for nx, left, right, dx in xv:
        for ny, top, bottom, dy in yv:
            if .85 < dx/dy < 1.15:
                # Palette/UI borders can coincide with a shifted lattice. A
                # grid should sit inside and near the centre of its board,
                # even when that skin uses margins wider than half a pitch.
                board_support=0.0
                for x,y,bw,bh in warm_bounds:
                    if (left>=x+dx*.25 and right<=x+bw-dx*.25 and
                            top>=y+dy*.25 and bottom<=y+bh-dy*.25 and
                            (right-left)>.65*bw and (bottom-top)>.65*bh):
                        displacement=(abs((left+right)/2-(x+bw/2))/dx+
                                      abs((top+bottom)/2-(y+bh/2))/dy)
                        board_support=max(board_support,4-3*displacement)
                candidates.append((nx+ny-abs(dx/dy-1)*10+board_support,
                                   (right-left)*(bottom-top), left, top, right, bottom))
    if not candidates:
        raise ValueError("无法确定 9 列 × 10 行网格，请标出四角后重新识别。")
    _, _, l, t, r, b = max(candidates)
    return [[l,t], [r,t], [r,b], [l,b]]


def validate_corners(corners, width, height):
    import numpy as np
    import cv2
    arr = np.asarray(corners, dtype=np.float32)
    if (arr.shape != (4,2) or not np.isfinite(arr).all() or
            (arr[:,0] < 0).any() or (arr[:,0] >= width).any() or
            (arr[:,1] < 0).any() or (arr[:,1] >= height).any()):
        raise ValueError("四角坐标错误。请依次标出左上、右上、右下、左下交叉点。")
    if not cv2.isContourConvex(arr) or cv2.contourArea(arr, oriented=True) < 5000:
        raise ValueError("四角顺序或范围错误。请依次选择左上、右上、右下、左下。")
    return arr


class BoardRecognizer:
    def __init__(self, model_dir=MODEL_DIR):
        import onnxruntime as ort
        paths = []
        for name, expected in MODEL_HASHES.items():
            p = Path(model_dir)/name
            if not p.is_file():
                raise ValueError(f"识别模型缺失：{name}。请运行“安装图片识别.bat”。")
            if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                raise ValueError(f"识别模型校验失败：{name}。请重新安装图片识别资源。")
            paths.append(p)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.detector, self.classifier, self.ocr = [ort.InferenceSession(str(p), sess_options=options,
                    providers=["CPUExecutionProvider"]) for p in paths]
        for session, expected_shape in ((self.detector, [1,3,640,640]), (self.classifier, [1,3,64,64])):
            if session.get_inputs()[0].shape != expected_shape:
                raise ValueError("识别模型输入与本版本不兼容。")
        # Upstream explicitly provides this seven-class sequence.
        if self.classifier.get_outputs()[0].shape[-1] != 7:
            raise ValueError("棋子分类模型输出与本版本不兼容。")
        alphabet = self.ocr.get_modelmeta().custom_metadata_map.get("character","").splitlines()
        self.alphabet = [""]+alphabet+[" "]
        if len(self.alphabet)!=self.ocr.get_outputs()[0].shape[-1]:
            raise ValueError("文字模型字符表不兼容。")

    def _ocr_piece(self, rgb, box, margin=.17):
        """Read the central upright glyph; PP-OCR uses BGR [-1,1], CTC."""
        import cv2
        import numpy as np
        x,y,w,h=box
        roi = rgb[max(0,round(y+h*margin)):min(rgb.shape[0],round(y+h*(1-margin))),
                  max(0,round(x+w*margin)):min(rgb.shape[1],round(x+w*(1-margin)))]
        if roi.size==0:return None,0.0,""
        channel=roi.astype(np.int16)
        rr,gg,bb=channel[:,:,0],channel[:,:,1],channel[:,:,2]
        red=(rr>gg+35)&(rr>bb+35)&(rr>gg*1.45)&(rr>bb*1.45)
        dark=(rr+gg+bb)/3<110
        yy,xx=np.ogrid[:roi.shape[0],:roi.shape[1]]
        inside=((xx-roi.shape[1]/2)/(w*.37))**2+((yy-roi.shape[0]/2)/(h*.37))**2<1
        ink=(red|dark)&inside
        clean=np.full(roi.shape,255,dtype=np.uint8)
        clean[ink]=0
        bgr=clean
        bgr=cv2.copyMakeBorder(bgr,3,3,3,3,cv2.BORDER_CONSTANT,value=(255,255,255))
        width=min(320,math.ceil(48*bgr.shape[1]/bgr.shape[0]))
        tensor=np.zeros((1,3,48,320),dtype=np.float32)
        tensor[0,:,:,:width]=cv2.resize(bgr,(width,48)).transpose(2,0,1)/127.5-1
        output=self.ocr.run(None,{self.ocr.get_inputs()[0].name:tensor})[0][0]
        ids=output.argmax(axis=1)
        chars=[];scores=[];previous=-1
        for i,k in enumerate(ids):
            if k and k!=previous:
                chars.append(self.alphabet[k]);scores.append(float(output[i,k]))
            previous=k
        text="".join(chars).strip()
        mapping={"士":"A","仕":"A","车":"R","車":"R","俥":"R","马":"N","馬":"N","傌":"N",
                 "兵":"P","卒":"P","将":"K","將":"K","帅":"K","帥":"K","炮":"C","砲":"C",
                 "相":"B","象":"B"}
        return mapping.get(text),min(scores) if scores else 0.0,text

    def _boxes(self, rgb):
        import cv2
        import numpy as np
        h, w = rgb.shape[:2]
        scale = min(640/w,640/h)
        nw, nh = round(w*scale), round(h*scale)
        ox, oy = (640-nw)//2, (640-nh)//2
        image = np.full((640,640,3),114,dtype=np.uint8)
        image[oy:oy+nh, ox:ox+nw] = cv2.resize(rgb,(nw,nh))
        tensor = np.ascontiguousarray(image.transpose(2,0,1)[None],dtype=np.float32)/255
        output = self.detector.run(None, {self.detector.get_inputs()[0].name:tensor})[0][0]
        if output.shape[0] == 5:
            output = output.T
        if output.shape[1] != 5:
            raise ValueError("棋子检测输出格式不兼容。")
        boxes, scores = [], []
        for x,y,bw,bh,confidence in output:
            if confidence < .25:
                continue
            boxes.append([float((x-bw/2-ox)/scale),float((y-bh/2-oy)/scale),float(bw/scale),float(bh/scale)])
            scores.append(float(confidence))
        indices = cv2.dnn.NMSBoxes(boxes,scores,.25,.45)
        return [(boxes[int(i)],scores[int(i)]) for i in np.asarray(indices).reshape(-1)]

    def recognize(self, path, corners=None):
        import cv2
        import numpy as np
        started = time.monotonic()
        return self.recognize_image(load_image(path), corners)

    def recognize_image(self, image, corners=None):
        """Read an in-memory RGB image; preserve original pixels for the warp."""
        import cv2
        import numpy as np
        started = time.monotonic()
        image = image.convert("RGB")
        width,height = image.size
        image_original = image
        scale = min(1,1600/max(image.size))
        image = image.resize((round(width*scale),round(height*scale)))
        rgb = np.array(image)
        if corners is None:
            grid = find_grid(rgb)
        else:
            grid = (validate_corners(corners,width,height)*scale).tolist()
        grid = validate_corners(grid,rgb.shape[1],rgb.shape[0])
        # Warp enough margin to keep edge pieces complete; intersection pitch 64.
        destination = np.array([[40,40],[552,40],[552,616],[40,616]],dtype=np.float32)
        matrix = cv2.getPerspectiveTransform(grid/scale,destination)
        warped = cv2.warpPerspective(np.array(image_original),matrix,(592,656),borderValue=(240,220,180))
        board = [[""]*9 for _ in range(10)]
        # Undetected cells have no measured empty-square confidence.
        confidence = [[None]*9 for _ in range(10)]
        uncertain, warnings, detections = set(), [], []
        boxes = self._boxes(warped)
        if not boxes:
            raise ValueError("未检测到棋子。请检查棋盘范围、图片清晰度或截图样式。")
        for (x,y,bw,bh), detected in boxes:
            cx,cy = x+bw/2,y+bh/2
            c,r = round((cx-40)/64),round((cy-40)/64)
            if not (0<=r<10 and 0<=c<9):
                continue
            if math.hypot(cx-(40+c*64),cy-(40+r*64)) > 64*.32:
                uncertain.add((r,c))
                warnings.append("部分棋子中心偏离交叉点，请检查网格和黄色格子。")
            x1,y1 = max(0,math.floor(x-bw*.15)),max(0,math.floor(y-bh*.15))
            x2,y2 = min(592,math.ceil(x+bw*1.15)),min(656,math.ceil(y+bh*1.15))
            roi = warped[y1:y2,x1:x2]
            if roi.size == 0:
                uncertain.add((r,c)); continue
            tensor = np.ascontiguousarray(cv2.resize(roi,(64,64)).transpose(2,0,1)[None],dtype=np.float32)/255
            probs = self.classifier.run(None,{self.classifier.get_inputs()[0].name:tensor})[0].reshape(-1)
            kind, score = int(probs.argmax()),float(probs.max())
            readings=[self._ocr_piece(warped,(x,y,bw,bh),margin) for margin in (.14,.20,.10)]
            known=[reading for reading in readings if reading[0]]
            ocr_kind,ocr_score,ocr_text=max(known or readings,key=lambda reading:reading[1])
            central = roi[roi.shape[0]//4:roi.shape[0]*3//4,roi.shape[1]//4:roi.shape[1]*3//4].astype(np.int16)
            rr,gg,bb = central[:,:,0],central[:,:,1],central[:,:,2]
            # Warm wood is not red ink; require a strong red/green ratio.
            red_mask = (rr>gg+35)&(rr>bb+35)&(rr>gg*1.45)&(rr>bb*1.45)
            black_mask = ((rr+gg+bb)/3<80)&(abs(rr-gg)<20)&(abs(gg-bb)<20)
            red_score = np.where(red_mask,np.where(rr>150,3,np.where(rr>100,2,1)),0).sum()
            black_score = np.where(black_mask,3,np.where((~red_mask)&((rr+gg+bb)/3<100),1,0)).sum()
            piece = CLASS_PIECES[kind]
            if ocr_kind and ocr_score>=.45:
                if piece!=ocr_kind:
                    uncertain.add((r,c))
                piece=ocr_kind
                score=ocr_score
            elif not ocr_kind:
                uncertain.add((r,c))
            if red_score <= black_score:
                piece = piece.lower()
            if max(red_score,black_score) < 10 or score < .80 or detected < .45:
                uncertain.add((r,c))
            if board[r][c]:
                uncertain.add((r,c))
                warnings.append("同一交叉点检测到多个棋子，请检查预览。")
                continue
            board[r][c],confidence[r][c] = piece,min(score,detected)
            detections.append({"row":r,"col":c,"piece":piece,"confidence":min(score,detected),
                               "ocr_text":ocr_text,"ocr_confidence":ocr_score,
                               "classifier_piece":CLASS_PIECES[kind],"classifier_scores":probs.tolist(),
                               "ocr_readings":[list(v) for v in readings],
                               "color_scores":[int(red_score),int(black_score)],
                               "box":[x,y,bw,bh]})
        return {"board":board,"confidence":confidence,"uncertain":[list(p) for p in sorted(uncertain)],
                "corners":(grid/scale).tolist(),"image_size":[width,height],
                "warnings":sorted(set(warnings)),"detections":detections,
                "recognition_seconds":round(time.monotonic()-started,3)}
