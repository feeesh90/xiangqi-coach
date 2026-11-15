"""Image import preview. All Tk actions run on the UI thread."""
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from position_import import BoardDraft, ImportErrorDetail
from recognition_process import RecognitionJob
from rules import piece_name

VIEWS = {"auto":"自动（己方在上）","red_bottom":"红方在下","black_bottom":"黑方在下"}
PIECE_OPTIONS = {"清空":""}
PIECE_OPTIONS.update({("红" if p.isupper() else "黑")+piece_name(p):p for p in "RNBAKCP rnbakcp".replace(" ","")})


class EndgameWindow(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.title("图片导入残局 · 本地识别")
        self.transient(owner)
        self.geometry("1000x700")
        self.minsize(960,640)
        self.protocol("WM_DELETE_WINDOW",self.close)
        self.alive = True
        self.generation = 0
        self.job = None
        self.path = None
        self.preview = self.photo = None
        self.image_size = None
        self.corners = []
        self.marking = False
        self.draft = None
        self.last_result = None
        self.status_var = tk.StringVar(master=self,value="选择完整、清晰的象棋截图。图片只在本机读取，不额外保存。")
        self.orientation_var = tk.StringVar(master=self,value="请选择")
        self.turn_var = tk.StringVar(master=self,value="请选择")
        self.human_var = tk.StringVar(master=self,value="红方" if owner.human_red else "黑方")
        self.view_mode = owner.board_view_mode
        self._refreshing_view = False
        self.view_var = tk.StringVar(master=self,value="黑方在下" if owner._view_flipped() else "红方在下")
        self.piece_var = tk.StringVar(master=self,value="清空")
        body = ttk.Frame(self,padding=12)
        body.pack(fill="both",expand=True)
        bar = ttk.Frame(body);bar.pack(fill="x")
        ttk.Button(bar,text="选择截图",command=self.choose_image).pack(side="left")
        ttk.Button(bar,text="标四角 / 校正网格",command=self.mark_corners).pack(side="left",padx=6)
        self.recognize_button = ttk.Button(bar,text="本地识别",command=self.recognize)
        self.recognize_button.pack(side="left")
        ttk.Button(bar,text="取消识别",command=self.cancel_recognition).pack(side="left",padx=6)
        panels = ttk.Frame(body);panels.pack(fill="both",expand=True,pady=10)
        panels.columnconfigure(0,weight=1);panels.columnconfigure(1,weight=1)
        panels.rowconfigure(1,weight=1)
        ttk.Label(panels,text="原图：四角按左上 → 右上 → 右下 → 左下点击").grid(row=0,column=0,sticky="w")
        ttk.Label(panels,text="预览可翻转；点交叉点即可修改棋子").grid(row=0,column=1,sticky="w")
        self.image_canvas = tk.Canvas(panels,bg="#202622",highlightthickness=0)
        self.image_canvas.grid(row=1,column=0,sticky="nsew",padx=(0,8))
        self.board_canvas = tk.Canvas(panels,bg="#EED4A5",highlightthickness=0)
        self.board_canvas.grid(row=1,column=1,sticky="nsew")
        self.image_canvas.bind("<Configure>",lambda e:self.draw_image())
        self.board_canvas.bind("<Configure>",lambda e:self.draw_board())
        self.image_canvas.bind("<Button-1>",self.image_click)
        self.board_canvas.bind("<Button-1>",self.board_click)
        edits = ttk.Frame(body);edits.pack(fill="x",pady=(0,8))
        ttk.Label(edits,text="点击时放置").pack(side="left")
        ttk.Combobox(edits,textvariable=self.piece_var,values=list(PIECE_OPTIONS),state="readonly",width=9).pack(side="left",padx=6)
        ttk.Button(edits,text="确认标记格",command=self.confirm_uncertain).pack(side="left")
        ttk.Label(edits,text="黄色 = 待确认；识别结果可能漏子，请对照整盘。").pack(side="left",padx=10)
        options = ttk.Frame(body);options.pack(fill="x")
        for name,label,var,values,width in (
            ("orientation_selector","图片下方",self.orientation_var,("红方","黑方"),8),
            ("turn_selector","轮到谁走",self.turn_var,("红方","黑方"),8),
            ("human_selector","我执",self.human_var,("红方","黑方"),8)):
            ttk.Label(options,text=label).pack(side="left",padx=(0,4))
            selector=ttk.Combobox(options,textvariable=var,values=values,state="readonly",width=width)
            selector.pack(side="left",padx=(0,10))
            setattr(self,name,selector)
        self.flip_board_button=ttk.Button(options,text="翻转棋盘",command=self.flip_board)
        self.flip_board_button.pack(side="left")
        ttk.Label(options,textvariable=self.view_var).pack(side="left",padx=6)
        self.orientation_var.trace_add("write",lambda *args:self.draw_board())
        self.view_var.trace_add("write",self._preview_view_changed)
        self.human_var.trace_add("write",self._refresh_preview_view)
        ttk.Label(body,textvariable=self.status_var,wraplength=950).pack(fill="x",pady=10)
        footer = ttk.Frame(body);footer.pack(fill="x")
        ttk.Label(footer,text="确认后替换当前本地棋局；取消则保留原棋局。").pack(side="left")
        self.apply_button = ttk.Button(footer,text="确认导入",command=self.apply,state="disabled")
        self.apply_button.pack(side="right")
        ttk.Button(footer,text="取消",command=self.close).pack(side="right",padx=8)

    def choose_image(self):
        path = filedialog.askopenfilename(parent=self,title="选择象棋截图",
                    filetypes=[("棋盘截图","*.png *.jpg *.jpeg")])
        if path:self.load_image(path)

    def load_image(self,path):
        try:
            from board_recognizer import load_image
            image = load_image(path)
            size = image.size
            image.thumbnail((900,900))
        except ModuleNotFoundError:
            messagebox.showerror("图片组件缺失","请先运行项目中的“安装图片识别.bat”，然后重新启动软件。",parent=self);return
        except Exception as e:
            messagebox.showerror("图片无法打开",str(e),parent=self);return
        self.cancel_recognition()
        self.path = Path(path)
        if self.preview is not None:self.preview.close()
        self.preview,self.image_size = image,size
        self.corners=[];self.marking=False;self.draft=None;self.last_result=None
        self.orientation_var.set("请选择")
        self.apply_button.configure(state="disabled")
        self.status_var.set("已选图。点击“本地识别”；定位失败时标出四个最外侧交叉点。")
        self.draw_image();self.draw_board()

    def draw_image(self):
        if not self.alive:return
        c = self.image_canvas;c.delete("all")
        if self.preview is None:return
        from PIL import ImageTk
        w,h = max(1,c.winfo_width()),max(1,c.winfo_height())
        image = self.preview.copy();image.thumbnail((max(1,w-12),max(1,h-12)))
        self.photo = ImageTk.PhotoImage(image,master=self)
        iw,ih = image.size
        self.image_rect = ((w-iw)/2,(h-ih)/2,iw,ih)
        c.create_image(w/2,h/2,image=self.photo)
        image.close()
        if self.corners:
            pts = [self.image_pixel(x,y) for x,y in self.corners]
            for i,(x,y) in enumerate(pts):
                c.create_oval(x-4,y-4,x+4,y+4,fill="#18E89B",outline="white")
                c.create_text(x+10,y+10,text=str(i+1),fill="#18E89B")
            if len(pts)==4:
                tl,tr,br,bl = pts
                for n in range(9):
                    f=n/8
                    c.create_line(tl[0]*(1-f)+tr[0]*f,tl[1]*(1-f)+tr[1]*f,
                                  bl[0]*(1-f)+br[0]*f,bl[1]*(1-f)+br[1]*f,fill="#18E89B")
                for n in range(10):
                    f=n/9
                    c.create_line(tl[0]*(1-f)+bl[0]*f,tl[1]*(1-f)+bl[1]*f,
                                  tr[0]*(1-f)+br[0]*f,tr[1]*(1-f)+br[1]*f,fill="#18E89B")

    def image_pixel(self,x,y):
        left,top,w,h = self.image_rect
        return left+x*w/self.image_size[0],top+y*h/self.image_size[1]

    def mark_corners(self):
        if self.preview is None:return
        self.cancel_recognition()
        self.corners=[];self.marking=True
        self.draft=None;self.last_result=None
        self.apply_button.configure(state="disabled")
        self.draw_board()
        self.status_var.set("依次点击左上、右上、右下、左下的最外侧交叉点（不是棋盘边框）。")
        self.draw_image()

    def image_click(self,e):
        if not self.marking or self.preview is None:return
        l,t,w,h = self.image_rect
        if not (l<=e.x<l+w and t<=e.y<t+h):return
        self.corners.append([(e.x-l)*self.image_size[0]/w,(e.y-t)*self.image_size[1]/h])
        if len(self.corners)==4:
            self.marking=False
            self.status_var.set("网格已标出。检查网格是否对齐棋子中心，再点击“本地识别”。")
        self.draw_image()

    def recognize(self):
        if self.path is None:
            self.choose_image();return
        if self.job is not None and not self.job.finished.is_set():
            self.status_var.set("上一次识别正在停止，请稍候再试。");return
        if self.marking:
            self.status_var.set("请先完成四角标记。");return
        if self.owner.mode=="lan" or self.owner.lan_connecting:
            self.status_var.set("请先退出联机，再导入残局。");return
        self.generation+=1;generation=self.generation
        self.draft=None;self.last_result=None
        self.draw_board()
        job = self.job = RecognitionJob()
        self.recognize_button.configure(state="disabled")
        self.apply_button.configure(state="disabled")
        self.status_var.set("正在本机加载模型和识别，可取消；图片不上传、不另存。")
        path,corners = self.path,self.corners[:] if len(self.corners)==4 else None
        def work():
            try:result=job.run(path,corners);error=None
            except Exception as e:result=None;error=e
            def finish():
                if not self.alive:return
                if self.job is job:
                    self.job=None;self.recognize_button.configure(state="normal")
                if generation!=self.generation:return
                if error:
                    self.status_var.set(str(error));return
                self.set_result(result)
            self.owner._post_ui(finish)
        threading.Thread(target=work,daemon=True).start()

    def set_result(self,result):
        self.draft = BoardDraft.from_result(result)
        self.last_result=result
        corners = result.get("corners")
        if isinstance(corners,list) and len(corners)==4:self.corners=corners
        direction=self.draft.red_bottom
        self.orientation_var.set("请选择" if direction is None else "红方" if direction else "黑方")
        seconds=result.get("total_seconds",result.get("recognition_seconds",0))
        self.status_var.set(f"识别完成（{seconds} 秒），{len(self.draft.uncertain)} 个待确认格。请对照整盘并选择轮到谁走。")
        self.apply_button.configure(state="normal")
        self.draw_image();self.draw_board()

    def cancel_recognition(self):
        self.generation+=1
        if self.job is not None:self.job.cancel()
        if self.alive:
            self.recognize_button.configure(state="normal")
            self.status_var.set("识别已取消，原棋局保持。")
            if self.draft:self.apply_button.configure(state="normal")

    def board_geo(self):
        w,h=max(1,self.board_canvas.winfo_width()),max(1,self.board_canvas.winfo_height())
        d=max(1,min((w-48)/8,(h-48)/9))
        return (w-8*d)/2,(h-9*d)/2,d

    def flip_board(self):
        self.view_mode="red_bottom" if self.view_var.get()=="黑方在下" else "black_bottom"
        self._refresh_preview_view()

    def _preview_view_changed(self,*args):
        if self._refreshing_view:return
        # Explicit preview selection (also used by automation) fixes the view.
        self.view_mode="black_bottom" if self.view_var.get()=="黑方在下" else "red_bottom"
        self.draw_board()

    def _refresh_preview_view(self,*args):
        if not self.alive:return
        if self.view_mode=="auto":
            # Local two-player games have no single player identity.
            red_bottom=self.owner.mode not in ("ai","lan") or self.human_var.get()=="黑方"
        else:
            red_bottom=self.view_mode=="red_bottom"
        self._refreshing_view=True
        try:
            self.view_var.set("红方在下" if red_bottom else "黑方在下")
        finally:
            self._refreshing_view=False
        self.draw_board()

    def preview_flipped(self):
        # The draft stays in image coordinates; only canvas coordinates rotate.
        source_red=self.orientation_var.get()!="黑方"
        display_red=self.view_var.get()!="黑方在下"
        return source_red!=display_red

    def draw_board(self):
        if not self.alive:return
        c=self.board_canvas;c.delete("all")
        ox,oy,d=self.board_geo()
        for r in range(10):c.create_line(ox,oy+r*d,ox+8*d,oy+r*d,fill="#795028")
        for col in range(9):
            for a,b in ((0,4),(5,9)) if col not in (0,8) else ((0,9),):
                c.create_line(ox+col*d,oy+a*d,ox+col*d,oy+b*d,fill="#795028")
        for start in (0,7):
            c.create_line(ox+3*d,oy+start*d,ox+5*d,oy+(start+2)*d,fill="#795028")
            c.create_line(ox+5*d,oy+start*d,ox+3*d,oy+(start+2)*d,fill="#795028")
        if not self.draft:return
        for r,row in enumerate(self.draft.board):
            for col,p in enumerate(row):
                vr,vc=(9-r,8-col) if self.preview_flipped() else (r,col)
                x,y=ox+vc*d,oy+vr*d
                if (r,col) in self.draft.uncertain:
                    c.create_rectangle(x-d*.45,y-d*.45,x+d*.45,y+d*.45,fill="#FFD767",outline="",tags=("uncertain",))
                if p:
                    radius=d*.40
                    c.create_oval(x-radius,y-radius,x+radius,y+radius,fill="#FFF0CF",outline="#A73434" if p.isupper() else "#292C25",width=2)
                    c.create_text(x,y,text=piece_name(p),fill="#B33136" if p.isupper() else "#202621",
                                  font=("SimSun",max(10,round(d*.38)),"bold"),tags=(f"piece:{r}:{col}",))

    def board_click(self,e):
        if not self.draft:return
        ox,oy,d=self.board_geo()
        c,r=round((e.x-ox)/d),round((e.y-oy)/d)
        if not (0<=r<10 and 0<=c<9) or abs(e.x-(ox+c*d))>d*.45 or abs(e.y-(oy+r*d))>d*.45:return
        if self.preview_flipped():r,c=9-r,8-c
        self.draft.set_piece(r,c,PIECE_OPTIONS[self.piece_var.get()])
        self.draw_board()

    def confirm_uncertain(self):
        if self.draft:
            self.draft.uncertain.clear();self.draw_board()
            self.status_var.set("标记格已确认；导入前仍会检查棋子数量、位置和将军状态。")

    def apply(self):
        if not self.draft:return
        if self.job is not None and not self.job.finished.is_set():
            self.status_var.set("请先等待识别完成或取消。");return
        try:
            if self.orientation_var.get() not in ("红方","黑方") or self.turn_var.get() not in ("红方","黑方"):
                raise ImportErrorDetail("请确认图片下方是哪一方，并选择轮到哪方走。")
            self.draft.red_bottom=self.orientation_var.get()=="红方"
            pos=self.draft.position(self.turn_var.get()=="红方")
            self.owner.import_position(pos,self.human_var.get()=="红方",self.view_mode)
        except Exception as e:
            messagebox.showerror("尚不能导入",str(e),parent=self);return
        self.close()

    def close(self):
        if not self.alive:return
        self.cancel_recognition()
        self.alive=False
        self.image_canvas.delete("all")
        self.photo=None
        if self.preview is not None:self.preview.close()
        self.preview=self.draft=self.last_result=self.path=None
        self.owner.endgame_window=None
        self.destroy()
        self.owner._refresh_player_side()
