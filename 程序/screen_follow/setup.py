"""One-time visual confirmation; main-board recommendations stay on main canvas."""
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from collections import deque
from datetime import datetime,timezone
from time import monotonic
import json
from PIL import ImageTk
from endgame_ui import EndgameWindow
from screen_follow.capture import CaptureError,WindowCapture,phone_window_choices,activate_source
from screen_follow.process import FollowProcess
from screen_follow.opening import infer_opening
from position_import import guess_red_bottom

class FollowSetup(EndgameWindow):
    def __init__(self,owner):
        self.worker=None;self.frame=None;self.capture=None;self.request=None;self.cropping=False
        self.capture_generation=0;self.capture_job=None;self.recognition_job=None
        self.capture_pending=False;self.selected_hwnd=None;self.frame_stale=False;self.capture_stage="idle"
        self.capture_hidden=False;self.worker_token=None
        self.capture_diagnostics=deque(maxlen=200)
        self.manual_choices=set();self.turn_inferred=False
        super().__init__(owner)
        self.initial_human=self.human_var.get()
        for field,selector in (("orientation",self.orientation_selector),
                ("human",self.human_selector),("turn",self.turn_selector)):
            selector.bind("<<ComboboxSelected>>",lambda e,field=field:self._manual_choice(field),add="+")
        self.title("投屏跟随 · 校准并确认手机棋局")
        self.apply_button.configure(text="开始跟随")
        for button in self.recognize_button.master.winfo_children():
            if isinstance(button,ttk.Button) and button.cget("text")=="选择截图":button.configure(text="重新选窗口")
            if isinstance(button,ttk.Button) and button.cget("text")=="取消识别":button.configure(text="取消采集 / 识别")
        self.status_var.set("先选择 ToDesk 手机窗口并点击“采集并识别”；如需修正可框选棋盘或标四角。确认后再开始跟随。")
        self.image_canvas.bind("<ButtonPress-1>",self.press)
        self.image_canvas.bind("<ButtonRelease-1>",self.release)
        self.image_canvas.bind("<B1-Motion>",self.drag)
        tools=ttk.Frame(self);tools.pack(fill="x",before=self.winfo_children()[0])
        self.choose_window_button=ttk.Button(tools,text="选择 ToDesk 窗口 / 重新采集",command=self.choose_image)
        self.choose_window_button.pack(side="left",padx=12,pady=5)
        ttk.Button(tools,text="框选棋盘区域（拖动）",command=self.crop_mode).pack(side="left")
        self.model_user_selected=False
        self.model_var=tk.StringVar(master=self,value="兼容模型")
        self.model_selector=ttk.Combobox(tools,textvariable=self.model_var,values=("兼容模型","JJ截图候选（需确认）"),state="readonly",width=21)
        self.model_selector.pack(side="left",padx=6)
        self.model_selector.bind("<<ComboboxSelected>>",lambda e:setattr(self,"model_user_selected",True))
        self.retry_button=ttk.Button(tools,text="重试采集",command=self.retry_capture,state="disabled")
        self.retry_button.pack(side="left",padx=6)
        ttk.Button(tools,text="导出诊断",command=self.export_diagnostics).pack(side="left")
        progress=ttk.Frame(self,padding=(12,4))
        progress.pack(fill="x",after=tools)
        self.progress_var=tk.StringVar(master=self,value="请选择手机窗口，然后采集并识别。")
        self.progress_label=ttk.Label(progress,textvariable=self.progress_var,wraplength=900)
        self.progress_label.pack(fill="x")
        progress.bind("<Configure>",lambda e:self.progress_label.configure(wraplength=max(200,e.width-24)))
        # Let the previews yield space to confirmation controls at high DPI.
        self.image_canvas.configure(height=1);self.board_canvas.configure(height=1)
        self.geometry(f"1000x{max(640,min(800,self.winfo_screenheight()-80))}")
        self._refresh_preview_view()

    def _manual_choice(self,field):
        # Only real selection events lock a field, not programmatic StringVar.set.
        self.manual_choices.add(field)
        self._refresh_opening_defaults()

    def _reset_opening_choices(self):
        self.manual_choices.clear();self.turn_inferred=False
        self.orientation_var.set("请选择");self.turn_var.set("请选择")
        self.human_var.set(self.initial_human)

    def set_result(self,result):
        direction=self.orientation_var.get()
        super().set_result(result)
        if "orientation" in self.manual_choices:self.orientation_var.set(direction)
        self._refresh_opening_defaults()
        if not self.frame_stale:self._set_stage("ready","识别完成，请检查棋盘；黄色格仍须确认。",update_status=False)

    def _refresh_opening_defaults(self):
        if self.draft is None:return
        if "orientation" not in self.manual_choices:
            direction=guess_red_bottom(self.draft.board)
            self.orientation_var.set("请选择" if direction is None else "红方" if direction else "黑方")
        direction={"红方":True,"黑方":False}.get(self.orientation_var.get())
        self.draft.red_bottom=direction
        opening=infer_opening(self.draft.board,direction)
        if opening is not None:
            if "human" not in self.manual_choices:
                self.human_var.set("红方" if opening.own_red else "黑方")
            if "turn" not in self.manual_choices:
                self.turn_var.set("红方" if opening.red_to_move else "黑方")
                self.turn_inferred=True
            reason="标准开局（红方先走）" if opening.kind=="initial" else "红方已走一步（应黑方走）"
            manual="；保留手动选择" if self.manual_choices & {"human","turn"} else "；可手动修改"
            hint=f"已识别{reason}；当前我执{self.human_var.get()}，轮到{self.turn_var.get()}{manual}。"
        else:
            if self.turn_inferred and "turn" not in self.manual_choices:self.turn_var.set("请选择")
            self.turn_inferred=False
            hint="未匹配标准开局或红方非吃子首步，请手动确认我方和轮次。"
        result=self.last_result or {}
        seconds=result.get("total_seconds",result.get("recognition_seconds",0))
        message=f"识别完成（{seconds} 秒），{len(self.draft.uncertain)} 个待确认格。{hint}"
        missing=sum(not self.draft.board[r][c] for r,c in self.draft.uncertain)
        if missing:message+=f" {missing} 个黄色空格疑似漏子，请补棋；确认为空时用“清空”逐格点选。"
        self.status_var.set(message)
        self._refresh_preview_view()

    def board_click(self,event):
        if self.draft is None:return
        before=([row[:] for row in self.draft.board],self.draft.uncertain.copy())
        super().board_click(event)
        if before!=(self.draft.board,self.draft.uncertain):self._refresh_opening_defaults()

    def _refresh_preview_view(self,*args):
        if not getattr(self,"alive",False):return
        if self.view_mode=="auto":
            self._refreshing_view=True
            self.view_var.set("红方在下" if self.human_var.get()=="红方" else "黑方在下")
            self._refreshing_view=False
        self.draw_board()

    def choose_image(self):
        if not self.alive:return
        self.cancel_recognition()
        picker=self.__dict__.get("window_picker")
        if picker is not None and picker.winfo_exists():picker.destroy()
        choices=phone_window_choices()
        win=tk.Toplevel(self);win.title("选择正在显示手机的 ToDesk 窗口")
        var=win.choice_var=tk.StringVar(master=win)
        labels=[f"{title}  [{hwnd}]" for hwnd,title in choices]
        box=ttk.Combobox(win,textvariable=var,values=labels,width=65,state="readonly");box.pack(padx=15,pady=15)
        self.window_selector=box;self.window_picker=win
        if labels:box.current(0)
        else:self.status_var.set("没有找到可见的投屏窗口，请展开 ToDesk 手机画面后重新选择")
        def take():
            if box.current()<0:return
            hwnd=choices[box.current()][0]
            self.capture_selected(hwnd)
        self.capture_button=ttk.Button(win,text="采集并识别",command=take,state="normal" if labels else "disabled")
        self.capture_button.pack(pady=10)
        win.transient(self);win.update_idletasks()
        x=max(0,min(self.winfo_rootx()+60,self.winfo_screenwidth()-win.winfo_reqwidth()-20))
        y=max(0,min(self.winfo_rooty()+60,self.winfo_screenheight()-win.winfo_reqheight()-60))
        win.geometry(f"+{x}+{y}")

    def _record(self,stage,**details):
        self.capture_diagnostics.append(dict(time=datetime.now(timezone.utc).isoformat(),
            operation=self.capture_generation,stage=stage,**details))

    def _set_stage(self,stage,message,*,error=False,update_status=True):
        self.capture_stage=stage
        if update_status:self.status_var.set(message)
        if "progress_var" in self.__dict__:
            self.progress_var.set(message)
            self.progress_label.configure(foreground="#A32626" if error else "#24543B")
        self._record(stage,message=message)
        self.draw_image()

    def draw_image(self):
        super().draw_image()
        if not self.alive:return
        c=self.image_canvas
        if self.preview is None:
            c.create_text(max(1,c.winfo_width())/2,max(1,c.winfo_height())/2,
                text=self.progress_var.get() if "progress_var" in self.__dict__ else "等待采集手机画面",
                fill="white",width=max(120,c.winfo_width()-30),tags=("capture_status",))
        elif self.frame_stale:
            c.create_rectangle(0,0,c.winfo_width(),38,fill="#752525",outline="",tags=("stale_frame",))
            c.create_text(12,19,anchor="w",text="上次画面，本次采集未成功；请重试采集",fill="white",tags=("stale_frame",))

    def _capture_controls(self):
        if not self.alive:return
        busy=self.capture_pending
        self.choose_window_button.configure(state="disabled" if busy else "normal")
        self.retry_button.configure(state="disabled" if busy or self.selected_hwnd is None else "normal")
        self.recognize_button.configure(state="disabled" if busy or self.request or self.frame_stale else "normal")
        self.model_selector.configure(state="disabled" if busy or self.request else "readonly")
        self.apply_button.configure(state="normal" if self.draft is not None and self.worker is not None and not
            (busy or self.request or self.frame_stale) else "disabled")

    def _restore_capture_windows(self):
        if not self.capture_hidden:return
        self.capture_hidden=False
        if not self.alive or self.owner.closing:return
        # Restore the owner's previous state rather than opening a withdrawn owner.
        if self.owner_capture_state=="normal":self.owner.deiconify()
        self.deiconify();self.lift()

    def _cancel_capture(self):
        self.capture_generation+=1
        if self.capture_job is not None:
            self.after_cancel(self.capture_job);self.capture_job=None
        self.capture_pending=False
        self._restore_capture_windows()

    def retry_capture(self):
        if self.selected_hwnd is None:self.choose_image()
        else:self.capture_selected(self.selected_hwnd)

    def capture_selected(self,hwnd):
        if not self.alive or self.owner.closing or self.capture_pending:return
        self.cancel_recognition()
        self.capture_generation+=1
        self.selected_hwnd=int(hwnd);self.capture=None;self.frame_stale=self.frame is not None
        self.cropping=False;self.marking=False
        self.__dict__.pop("crop_start",None)
        self.capture_pending=True;generation=self.capture_generation
        self._pending_capture=None;self._ready_count=0;self._capture_deadline=monotonic()+2
        self._last_readiness_error=None
        self._capture_controls()
        self._set_stage("preparing","正在准备手机画面……")
        try:
            # Request foreground while the explicit button's input is still current.
            # Readiness is checked asynchronously; do not fail on an immediate check.
            activate_source(hwnd,verify=False)
            picker=self.__dict__.get("window_picker")
            if picker is not None and picker.winfo_exists():picker.destroy()
            self.owner_capture_state=self.owner.state();self.capture_hidden=True
            self.withdraw();self.owner.withdraw()
            self.capture_job=self.after(100,lambda:self._poll_capture(generation))
        except Exception as error:self._capture_failed(error)

    def _poll_capture(self,generation):
        if generation!=self.capture_generation or not self.capture_pending or not self.alive or self.owner.closing:return
        self.capture_job=None
        if monotonic()>=self._capture_deadline:
            self._capture_failed(self._last_readiness_error or CaptureError("等待手机窗口就绪超时，请重试采集", "readiness_timeout"));return
        try:
            if self._pending_capture is None:self._pending_capture=WindowCapture(self.selected_hwnd)
            self._pending_capture.check_ready(initial=True,require_foreground=True)
            self._ready_count+=1
        except CaptureError as error:
            self._ready_count=0
            self._last_readiness_error=error
            # Initial calibration may see the restored window finishing layout.
            # Live capture retains its strict size/DPI-change error behavior.
            if error.code=="size_changed":self._pending_capture=None
            self._record("readiness",code=error.code,message=str(error),details=error.details)
            if error.code not in ("target_unavailable","not_foreground","occluded","size_changed") or monotonic()>=self._capture_deadline:
                self._capture_failed(error);return
        except Exception as error:
            self._capture_failed(error);return
        if self._ready_count>=2:
            self._capture(self.selected_hwnd,generation);return
        self.capture_job=self.after(100,lambda:self._poll_capture(generation))

    def _capture_failed(self,error):
        self._record("capture_error",code=getattr(error,"code","capture_failed"),details=getattr(error,"details",{}))
        if getattr(error,"code",None)=="target_closed":self.selected_hwnd=None
        picker=self.__dict__.get("window_picker")
        if picker is not None and picker.winfo_exists():picker.destroy()
        self._cancel_capture();self.capture=None
        self._capture_controls()
        suffix="；当前仍显示上次画面，请重试采集。" if self.frame_stale else ""
        self._set_stage("capture_failed","采集失败："+str(error)+suffix,error=True)

    def _capture(self,hwnd,generation):
        if generation!=self.capture_generation or not self.capture_pending or not self.alive or self.owner.closing:return
        frame=None
        try:
            frame=self._pending_capture.grab(initial=True)
            self.capture_pending=False
            self.capture=self._pending_capture;self.set_frame(frame)
            if not self.model_user_selected:self.model_var.set("JJ截图候选（需确认）")
        except Exception as error:
            self._capture_failed(error);return
        finally:
            if frame is not None:frame.close()
        self._restore_capture_windows();self._capture_controls()
        self._set_stage("captured","截图已取得，正在本地识别……")
        self.capture_job=self.after_idle(lambda:self._recognize_captured(generation))

    def _recognize_captured(self,generation):
        if generation!=self.capture_generation or not self.alive or self.owner.closing:return
        self.capture_job=None
        self.recognize()

    def export_diagnostics(self):
        path=filedialog.asksaveasfilename(parent=self,title="导出采集诊断（不含图片）",
            defaultextension=".json",filetypes=[("JSON","*.json")])
        if not path:return
        try:
            with open(path,"w",encoding="utf-8") as stream:
                json.dump(list(self.capture_diagnostics),stream,ensure_ascii=False,indent=2)
        except OSError as error:messagebox.showerror("诊断导出失败",str(error),parent=self)

    def set_frame(self,frame,*,new_capture=True):
        self.cancel_recognition()
        self.frame_stale=False
        if self.frame is not None:self.frame.close()
        if self.preview is not None:self.preview.close()
        self.frame=frame.convert("RGB");self.preview=self.frame.copy();self.image_size=self.frame.size
        self.path="memory";self.corners=[];self.draft=None;self.last_result=None;self.marking=False
        if new_capture:self._reset_opening_choices()
        self.apply_button.configure(state="disabled")
        self.status_var.set("拖动框选棋盘（含完整边缘棋子），然后本地识别。四角定位仍是交叉点。")
        self.draw_image();self.draw_board()

    def crop_mode(self):
        if self.capture_pending or self.frame_stale:return
        if self.frame is not None:
            self.cropping=True;self.marking=False
            self.status_var.set("在左侧图片拖出棋盘区域，保留完整的边缘棋子。")

    def press(self,event):
        if self.cropping:
            l,t,w,h=self.image_rect
            self.crop_start=(event.x,event.y)
        else:self.image_click(event)

    def drag(self,event):
        if self.cropping and hasattr(self,"crop_start"):
            self.image_canvas.delete("crop_selection")
            self.image_canvas.create_rectangle(*self.crop_start,event.x,event.y,outline="#18E89B",width=2,tags=("crop_selection",))

    def release(self,event):
        if self.capture_pending or self.frame_stale or self.capture is None:return
        if not self.cropping or not hasattr(self,"crop_start"):return
        l,t,w,h=self.image_rect;sw,sh=self.image_size
        x1,y1=self.crop_start;x2,y2=event.x,event.y
        box=(max(0,round((min(x1,x2)-l)*sw/w)),max(0,round((min(y1,y2)-t)*sh/h)),
             min(sw,round((max(x1,x2)-l)*sw/w)),min(sh,round((max(y1,y2)-t)*sh/h)))
        if box[2]-box[0]<100 or box[3]-box[1]<100:return
        old=self.capture.roi
        self.capture.roi=(old[0]+box[0],old[1]+box[1],old[0]+box[2],old[1]+box[3])
        cropped=self.frame.crop(box);self.cropping=False;self.set_frame(cropped,new_capture=False);cropped.close()
        generation=self.capture_generation
        self.capture_job=self.after_idle(lambda:self._recognize_captured(generation))

    def recognize(self):
        if self.capture_pending or self.request:return
        if self.frame_stale:
            self._set_stage("capture_failed","当前是上次画面，请先重试采集。",error=True);return
        if self.frame is None:self.choose_image();return
        if self.marking:self.status_var.set("请先完成四角标记");return
        try:
            self.generation+=1
            if self.worker is None:
                token=self.worker_token=object()
                self.worker=FollowProcess(lambda event:self.owner._post_ui(lambda:self.receive(event,token)))
            self._set_stage("recognizing","截图已取得，正在本地识别；黄色格仍须检查。")
            self.request=self.worker.recognize(self.frame,self.corners if len(self.corners)==4 else None,
                "tchess" if self.model_var.get().startswith("JJ") else "compatible")
            generation=self.generation
            self.recognition_job=self.after(30000,lambda:self._timeout(generation))
            self._capture_controls()
        except Exception as error:self._recognition_failed(error)

    def _timeout(self,generation):
        if self.alive and generation==self.generation and self.request:
            self._recognition_failed(RuntimeError("识别超时，请重试本地识别"))

    def receive(self,event,source=None):
        if not self.alive:return
        if source is not None and source is not self.worker_token:return
        if event.get("fatal"):
            self._recognition_failed(RuntimeError(event.get("message","识别进程已退出")));return
        if not self.request:return
        if event.get("id")!=self.request:return
        if self.recognition_job is not None:self.after_cancel(self.recognition_job);self.recognition_job=None
        self.request=None
        try:
            if event["kind"]=="recognized":self.set_result(event["result"])
            else:raise RuntimeError(event.get("message","识别失败"))
        except Exception as error:self._recognition_failed(error)
        self._capture_controls()

    def _recognition_failed(self,error):
        self.cancel_recognition();self.draft=None;self.last_result=None
        self.draw_board();self._capture_controls()
        self._set_stage("recognition_failed","识别失败："+str(error)+"；可校正网格或重新识别。",error=True)

    def cancel_recognition(self):
        pending=self.capture_pending or self.capture_job is not None
        was_recognizing=self.request is not None
        had_worker=self.worker is not None
        if pending:self._cancel_capture()
        self.generation+=1;self.request=None
        self.worker_token=None
        if self.recognition_job is not None:self.after_cancel(self.recognition_job);self.recognition_job=None
        if self.worker:
            self.worker.close();self.worker=None
        if getattr(self,"alive",False):
            self._capture_controls()
            if pending or was_recognizing or had_worker:self._set_stage("cancelled","采集／识别已取消，原棋局保持。")

    def confirm_uncertain(self):
        if self.draft is None:return
        missing=[(r,c) for r,c in self.draft.uncertain if not self.draft.board[r][c]]
        if missing:
            self.status_var.set("仍有疑似漏子的黄色空格：请补入棋子；确认为空时，选“清空”并逐格点击。")
            return
        super().confirm_uncertain()
        self._refresh_opening_defaults()

    def apply(self):
        try:
            if self.capture_pending or self.frame_stale or self.request:raise ValueError("请先完成本次采集和识别")
            if self.draft is None or self.capture is None or self.worker is None:raise ValueError("请先识别并确认画面")
            self.draft.red_bottom={"红方":True,"黑方":False}.get(self.orientation_var.get())
            pos=self.draft.position({"红方":True,"黑方":False}.get(self.turn_var.get()))
            worker=self.worker
            if self.owner._follow_begin(pos,self.human_var.get()=="红方",self.view_mode,
                    worker,self.capture,self.corners,self.draft.red_bottom):
                self.worker=None;self.close()
        except Exception as e:messagebox.showerror("尚不能开始跟随",str(e),parent=self)

    def close(self):
        if not self.alive:return
        self._cancel_capture()
        self.cancel_recognition();self.alive=False
        for job in self.tk.call("after","info"):
            # Only own window registered commands, not the application pump.
            try:
                script=self.tk.call("after","info",job)[0]
                if str(script) in self.__dict__.get("_tclCommands",[]):self.after_cancel(job)
            except tk.TclError:pass
        if self.frame:self.frame.close()
        if self.preview:self.preview.close()
        self.photo=None;self.frame=None;self.preview=None
        self.owner.follow_setup=None;self.destroy();self.owner._refresh_player_side()
        if not self.owner.closing:
            if self.owner.__dict__.get("follow_session"):self.owner.after(10,self.owner._follow_request)
            elif self.owner._is_ai_turn():self.owner._schedule_opponent()
