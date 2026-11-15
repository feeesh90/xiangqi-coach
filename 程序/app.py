import os, sys, threading, queue, time, tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from contextlib import nullcontext
from pathlib import Path
from copy import deepcopy

from rules import Position, Move, START_FEN, in_check, legal_moves, piece_name, move_desc, explain_move, chinese_notation
from engine_manager import EngineManager
from px0_engine import NoLegalMoves, SearchCancelled, UnsupportedFeature
from coach import best_explanation, user_move_report, premove_report, threat_report, candidate_report
from master_book import lookup as master_lookup, stats as master_stats
from api_client import ApiClient, ApiError
from lan_ui import LanUI
from creative_ui import CreativeUI
from screen_follow.ui import FollowUI
from ui_preferences import UIPreferences
from repetition import (from_history, cycle_move, blocked_cycle_move,
                        playable_moves, trim_pv, MESSAGE, FORCED_MESSAGE)

ROOT=Path(__file__).resolve().parent
WOOD="#DDA65D"; WOOD_DARK="#92592D"; LINE="#75451F"
GREEN="#14231D"; GREEN2="#20372D"; BG="#202622"; CARD="#F7F3EB"
RED="#B33136"; BLACK="#202621"; GOLD="#E0B45B"; BLUE="#2779D5"; TARGET="#1D8A68"
TEXT="#2D302C"; MUTED="#756E64"; PANEL_DARK="#1A211D"

LEVELS={
    "休闲":100,"入门":220,"初级":450,"中级":900,
    "进阶":1800,"高手":3500,"大师":7000
}
# V7 prioritizes interactive speed. Native 当前引擎 still searches locally.
COACH_MS={"极速":700,"标准":1300,"深入":2500}
SIM_MS={"极速":350,"标准":650,"深入":1000}
FORCED_MS={"极速":450,"标准":700,"深入":1200}
BOARD_VIEWS={"auto":"自动（己方在上）","red_bottom":"红方在下","black_bottom":"黑方在下"}

class App(FollowUI,CreativeUI,LanUI,tk.Tk):
    def __init__(self,preferences_path=None,engine_config_path=None):
        super().__init__()
        self.title("象棋指导棋 · 双引擎分析工作台")
        try:self.iconbitmap(default=str(ROOT/"assets"/"app.ico"))
        except Exception:pass
        width=min(1500,max(1180,self.winfo_screenwidth()-100))
        height=min(920,max(760,self.winfo_screenheight()-100))
        self.geometry(f"{width}x{height}")
        self.minsize(1080,720)
        self.configure(bg=BG)
        style=ttk.Style(self)
        try:style.theme_use("clam")
        except tk.TclError:pass
        style.configure("Coach.TCombobox",fieldbackground="#F5F1E8",background="#F5F1E8",
                        foreground=TEXT,borderwidth=0,padding=7)
        style.configure("Board.TButton",font=("Microsoft YaHei",-14),padding=(6,4))

        self.engine=EngineManager(ROOT,config_path=engine_config_path)
        self.ui_preferences=UIPreferences(LEVELS,COACH_MS,preferences_path)
        self.saved_ui_preferences=self.ui_preferences.load()
        self.human_red=True
        self.board_view_mode="auto"
        self.api=ApiClient()
        self.mode="ai"
        self.official=Position()
        self.game_start_fen=START_FEN
        self.game_source="standard"
        self.endgame_window=None
        self.engine_settings_window=None
        self.editor=None
        self.editor_snapshot=None
        self.sim=None
        self.history=[]
        self.sim_history=[]
        self.move_history=[]
        self.sim_move_history=[]
        self.sim_cycle_base=()
        self.last=None
        self.reco=None
        self.selected=None
        self.targets=[]
        self.busy=False
        self.task_token=0
        self.pv_cache=[]
        self.last_user_before=None
        self.last_user_move=None
        self.last_user_cycle_context=()
        self.premove_mode=False
        self.premove_candidate=None
        self.analysis_snapshot=None
        self.analysis_result=None
        self.active_plan=0
        self.opponent_job=None
        self.manual_takeover=False
        self.playback_moves=[]
        self.playback_index=0
        self.playback_after=None
        self.playback_paused=False
        self.playback_explanations=[]
        self.api_request_token=0
        self.api_last_feedback=""
        self.closing=False
        self.ui_queue=queue.Queue()
        self.ui_pump_after=None
        self.book_refresh_after=None
        self.engine_check_after=None
        self._init_lan()

        self._build()
        self._refresh_player_side()
        self._resize_board()
        self.ui_pump_after=self.after(30,self._drain_ui_queue)
        self.engine_check_after=self.after(100,self._engine_check)
        self.protocol("WM_DELETE_WINDOW",self._close)
        self.bind("<Escape>",self._escape_mode)

    def _build(self):
        self.columnconfigure(0,weight=1)
        self.rowconfigure(1,weight=1)

        # One calm header replaces the old full-height command rail.
        self.header=tk.Frame(self,bg=GREEN,height=70)
        self.header.grid(row=0,column=0,sticky="ew")
        self.header.pack_propagate(False)
        brand=tk.Frame(self.header,bg=GREEN)
        brand.pack(side="left",padx=(22,24),pady=12)
        tk.Label(brand,text="象棋指导棋",bg=GREEN,fg="white",
                 font=("Microsoft YaHei",18,"bold")).pack(anchor="w")
        tk.Label(brand,text="PIKAFISH  ·  ANALYSIS DESK",bg=GREEN,fg=GOLD,
                 font=("Arial",8,"bold")).pack(anchor="w")

        badges=tk.Frame(self.header,bg=GREEN)
        badges.pack(side="left",pady=17)
        self.engine_badge=tk.Label(badges,text="引擎检查中",bg="#4A554F",fg="white",
                                   font=("Microsoft YaHei",8,"bold"),padx=8,pady=4)
        self.engine_badge.pack(side="left",padx=(0,5))
        self.api_badge=tk.Label(badges,text="API检查中",bg="#4A554F",fg="white",
                                font=("Microsoft YaHei",8,"bold"),padx=8,pady=4)
        self.api_badge.pack(side="left")

        gamebar=tk.Frame(self.header,bg=GREEN)
        gamebar.pack(side="right",padx=18,pady=13)
        tk.Button(gamebar,text="新棋局",command=self.new_game,bd=0,bg="#314239",fg="white",
                  activebackground="#42564B",activeforeground="white",
                  font=("Microsoft YaHei",9,"bold"),padx=12,pady=7,cursor="hand2").pack(side="left",padx=2)
        tk.Button(gamebar,text="悔棋",command=self.undo,bd=0,bg="#314239",fg="white",
                  activebackground="#42564B",activeforeground="white",
                  font=("Microsoft YaHei",9,"bold"),padx=12,pady=7,cursor="hand2").pack(side="left",padx=2)
        modes=tk.Frame(gamebar,bg="#0D1712")
        modes.pack(side="left",padx=(10,0))
        self.ai_btn=self._mode_btn(modes,"人机",True,lambda:self._set_mode("ai"))
        self.pvp_btn=self._mode_btn(modes,"双人",False,lambda:self._set_mode("pvp"))
        self.ai_btn.pack(side="left");self.pvp_btn.pack(side="left")

        self.turn_label=tk.Label(self.header,text="● 红方行棋",bg=GREEN,fg="white",
                                 font=("Microsoft YaHei",11,"bold"))
        self.turn_label.pack(side="right",padx=(8,6))

        workspace=tk.Frame(self,bg=BG)
        workspace.grid(row=1,column=0,sticky="nsew")
        workspace.rowconfigure(0,weight=1);workspace.columnconfigure(0,weight=1)

        # Board stage: only chess and the actions used while looking at chess.
        self.board_stage=tk.Frame(workspace,bg=BG)
        self.board_stage.grid(row=0,column=0,sticky="nsew",padx=(18,10),pady=16)
        self.board_stage.rowconfigure(1,weight=1);self.board_stage.columnconfigure(0,weight=1)
        board_head=tk.Frame(self.board_stage,bg=BG)
        board_head.grid(row=0,column=0,sticky="ew",pady=(0,8))
        board_head.columnconfigure(0,weight=1)
        board_title=tk.Frame(board_head,bg=BG)
        board_title.grid(row=0,column=0,columnspan=5,sticky="ew",pady=(0,5))
        board_title.columnconfigure(0,weight=1)
        self.sim_label=tk.Label(board_title,text=f"正式棋局  ·  {self._view_name()}",bg=BG,fg="#F3EEE3",
                                anchor="w",justify="left",width=1,font=("Microsoft YaHei",12,"bold"))
        self.sim_label.grid(row=0,column=0,sticky="ew")
        self.sim_label.bind("<Configure>",lambda e:self.sim_label.configure(wraplength=max(1,e.width)))

        view_controls=tk.Frame(board_head,bg=BG)
        view_controls.grid(row=1,column=0,columnspan=5,sticky="w",pady=(0,5))
        self.flip_board_button=ttk.Button(view_controls,text="翻转棋盘",command=self._flip_board,width=8,style="Board.TButton")
        self.flip_board_button.pack(side="left")
        self.player_side_button=ttk.Button(view_controls,text="我执红方",command=self._toggle_player_side,
                width=8,style="Board.TButton")
        self.player_side_button.pack(side="left",padx=(5,0))
        self.board_view_var=tk.StringVar(master=self,value=self._view_name())
        self.board_view_label=tk.Label(view_controls,textvariable=self.board_view_var,
                bg=BG,fg="#9CA9A1",font=("Microsoft YaHei",8))
        self.board_view_label.pack(side="left",padx=5)
        self.creative_button=ttk.Button(view_controls,text="创造模式",command=self.enter_creative,width=8,style="Board.TButton")
        self.creative_button.pack(side="left")
        self._build_follow_controls(view_controls)
        self.lan_locked_buttons.append(self.creative_button)

        # Local position tools are independent of guidance and LAN permissions.
        position_tools=ttk.Menubutton(board_title,text="残局 ▾",width=6)
        position_tools.grid(row=0,column=1,sticky="e",padx=(5,0))
        position_menu=tk.Menu(position_tools,tearoff=False)
        position_menu.add_command(label="图片导入残局",command=self.open_endgame)
        position_menu.add_command(label="重置当前起点",command=self.restart_current_game)
        position_menu.add_command(label="恢复标准开局",command=self.new_game)
        position_tools.configure(menu=position_menu)

        self.level=tk.StringVar(master=self,value=self.saved_ui_preferences["level"])
        self.coach=tk.StringVar(master=self,value=self.saved_ui_preferences["coach"])
        tk.Label(board_head,text="棋力",bg=BG,fg="#9CA9A1",font=("Microsoft YaHei",8)).grid(row=2,column=1,padx=(8,4))
        self.level_selector=ttk.Combobox(board_head,textvariable=self.level,values=list(LEVELS),style="Coach.TCombobox",
                     state="readonly",font=("Microsoft YaHei",-14),width=5)
        self.level_selector.grid(row=2,column=2)
        tk.Label(board_head,text="指导速度",bg=BG,fg="#9CA9A1",font=("Microsoft YaHei",8)).grid(row=2,column=3,padx=(10,4))
        self.coach_selector=ttk.Combobox(board_head,textvariable=self.coach,values=list(COACH_MS),style="Coach.TCombobox",
                     state="readonly",font=("Microsoft YaHei",-14),width=5)
        self.coach_selector.grid(row=2,column=4)
        for selector in (self.level_selector,self.coach_selector):
            selector.bind("<<ComboboxSelected>>",self._save_ui_preferences)
        self._build_editor_controls(board_head)

        board_shell=tk.Frame(self.board_stage,bg=PANEL_DARK,padx=10,pady=10)
        board_shell.grid(row=1,column=0,sticky="nsew")
        board_shell.rowconfigure(0,weight=1);board_shell.columnconfigure(0,weight=1)
        self.canvas=tk.Canvas(board_shell,bg=PANEL_DARK,highlightthickness=0,cursor="hand2")
        self.canvas.grid(row=0,column=0,sticky="nsew")
        self.canvas.bind("<Button-1>",self.on_board_click)
        self.canvas.bind("<Configure>",lambda e:self.draw())

        self.action_bar=tk.Frame(self.board_stage,bg="#171D19",padx=7,pady=7)
        self.action_bar.grid(row=2,column=0,sticky="ew",pady=(8,0))
        for col in range(4):self.action_bar.columnconfigure(col,weight=1)
        self._action_button(self.action_bar,"推荐一步",self.recommend,0,0,"gold")
        self.ai_take_btn=self._action_button(self.action_bar,"AI 代走",self.ai_take,0,1,"blue")
        self.sim_btn=self._action_button(self.action_bar,"推演沙盘",self.toggle_sim,0,2)
        self._action_button(self.action_bar,"推演 4 步",self.simulate_four,0,3)
        self.takeover_btn=self._action_button(self.action_bar,"接管黑方",self.take_over_turn,1,0,"quiet")
        self._action_button(self.action_bar,"预走分析",self.toggle_premove,1,1,"quiet")
        self._action_button(self.action_bar,"复盘着法",self.analyze_my_move,1,2,"quiet")
        self._action_button(self.action_bar,"扫描威胁",self.scan_threats,1,3,"quiet")

        playbar=tk.Frame(self.board_stage,bg="#2C342F",padx=10,pady=6)
        self.playbar=playbar
        playbar.grid(row=3,column=0,sticky="ew",pady=(5,0))
        playbar.columnconfigure(0,weight=1)
        tk.Label(playbar,text="变化回放",bg="#2C342F",fg="#9EAAA3",
                 font=("Microsoft YaHei",8,"bold")).grid(row=0,column=0,sticky="w")
        self.playback_back=tk.Button(playbar,text="← 上一步",command=self.playback_step_back)
        self.playback_pause=tk.Button(playbar,text="暂停",command=self.toggle_playback_pause)
        self.playback_next=tk.Button(playbar,text="下一步 →",command=self.playback_step_next)
        for i,b in enumerate((self.playback_back,self.playback_pause,self.playback_next),1):
            b.configure(state="disabled",bd=0,bg="#3A443E",fg="#E4E9E6",
                        padx=10,pady=4,font=("Microsoft YaHei",8),cursor="hand2")
            b.grid(row=0,column=i,padx=(4,0))

        # Analysis card stays visually quiet until the user asks for analysis.
        self.analysis_panel=tk.Frame(workspace,bg=CARD,width=420,
                                     highlightthickness=1,highlightbackground="#CBC3B6")
        self.analysis_panel.grid(row=0,column=1,sticky="nse",padx=(0,18),pady=16)
        self.analysis_panel.pack_propagate(False)
        h=tk.Frame(self.analysis_panel,bg=CARD);h.pack(fill="x",padx=20,pady=(20,10))
        tk.Label(h,text="析",bg=GREEN,fg=GOLD,font=("Microsoft YaHei",17,"bold"),
                 width=2,height=1).pack(side="left")
        tt=tk.Frame(h,bg=CARD);tt.pack(side="left",padx=10)
        self.coach_title=tk.Label(tt,text="局面分析",bg=CARD,fg=TEXT,
                                   font=("Microsoft YaHei",15,"bold"))
        self.coach_title.pack(anchor="w")
        tk.Label(tt,text="候选着 · 主变化 · 大师棋谱",bg=CARD,fg=MUTED,
                 font=("Microsoft YaHei",9)).pack(anchor="w")
        tk.Frame(self.analysis_panel,bg="#D8D0C4",height=1).pack(fill="x",padx=20,pady=(0,8))

        self.plan_frame=tk.Frame(self.analysis_panel,bg=CARD)
        self.plan_frame.pack(fill="x",padx=18,pady=(4,7))
        self.plan_buttons=[]
        for i in range(3):
            b=tk.Button(self.plan_frame,text=f"候选 {i+1}",state="disabled",bd=0,
                        bg="#E7E1D6",fg=MUTED,font=("Microsoft YaHei",9,"bold"),
                        pady=7,cursor="hand2",command=lambda x=i:self.select_plan(x))
            b.pack(side="left",fill="x",expand=True,padx=(0 if i==0 else 3,0 if i==2 else 3))
            self.plan_buttons.append(b)

        apirow=tk.Frame(self.analysis_panel,bg=CARD)
        apirow.pack(fill="x",padx=18,pady=(0,7))
        self.api_explain_btn=tk.Button(apirow,text="☁  API 协作评议",bd=0,
                        bg="#DCE8E2",fg=GREEN,font=("Microsoft YaHei",9,"bold"),
                        pady=7,cursor="hand2",command=self.api_explain_current)
        self.api_explain_btn.pack(side="left",fill="x",expand=True,padx=(0,3))
        tk.Button(apirow,text="测试",bd=0,bg="#E8E2D7",fg="#5C5348",
                  font=("Microsoft YaHei",9),padx=12,pady=7,cursor="hand2",
                  command=self.api_test).pack(side="left",padx=(3,0))

        status_box=tk.Frame(self.analysis_panel,bg=GREEN,padx=12,pady=8)
        status_box.pack(fill="x",side="bottom")
        self.status=tk.Label(status_box,text="●  准备就绪",bg=GREEN,fg="#D9E5DF",
                             font=("Microsoft YaHei",9),anchor="w")
        self.status.pack(fill="x")
        utilities=tk.Frame(self.analysis_panel,bg="#E5DED2",padx=12,pady=8)
        utilities.pack(fill="x",side="bottom")
        tk.Button(utilities,text="⚙ 引擎",command=self.engine_settings,bd=0,bg="#D6CEC1",fg=TEXT,
                  font=("Microsoft YaHei",8),padx=9,pady=5).pack(side="right",padx=(4,0))
        tk.Button(utilities,text="☁ API",command=self.api_settings,bd=0,bg="#D6CEC1",fg=TEXT,
                  font=("Microsoft YaHei",8),padx=9,pady=5).pack(side="right")
        tk.Button(utilities,text="联机",command=self.lan_dialog,bd=0,bg="#D6CEC1",fg=TEXT,
                  font=("Microsoft YaHei",8),padx=9,pady=5).pack(side="right",padx=(0,4))
        self.book_badge=tk.Label(utilities,text="大师棋谱：检查中…",bg="#E5DED2",fg="#765C2B",
                                 font=("Microsoft YaHei",8,"bold"))
        self.book_badge.pack(side="left")

        self.coach_text=tk.Text(self.analysis_panel,bg="#EEE8DE",fg=TEXT,relief="flat",
                                wrap="word",font=("Microsoft YaHei",10),spacing1=3,spacing3=6,
                                padx=15,pady=15,height=18)
        self.coach_text.pack(fill="both",expand=True,padx=18,pady=(3,12))
        self.coach_text.insert("1.0","选择棋子，棋盘会显示所有合法落点。\n\n"
                             "推荐与代走先由 当前引擎 搜索；配置 API 后，"
                             "代走会让 API 从引擎候选中选择。\n\n"
                             "正式棋局与推演沙盘始终隔离。")
        self.coach_text.configure(state="disabled")

    def _mode_btn(self,parent,text,active,cmd):
        return tk.Button(parent,text=text,command=cmd,bd=0,
                         bg=GOLD if active else "#26342D",
                         fg=GREEN if active else "#BFD0C9",
                         activebackground=GOLD,activeforeground=GREEN,
                         font=("Microsoft YaHei",9,"bold"),padx=12,pady=7,cursor="hand2")

    def _action_button(self,parent,text,cmd,row,column,kind="normal"):
        colors={
            "gold":(GOLD,GREEN),
            "blue":("#2B6FA9","white"),
            "quiet":("#28312C","#B9C4BE"),
            "normal":("#36423B","#EDF1EF"),
        }
        bg,fg=colors[kind]
        b=tk.Button(parent,text=text,command=cmd,bd=0,bg=bg,fg=fg,
                    activebackground="#46554D",activeforeground="white",
                    font=("Microsoft YaHei",9,"bold"),pady=7,cursor="hand2")
        b.grid(row=row,column=column,sticky="ew",padx=2,pady=2)
        if cmd.__name__ in ("ai_take","take_over_turn"):
            self.lan_locked_buttons.append(b)
        else:
            self.lan_guidance_buttons.append(b)
        return b

    def _post_ui(self,callback):
        """Workers enqueue results; only the Tk main thread touches widgets."""
        if not self.closing:self.ui_queue.put(callback)

    def _drain_ui_queue(self):
        if self.closing:return
        for _ in range(100):
            try:callback=self.ui_queue.get_nowait()
            except queue.Empty:break
            try:callback()
            except Exception as e:
                if not self.closing:
                    self.report_callback_exception(type(e),e,e.__traceback__)
        self._refresh_player_side()
        self.ui_pump_after=self.after(30,self._drain_ui_queue)

    def _save_ui_preferences(self,event=None):
        try:
            self.ui_preferences.save(self.level.get(),self.coach.get())
        except (OSError,ValueError):
            self.status.configure(text="本次设置已生效，但未能保存")

    def _can_switch_player_side(self):
        state=self.__dict__
        return (state.get("mode")=="ai" and not state.get("closing",False)
                and not state.get("busy",False) and not state.get("lan_connecting",False)
                and state.get("editor") is None and state.get("sim") is None
                and not state.get("playback_moves")
                and state.get("endgame_window") is None
                and state.get("engine_settings_window") is None
                and state.get("follow_setup") is None)

    def _refresh_player_side(self):
        button=self.__dict__.get("player_side_button")
        if button is not None:
            try:
                button.configure(text=("JJ我执红方" if state.own_red else "JJ我执黑方") if (state:=self.__dict__.get("follow_session")) else ("我执红方" if self.human_red else "我执黑方"),
                        state="normal" if self._can_switch_player_side() else "disabled")
            except tk.TclError:
                pass

    def _toggle_player_side(self):
        if not self._can_switch_player_side():return
        self.human_red=not self.human_red
        self.restart_current_game()
        self._refresh_player_side()

    def _invalidate_api_requests(self):
        self.api_request_token+=1
        self.api_last_feedback=""
        if hasattr(self,"api_explain_btn"):
            self.api_explain_btn.configure(state="normal")

    def _set_mode(self,m):
        if self._follow_blocked():return
        if self.__dict__.get("follow_session"):self._follow_end()
        if self._editing_blocked():return
        if self.__dict__.get("lan_connecting") and self.mode!="lan":
            self._lan_shutdown()
            if self.lan_window is not None:
                self.lan_window.destroy()
                self.lan_window=None
        if self.mode=="lan":
            if not messagebox.askyesno("退出联机", "切换模式会退出当前局域网房间，是否继续？", parent=self):return
            self._lan_leave()
        self.mode=m
        self.ai_btn.configure(bg=GOLD if m=="ai" else "#26342D",
                              fg=GREEN if m=="ai" else "#BFD0C9")
        self.pvp_btn.configure(bg=GOLD if m=="pvp" else "#26342D",
                               fg=GREEN if m=="pvp" else "#BFD0C9")
        self.restart_current_game()

    def _engine_check(self):
        self.engine_check_after=None
        if self.closing:return
        self._refresh_api_badge()
        self._refresh_book_badge()
        target={"pikafish":"Pikafish","px0":"Px0"}[self.engine.preferred]
        self._set_engine_badge("正在恢复 "+target,False)
        def done(name):
            self._set_engine_badge(name+" 已就绪",True)
            self.status.configure(text="已恢复 "+name)
            notices=[self.engine.config_error,self.engine.startup_notice,self.engine.save_error]
            if any(notices):
                self._set_coach("引擎启动提示",name+" 已就绪。\n\n"+"\n\n".join(x for x in notices if x))
                self.status.configure(text=("已回退 " if self.engine.startup_notice else "已恢复 ")
                                      +name+"，详见右侧引擎启动提示")
            if self.sim is None and self._is_ai_turn():self._schedule_opponent()
        self._run_task("正在恢复并验证 "+target+"…",self.engine.restore_startup,done)

    def _refresh_book_badge(self):
        previous=self.book_refresh_after
        self.book_refresh_after=None
        if previous is not None:
            try:self.after_cancel(previous)
            except tk.TclError:pass
        if self.closing:return
        st=master_stats()
        suffix=" · 导入中" if st.get("importing") else ""
        self.book_badge.configure(
            text=f"大师棋谱：{st['games']}盘 · {st['source']}{suffix}")
        self.book_refresh_after=self.after(5000,self._refresh_book_badge)

    def _set_engine_badge(self,s,ok):
        self.engine_badge.configure(text=s,bg="#146F4B" if ok else "#8C5042")

    def _refresh_api_badge(self):
        s=self.api.settings
        if not s.get("enabled"):
            self.api_badge.configure(text="API未启用",bg="#59665F")
        elif self.api.configured():
            self.api_badge.configure(text="API代走启用",bg="#315F86")
        else:
            self.api_badge.configure(text="API待配置",bg="#A06F31")
        if hasattr(self,"ai_take_btn"):
            self.ai_take_btn.configure(text="API代走" if self.api.configured() else "AI代走")

    def engine_settings(self):
        if self.__dict__.get("follow_setup") is not None:
            self.status.configure(text="请先完成或取消投屏校准");return
        if self._editing_blocked():return
        if self.mode=="lan":
            self._set_coach("联机期间保留当前引擎", "如需更换引擎或玩家执方，请先退出局域网房间。")
            return
        old=self.__dict__.get("engine_settings_window")
        if old is not None:
            old.lift();return
        win=tk.Toplevel(self);win.title("AI 引擎设置");win.transient(self)
        self.engine_settings_window=win
        self._refresh_player_side()
        def closed(event):
            if event.widget is win and self.engine_settings_window is win:
                self.engine_settings_window=None
                self._refresh_player_side()
        win.bind("<Destroy>",closed)
        win.resizable(False,False);win.configure(bg=CARD)
        body=tk.Frame(win,bg=CARD,padx=18,pady=16);body.pack(fill="both",expand=True)
        choice=tk.StringVar(master=win,value=self.engine.name)
        side=tk.StringVar(master=win,value="红方" if self.human_red else "黑方")
        fields={}
        def label(text,row):
            tk.Label(body,text=text,bg=CARD).grid(row=row,column=0,sticky="w",pady=6)
        label("AI 引擎",0)
        selector=ttk.Combobox(body,textvariable=choice,values=("Pikafish","Px0"),state="readonly",width=22)
        selector.grid(row=0,column=1,sticky="w")
        label("玩家执方（修改后重置当前起点）",1)
        ttk.Combobox(body,textvariable=side,values=("红方","黑方"),state="disabled" if self.mode=="follow" else "readonly",width=22).grid(row=1,column=1,sticky="w")
        widgets={}
        names=[("threads","线程数"),("hash_mb","Pikafish Hash (MB)"),
               ("enginePath","Px0 程序路径"),("networkPath","Px0 网络路径"),
               ("backend","Px0 Backend（空为引擎默认）"),
               ("backendOptions","Px0 BackendOptions（可选）")]
        for row,(key,title) in enumerate(names,2):
            label(title,row)
            fields[key]=tk.StringVar(master=win)
            widgets[key]=ttk.Entry(body,textvariable=fields[key],width=48)
            widgets[key].grid(row=row,column=1,sticky="ew",padx=8)
            if key in ("enginePath","networkPath"):
                def browse(k=key):
                    path=filedialog.askopenfilename(parent=win,title="选择 Px0 程序" if k=="enginePath" else "选择 Px0 中国象棋模型")
                    if path:
                        try:path=Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
                        except ValueError:pass
                        fields[k].set(path)
                tk.Button(body,text="浏览",command=browse).grid(row=row,column=2)
        def populate(event=None):
            key=choice.get().lower()
            profile=self.engine.settings[key]
            for field,var in fields.items():
                value=profile.get(field,"")
                if field=="threads" and value is None:value=self.engine.threads
                var.set(str(value))
                enabled=field=="threads" or (field=="hash_mb" and key=="pikafish") or (field not in ("threads","hash_mb") and key=="px0")
                widgets[field].configure(state="normal" if enabled else "disabled")
        selector.bind("<<ComboboxSelected>>",populate)
        populate()
        tk.Label(body,text="相对路径从项目目录解析。Px0 使用独立模型，不能使用 pikafish.nnue。\n"
                           "Backend 必须由当前 Px0 声明支持；GPU/CPU 取决于安装包与驱动。\n"
                           "切换会取消当前分析；加载失败时保留原引擎。",bg=CARD,justify="left").grid(row=8,column=0,columnspan=3,pady=10)
        def save():
            try:
                key=choice.get().lower()
                profile={"threads":int(fields["threads"].get())}
                if key=="pikafish":profile["hash_mb"]=int(fields["hash_mb"].get())
                else:
                    profile.update({k:fields[k].get().strip() for k in ("enginePath","networkPath","backend","backendOptions")})
                human_red=side.get()=="红方"
            except Exception as e:
                messagebox.showerror("设置失败",str(e),parent=win)
                return
            win.destroy()
            self._switch_engine(key,profile,human_red)
        tk.Button(body,text="保存并应用",command=save,bd=0,bg=GREEN,fg="white",
                  font=("Microsoft YaHei",10,"bold"),pady=7).grid(row=9,column=0,columnspan=3,sticky="ew",pady=(14,0))

    def _switch_engine(self,key,profile,human_red=None):
        if self._editing_blocked():return
        if self.mode=="lan":return
        self._cancel_current_task()
        self.engine.stop()
        self._cancel_opponent_job();self._cancel_playback();self._invalidate_api_requests()
        self.analysis_snapshot=None;self.analysis_result=None;self.reco=None
        self._clear_plan_buttons()
        ticket=self.engine.ticket()
        def done(name):
            self._set_engine_badge(name+" 已就绪",True)
            self._set_coach("引擎切换完成",name+" 已完成模型加载及试算。")
            if self.mode=="follow":
                self.follow_session.proposal=None
                self._follow_request();self._follow_refresh();self.draw()
            elif human_red is not None and self.human_red!=human_red:
                self.human_red=human_red
                self.restart_current_game()
            elif self._is_ai_turn() and self.sim is None:
                self._schedule_opponent()
            if self.engine.save_error:
                self.status.configure(text=self.engine.save_error)
                self._append_coach("\n\n"+self.engine.save_error)
        self.switching_engine=True
        self._run_task("正在加载并验证 "+key+"…",
                       lambda:self.engine.switch(key,profile,ticket),done,check_position=False)

    def _is_ai_turn(self):
        follow=self.__dict__.get("follow_session")
        if self.mode=="follow":return follow is not None and follow.phase=="manual" and self.official.red==follow.own_red
        return self.mode=="ai" and self.official.red!=self.__dict__.get("human_red",True)

    def _engine_ticket(self):
        engine=self.__dict__.get("engine")
        return engine.ticket() if isinstance(engine,EngineManager) else None

    def _engine_session(self,ticket):
        return self.engine.session(ticket) if ticket is not None else nullcontext()

    def api_settings(self):
        if self._editing_blocked():return
        s=dict(self.api.settings)
        win=tk.Toplevel(self);win.title("API 接入设置");win.transient(self)
        win.geometry("610x500");win.configure(bg=CARD)
        body=tk.Frame(win,bg=CARD,padx=18,pady=16);body.pack(fill="both",expand=True)
        body.columnconfigure(1,weight=1)

        enabled=tk.BooleanVar(value=bool(s.get("enabled")))
        auto=tk.BooleanVar(value=bool(s.get("auto_explain")))
        save_key=tk.BooleanVar(value=bool(s.get("save_key")))
        endpoint=tk.StringVar(value=s.get("endpoint",""))
        model=tk.StringVar(value=s.get("model",""))
        key=tk.StringVar(value=s.get("api_key",""))
        timeout=tk.StringVar(value=str(s.get("timeout",25)))

        row=0
        tk.Checkbutton(body,text="启用 API 协作与代走",variable=enabled,bg=CARD,fg="#352F28",
                       font=("Microsoft YaHei",10,"bold")).grid(row=row,column=0,columnspan=2,sticky="w",pady=5);row+=1
        tk.Checkbutton(body,text="让 API 自动参与推荐与推演评议",variable=auto,bg=CARD,fg="#352F28",
                       font=("Microsoft YaHei",10)).grid(row=row,column=0,columnspan=2,sticky="w",pady=5);row+=1

        def field(label,var,show=None):
            nonlocal row
            tk.Label(body,text=label,bg=CARD,fg="#51483E",font=("Microsoft YaHei",10)).grid(row=row,column=0,sticky="w",pady=7)
            e=tk.Entry(body,textvariable=var,show=show or "",font=("Consolas",10))
            e.grid(row=row,column=1,sticky="ew",padx=(10,0),pady=7);row+=1
            return e

        field("完整 API URL",endpoint)
        field("模型名",model)
        field("API Key",key,"*")
        field("超时(秒)",timeout)

        tk.Checkbutton(body,text="把 API Key 明文保存到本机配置（不推荐）",
                       variable=save_key,bg=CARD,fg="#7A443B",
                       font=("Microsoft YaHei",9)).grid(row=row,column=0,columnspan=2,sticky="w",pady=6);row+=1

        info=("要求接口兼容 chat/completions：POST JSON，返回 choices[0].message.content。\\n"
              "点‘AI代走’时，API会参考当前引擎候选、评分、主变化和棋谱，"
              "并从候选白名单中优先选择；普通对手仍由当前引擎落子。\\n"
              "如果不保存 Key，可以每次启动填写，或者设置环境变量 XIANGQI_API_KEY。")
        tk.Label(body,text=info,bg="#F4F0E7",fg="#655B50",justify="left",
                 wraplength=540,padx=10,pady=10,font=("Microsoft YaHei",9)).grid(
                     row=row,column=0,columnspan=2,sticky="ew",pady=(8,12));row+=1

        btns=tk.Frame(body,bg=CARD);btns.grid(row=row,column=0,columnspan=2,sticky="ew")
        btns.columnconfigure((0,1),weight=1)

        def collect():
            try:to=int(timeout.get())
            except:to=25
            return {
                "enabled":enabled.get(),"auto_explain":auto.get(),
                "endpoint":endpoint.get().strip(),"model":model.get().strip(),
                "api_key":key.get().strip(),"save_key":save_key.get(),
                "timeout":max(3,min(120,to)),"temperature":0.2,
            }

        def save():
            self.api.save(collect());self._refresh_api_badge();win.destroy()

        def test():
            self.api.save(collect());self._refresh_api_badge()
            win.config(cursor="watch");win.update_idletasks()
            request_token=self.api_request_token
            def work():
                try:r=self.api.test();err=None
                except Exception as e:r=None;err=e
                def done():
                    if request_token!=self.api_request_token or self._editing():return
                    try:
                        if not win.winfo_exists():return
                    except tk.TclError:return
                    win.config(cursor="")
                    if err:messagebox.showerror("API测试失败",str(err),parent=win)
                    else:messagebox.showinfo("API测试",r or "连接成功",parent=win)
                self._post_ui(done)
            threading.Thread(target=work,daemon=True).start()

        tk.Button(btns,text="测试连接",command=test,bd=0,bg="#E8E2D7",fg="#4B433A",
                  font=("Microsoft YaHei",10),pady=7).grid(row=0,column=0,sticky="ew",padx=(0,4))
        tk.Button(btns,text="保存",command=save,bd=0,bg=GREEN,fg="white",
                  font=("Microsoft YaHei",10,"bold"),pady=7).grid(row=0,column=1,sticky="ew",padx=(4,0))

    def api_test(self):
        if self._editing_blocked():return
        if not self.api.configured():
            self.api_settings();return
        self.status.configure(text="正在测试 API…")
        request_token=self.api_request_token
        def work():
            try:r=self.api.test();err=None
            except Exception as e:r=None;err=e
            def done():
                if request_token!=self.api_request_token or self._editing():return
                if err:
                    self.status.configure(text="API测试失败")
                    messagebox.showerror("API测试失败",str(err))
                else:
                    self.status.configure(text="API连接正常")
                    messagebox.showinfo("API测试",r or "连接成功")
            self._post_ui(done)
        threading.Thread(target=work,daemon=True).start()

    def _api_context(self):
        if not self.analysis_result or not self.analysis_snapshot:return None
        idx=max(0,min(self.active_plan,len(self.analysis_result.candidates)-1))
        cand=self.analysis_result.candidates[idx]
        snap=self.analysis_snapshot
        pv=" ".join(cand.pv[:10])
        refs=self._master_references()
        book="；".join(
            f"{x.get('name','大师实战')} {x.get('next','')}{'（当前不可走）' if x.get('cycle_blocked') else ''}".strip()
            for x in refs[:3]
        ) or "无"
        try:local=candidate_report(snap,cand,idx+1,idx==0)
        except:local=""
        return {
            "fen":snap.fen(),
            "move":move_desc(snap,cand.move),
            "plan_label":"当前引擎首选" if idx==0 else f"对比方案{idx+1}",
            "pv":pv,
            "alternatives":"；".join(
                f"方案{i+1}:{move_desc(snap,x.move)} PV={' '.join(x.pv[:8])}"
                for i,x in enumerate(self.analysis_result.candidates[:3])
            ),
            "task_type":"推荐方案评议",
            "local_explanation":local,
            "book":book,
        }

    def _context_for_analysis(self,analysis,snap,task_type,index=0):
        if not analysis or not analysis.candidates:return None
        idx=max(0,min(index,len(analysis.candidates)-1))
        cand=analysis.candidates[idx]
        refs=self._master_references()
        book="；".join(
            f"{x.get('name','大师实战')} {x.get('next','')}{'（当前不可走）' if x.get('cycle_blocked') else ''}".strip()
            for x in refs[:3]
        ) or "无"
        try:local=candidate_report(snap,cand,idx+1,idx==0)
        except Exception:local=""
        return {
            "fen":snap.fen(),
            "move":move_desc(snap,cand.move),
            "plan_label":"当前引擎首选" if idx==0 else f"对比方案{idx+1}",
            "pv":" ".join(cand.pv[:10]),
            "alternatives":"；".join(
                f"方案{i+1}:{move_desc(snap,x.move)} PV={' '.join(x.pv[:8])}"
                for i,x in enumerate(analysis.candidates[:3])
            ),
            "task_type":task_type,
            "local_explanation":local,
            "book":book,
        }

    def api_explain_current(self):
        if not self._lan_analysis_allowed():return
        if self.busy:return
        if not self.api.configured():
            self.api_settings();return
        ctx=self._api_context()
        if not ctx:
            self._set_coach("API协作评议","请先点“推荐一步”或“扫描当前威胁”，拿到当前引擎分析后再评议。")
            return
        self._request_api_context(ctx)

    def _request_api_context(self,ctx):
        if not self._lan_analysis_allowed():return
        if not ctx or not self.api.configured():return
        self.api_request_token+=1;token=self.api_request_token
        self.status.configure(text="API正在协作评议当前引擎方案…")
        self.api_explain_btn.configure(state="disabled")
        def work():
            try:r=self.api.explain_xiangqi(ctx);err=None
            except Exception as e:r=None;err=e
            def done():
                if token!=self.api_request_token:return
                self.api_explain_btn.configure(state="normal")
                if err:
                    self.status.configure(text="API评议失败")
                    self._append_coach("\\n\\n【API评议失败】\\n"+str(err))
                else:
                    self.status.configure(text="API协作评议完成")
                    self.api_last_feedback=r
                    self._append_coach("\\n\\n【API协作评议】\\n"+r)
            self._post_ui(done)
        threading.Thread(target=work,daemon=True).start()

    def _append_coach(self,text):
        self.coach_text.configure(state="normal")
        self.coach_text.insert("end",text)
        self.coach_text.see("end")
        self.coach_text.configure(state="disabled")

    def cur(self): return self.sim if self.sim is not None else self.official

    def _moves(self): return self.sim_move_history if self.sim is not None else self.move_history

    def _cycle_context(self):
        simulated=self.sim is not None
        history=self.__dict__.get("sim_history" if simulated else "history",[])
        moves=self.__dict__.get("sim_move_history" if simulated else "move_history",[])
        prefix=self.__dict__.get("sim_cycle_base",()) if simulated else ()
        return from_history(history,moves,prefix)

    def _position_stamp(self):
        return (self.cur().fen(),tuple(self._moves()),self._cycle_context(),
                self.sim is not None,self.mode)

    def _playable_moves(self):
        if self.mode=="follow" and not self._is_ai_turn() and self.sim is None:return legal_moves(self.cur())
        return playable_moves(self.cur(),self._cycle_context())

    def _reject_cycle(self):
        self.status.configure(text=MESSAGE)
        self._set_coach("请变招",MESSAGE)

    def _analyze_position(self,snap,ms,multipv,context=(),searchmoves=None):
        """Worker-only search of a frozen position/context; no live history reads."""
        allowed=playable_moves(snap,context)
        basic=legal_moves(snap)
        if searchmoves is not None:
            requested=[searchmoves] if isinstance(searchmoves,str) else list(searchmoves)
            allowed=[m for m in allowed if m.ucci() in requested]
            if not allowed:raise ValueError(MESSAGE)
        if not basic:raise NoLegalMoves("棋局已结束：当前一方没有合法着法")
        if searchmoves is not None or len(allowed)!=len(basic):
            result=self.engine.analyze(snap.fen(),ms,multipv,
                                       searchmoves=[m.ucci() for m in allowed])
        else:
            result=self.engine.analyze(snap.fen(),ms,multipv)
        if result.best not in allowed:
            raise RuntimeError("引擎返回不可执行的着法，已拒绝落子")
        # Cached engine objects must remain untouched by history-dependent checks.
        result=deepcopy(result)
        result.candidates=[c for c in result.candidates if c.move in allowed]
        result.cycle_truncated=False
        for rank,candidate in enumerate(result.candidates,1):
            candidate.multipv=rank
            candidate.pv,truncated=trim_pv(snap,context,candidate.pv)
            result.cycle_truncated |= truncated
        return result

    def _analysis_matches(self,snap,context=None):
        return (not self._editing() and self.cur().fen()==snap.fen()
                and (context is None or self._cycle_context()==context))

    def _cycle_analysis_notice(self,analysis):
        if getattr(analysis,"cycle_truncated",False):
            self._append_coach("\n\n主变化已在双方重复往返处截断，后续路线当前不可执行。")

    def _master_references(self):
        if self.__dict__.get("game_start_fen",START_FEN)!=START_FEN:return []
        blocked=blocked_cycle_move(self.cur(),self._cycle_context())
        rows=master_lookup(self._moves())
        if blocked is None:return rows
        return [{**row,"cycle_blocked":row.get("next")==blocked.ucci()}
                for row in rows]

    def open_endgame(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        if self.mode=="lan" or self.__dict__.get("lan_connecting"):
            self._set_coach("图片导入残局","请先退出联机，再导入残局。")
            return
        win=self.__dict__.get("endgame_window")
        if win is not None and win.winfo_exists():
            win.lift();return
        from endgame_ui import EndgameWindow
        self.endgame_window=EndgameWindow(self)
        self._refresh_player_side()

    def import_position(self,pos,human_red,view):
        if self._follow_blocked():return
        if self.__dict__.get("follow_session"):self._follow_end()
        if self._editing():raise ValueError("请先应用或取消创造模式。")
        if self.mode=="lan" or self.__dict__.get("lan_connecting"):
            raise ValueError("请先退出联机，再导入残局。")
        from position_import import validate_position
        checked=validate_position(pos.b,pos.red)
        if type(human_red) is not bool or view not in BOARD_VIEWS:
            raise ValueError("玩家执方或棋盘朝向错误。")
        self.human_red=human_red
        self.board_view_mode=view
        self._reset_game_from_fen(checked.fen(),"image")

    def restart_current_game(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        if self.mode=="lan":
            self._lan_request("restart");return
        self._reset_game_from_fen(self.__dict__.get("game_start_fen",START_FEN),
                                  self.__dict__.get("game_source","standard"))

    def new_game(self):
        if self.__dict__.get("follow_setup") is not None:
            self.status.configure(text="请先完成或取消投屏校准");return
        if self.__dict__.get("follow_session"):
            if not messagebox.askyesno("结束投屏棋局", "结束本次投屏棋局并恢复标准开局？",parent=self):return
            self._follow_end()
        if self._editing_blocked():return
        if self.mode=="lan":
            self._lan_request("restart")
            return
        self._reset_game_from_fen(START_FEN,"standard")

    def _reset_game_from_fen(self,fen,source):
        if self._editing_blocked():return
        position=Position(fen)
        if self.busy:self._cancel_current_task()
        # Invalidate even a queued callback for an identical FEN/new starting point.
        self.task_token+=1
        self.engine.stop()
        self._invalidate_api_requests()
        self._cancel_opponent_job()
        self._cancel_playback()
        self.manual_takeover=False
        self.game_start_fen=position.fen();self.game_source=source
        self.official=position;self.sim=None
        follow=self.__dict__.get("follow_session")
        if follow and follow.phase=="manual":
            follow.position=position.copy();follow.context=();follow.version+=1
            follow.proposal=None;follow.last=None;follow.last_observed_own=False
        self.history=[];self.sim_history=[]
        self.move_history=[];self.sim_move_history=[]
        self.sim_cycle_base=()
        self.last=None;self.reco=None;self.selected=None;self.targets=[];self.pv_cache=[]
        self.last_user_before=None;self.last_user_move=None
        self.last_user_cycle_context=()
        self.premove_mode=False;self.premove_candidate=None
        self.analysis_snapshot=None;self.analysis_result=None;self.active_plan=0
        self._clear_plan_buttons()
        self._refresh_board_caption()
        if hasattr(self,"sim_btn"):
            self.sim_btn.configure(text="推演沙盘",bg="#36423B",fg="#EDF1EF")
        custom=source in ("image","creative")
        self._set_coach({"image":"图片残局","creative":"自定义棋局"}.get(source,"局面分析"),
            "局面已加载，作为新的对局起点。点棋子可查看合法落点。\n\n"
            "可在“残局”菜单重置当前起点或恢复标准开局。"
            "大师棋谱库按标准开局历史查询，本局面不使用该开局参考。"
            if custom else "新棋局已开始。点棋子即可查看所有合法落点。")
        self.draw();self._update_turn()
        if self.engine.installed() and not self.engine.startup_failed:
            threading.Thread(target=self._notify_engine_new_game,args=(self._engine_ticket(),),daemon=True).start()
        if self._is_ai_turn() and legal_moves(self.official):self._schedule_opponent()

    def _notify_engine_new_game(self,ticket=None):
        try:
            with self._engine_session(ticket):self.engine.new_game()
        except Exception:pass

    def save_state(self):
        if self._editing_blocked():return
        if self.sim is not None:
            self.sim_history.append((self.sim.copy(),list(self.sim_move_history),self.last))
        else:
            self.history.append((self.official.copy(),list(self.move_history),self.last))

    def make_move(self,m,auto_ai=True,actor="user"):
        if self._follow_locked() and actor!="screen":return False
        if self._editing_blocked():return False
        p=self.cur()
        basic=legal_moves(p)
        if m not in basic:return False
        context=self._cycle_context()
        external=self.mode=="follow" and self.sim is None and (actor=="screen" or (actor=="user" and not self._is_ai_turn()))
        if not external and m==blocked_cycle_move(p,context,basic):
            self._reject_cycle()
            return False
        forced_repeat=len(basic)==1 and m==cycle_move(p,context)
        if self.mode=="lan" and self.sim is None:
            if actor=="user" and self._lan_can_move():
                self._lan_send("move",move=m.ucci())
                return True  # Submitted; only the host snapshot commits it.
            return False
        self.premove_mode=False;self.premove_candidate=None
        if actor=="user":self._cancel_playback()
        self._invalidate_api_requests()
        before=p.copy()
        captured=p.b[m.tr][m.tc]
        was_takeover=(self.sim is None and self._is_ai_turn()
                      and self.manual_takeover and actor=="user")
        if actor=="user":
            self.last_user_before=p.copy()
            self.last_user_move=m
            self.last_user_cycle_context=context
        self.save_state()
        p.apply_unchecked(m)
        self._moves().append(m.ucci())
        self.last=m;self.reco=None;self.selected=None;self.targets=[]
        if actor!="screen":self._follow_after_local_move(m)
        self.analysis_snapshot=None;self.analysis_result=None;self._clear_plan_buttons()
        self.draw();self._update_turn()
        if actor=="user":
            notes=[]
            if captured:notes.append(f"吃掉对方{piece_name(captured)}")
            if in_check(p,p.red):notes.append("并形成将军")
            detail=("；".join(notes)+"。") if notes else explain_move(before,m)
            self._set_coach("落子完成",f"{move_desc(before,m)}\n\n{detail}")
        if was_takeover:
            self.manual_takeover=False
        if forced_repeat:self.status.configure(text=FORCED_MESSAGE)
        if (self.sim is None and self._is_ai_turn()
                and auto_ai and legal_moves(self.official)):
            self._schedule_opponent()
        return True

    def _cancel_opponent_job(self):
        if self.opponent_job is not None:
            try:self.after_cancel(self.opponent_job)
            except Exception:pass
            self.opponent_job=None

    def _schedule_opponent(self):
        if self.mode=="follow":
            if self.sim is None:self._follow_request()
            return
        self._cancel_opponent_job()
        if self._editing():return
        if getattr(self.__dict__.get("engine"),"startup_failed",False):return
        if self.sim is not None or not self._is_ai_turn() or self.manual_takeover:return
        if not legal_moves(self.official):return
        # Measured UI-only delay was ~270 ms before search. Yield one frame,
        # while preserving a slower retry if another task still owns the engine.
        self.opponent_job=self.after(20,self._run_scheduled_opponent)

    def _run_scheduled_opponent(self):
        self.opponent_job=None
        if self._editing():return
        if self.sim is None and self._is_ai_turn() and not self.manual_takeover:
            if self.busy:
                self.opponent_job=self.after(100,self._run_scheduled_opponent)
                return
            self.opponent_move()

    def take_over_turn(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        if self.mode=="lan":return
        if self.sim is not None:
            self._set_coach("推演模式无需接管","沙盘中红黑双方本来就都可以由你走。")
            return
        if self.mode!="ai":
            self._set_coach("当前是双人模式","双人模式中双方一直都可以人工走棋。")
            return
        if not self._is_ai_turn():
            self._set_coach("现在轮到玩家","请在 AI 回合点击“人工接管本回合”。")
            return
        self._cancel_opponent_job()
        if self.busy:self._cancel_current_task()
        self.manual_takeover=True
        self.selected=None;self.targets=[];self.reco=None
        self.status.configure(text="已接管AI 本回合")
        self._set_coach("人工接管已开启",
            "这一个 AI 回合由你落子。点 AI 方棋子，再点合法落点。\n\n"
            "落子后自动恢复人机对战，之后仍由 当前引擎控制对手。")
        self.draw();self._update_turn()

    def undo(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        if self.mode=="lan" and self.sim is None:
            self._lan_request("undo")
            return
        if self.busy:self._cancel_current_task()
        self._cancel_opponent_job()
        self._cancel_playback()
        self.manual_takeover=False
        if self.sim is not None:
            if not self.sim_history:return
            self._invalidate_api_requests()
            self.sim,self.sim_move_history,self.last=self.sim_history.pop()
        else:
            if not self.history:return
            self._invalidate_api_requests()
            # 人机时，若 AI 已经应手，默认一次撤销双方两步，直接回到玩家出手前
            steps=2 if self.mode=="ai" and not self._is_ai_turn() and len(self.history)>=2 else 1
            for _ in range(steps):
                if not self.history:break
                self.official,self.move_history,self.last=self.history.pop()
        self.reco=None;self.selected=None;self.targets=[]
        self.analysis_snapshot=None;self.analysis_result=None;self._clear_plan_buttons()
        self.draw();self._update_turn()

        follow=self.__dict__.get("follow_session")
        if follow and self.sim is None:
            follow.position=self.official.copy();follow.context=self._cycle_context();follow.version+=1
            follow.proposal=None;follow.last=self.last;follow.last_observed_own=False
            self._follow_refresh();self.draw()
        if self.sim is None and self._is_ai_turn():self._schedule_opponent()

    def _cancel_current_task(self):
        """Invalidate the UI callback and ask 当前引擎 to stop immediately."""
        if not self.busy:return
        self.task_token+=1
        self.busy=False
        self.switching_engine=False
        self.status.configure(text="正在停止当前分析…")
        self.engine.stop()
        self._refresh_player_side()

    def _escape_mode(self,event=None):
        if self._editing():
            self.cancel_creative()
            return "break"
        if self.sim is not None:
            self.toggle_sim()
            return "break"
        if self.premove_mode:
            self.toggle_premove()
            return "break"

    def toggle_sim(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        if self.sim is None and not self._lan_analysis_allowed():return
        # V5 bug: `if self.busy:return` made it impossible to leave simulation
        # while 当前引擎 was calculating. Exiting is now always allowed.
        if self.sim is not None:
            self._invalidate_api_requests()
            self._cancel_playback()
            if self.busy:
                self._cancel_current_task()
            self.sim=None
            self.sim_history=[]
            self.sim_move_history=[]
            self.sim_cycle_base=()
            self.last=None
            self.reco=None
            self.selected=None
            self.targets=[]
            self.analysis_snapshot=None
            self.analysis_result=None
            self._clear_plan_buttons()
            self._refresh_board_caption()
            self.sim_btn.configure(text="推演沙盘",bg="#36423B",fg="#EDF1EF")
            self.status.configure(text="已退出推演")
            self._set_coach("已退出推演",
                "正式棋局没有发生任何变化。\n\n"
                "刚才如果引擎还在计算，已经发送 STOP；旧结果也不会再写回界面。")
            self.draw()
            self._update_turn()
            if self._is_ai_turn():self._schedule_opponent()
            return

        if self.busy:
            return

        self._invalidate_api_requests()
        self.premove_mode=False
        self.premove_candidate=None
        self.manual_takeover=False
        self.sim_cycle_base=self._cycle_context()
        self.sim=self.official.copy()
        self.sim_move_history=list(self.move_history)
        self.sim_history=[]
        self.last=None
        self.reco=None
        self.selected=None
        self.targets=[]
        self._refresh_board_caption()
        self.sim_btn.configure(text="退出推演沙盘",bg=GOLD,fg=GREEN)
        self._set_coach("推演已开启",
            "现在可以随便试走合法着法。\n\n"
            "推演中的推荐、AI代走和“AI推演4步”默认只需约0.3～1秒，"
            "不会再按25秒一轮等待。\n\n"
            "任何时候点“退出推演模式”或按 Esc，都可以立即返回正式棋局。")
        self.draw()
        self._update_turn()

    def opponent_move(self):
        if self._editing_blocked():return
        if getattr(self.__dict__.get("engine"),"startup_failed",False):return
        self.opponent_job=None
        if self.busy or not self._is_ai_turn() or self.manual_takeover:return
        ms=LEVELS[self.level.get()]
        snap=self.official.copy()
        context=self._cycle_context()
        if not legal_moves(snap):self._update_turn();return
        self._run_task(f"当前引擎 对手思考中 · {ms/1000:.1f}秒",
            lambda:self._analyze_position(snap,ms,1,context),
            lambda a:self._apply_ai_result(a,snap,opponent=True,context=context))

    def recommend(self):
        if self._follow_blocked():return
        if not self._lan_analysis_allowed():return
        if self.busy:return
        self.premove_candidate=None
        snap=self.cur().copy()
        context=self._cycle_context()
        ms=SIM_MS[self.coach.get()] if self.sim is not None else COACH_MS[self.coach.get()]
        mode_text="推演快速分析" if self.sim is not None else "深度分析"
        self._run_task(f"当前引擎 {mode_text}中 · 约{ms/1000:.1f}秒",
            lambda:self._analyze_position(snap,ms,3,context),
            lambda a:self._show_recommendation(a,snap,context))

    def ai_take(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        if self.mode=="lan":
            self._set_coach("联机对战由玩家落子", "本模式关闭 AI 和 API 代走，请自己选择合法着法。")
            return
        if self.busy:return
        snap=self.cur().copy()
        context=self._cycle_context()
        ms=SIM_MS[self.coach.get()] if self.sim is not None else COACH_MS[self.coach.get()]
        if self.api.configured():
            refs=self._master_references()
            self._run_task("当前引擎 计算候选 → API 正在选择代走…",
                lambda:self._api_guided_take_work(snap,ms,context,refs),
                lambda result:self._apply_api_guided_take(result,snap,context))
            return
        self._run_task(f"当前引擎 正在代走 · {ms/1000:.1f}秒",
            lambda:self._analyze_position(snap,ms,1,context),
            lambda a:self._apply_ai_result(a,snap,opponent=False,context=context))

    def _api_guided_take_work(self,snap,ms,context=(),refs=None):
        analysis=self._analyze_position(snap,ms,3,context)
        candidates=[]
        allowed=[]
        for i,cand in enumerate(analysis.candidates[:3],1):
            ucci=cand.move.ucci()
            allowed.append(ucci)
            candidates.append({
                "rank":i,
                "move":ucci,
                "notation":chinese_notation(snap,cand.move),
                "description":move_desc(snap,cand.move),
                "score_cp":cand.score_cp,
                "mate":cand.mate,
                "depth":cand.depth,
                "pv":" ".join(cand.pv[:10]),
            })
        refs=refs if refs is not None else []
        context={
            "fen":snap.fen(),
            "side_to_move":"red" if snap.red else "black",
            "candidates":candidates,
            "book":"；".join(
                f"{x.get('name','大师实战')} {x.get('next','')}{'（当前不可走）' if x.get('cycle_blocked') else ''}".strip()
                for x in refs[:3]
            ) or "无精确匹配",
        }
        try:
            choice=self.api.choose_move(context,allowed)
            chosen=next(c.move for c in analysis.candidates
                        if c.move.ucci()==choice["move"])
            return {"analysis":analysis,"move":chosen,"source":"api",
                    "reason":choice.get("reason","")}
        except Exception as e:
            return {"analysis":analysis,"move":analysis.best,"source":"pikafish_fallback",
                    "reason":str(e)[:500]}

    def _apply_api_guided_take(self,result,snap,context=None):
        if not self._analysis_matches(snap,context):return
        analysis=result["analysis"]
        move=result["move"]
        if not move:return
        current=self.cur()
        if current.fen()!=snap.fen():return
        allowed={c.move for c in analysis.candidates}
        if move not in allowed or move not in self._playable_moves():
            move=analysis.best
            result={**result,"move":move,"source":"pikafish_fallback",
                    "reason":"API结果未通过本地候选与合法性双重校验"}
        source=result.get("source")
        reason=result.get("reason") or "API未附加理由"
        if not self.make_move(move,auto_ai=False,actor="ai"):return
        if source=="api":
            title="API 优先代走 · 已参考 当前引擎"
            body=(f"{move_desc(snap,move)}\n\n"
                  f"API选择理由：{reason}\n\n"
                  f"选择范围：当前引擎 的 {len(analysis.candidates)} 个合法候选；"
                  f"搜索深度 {analysis.depth}，用时 {analysis.elapsed:.1f} 秒。")
            self.status.configure(text="API参考当前引擎完成代走")
        else:
            title="当前引擎 代走 · API安全回退"
            body=(f"{move_desc(snap,move)}\n\n"
                  f"API未能给出可用候选：{reason}\n\n"
                  "已自动采用当前引擎首选，未让异常或非法着法落盘。")
            self.status.configure(text="API不可用，已回退当前引擎首选")
        self._set_coach(title,body)
        if self.sim is None and self._is_ai_turn():
            self._schedule_opponent()

    def simulate_four(self):
        if self._follow_blocked():return
        if not self._lan_analysis_allowed():return
        if self.busy:return
        self._cancel_playback()
        if self.sim is None:
            self.toggle_sim()
        snap=self.sim.copy();ms=SIM_MS[self.coach.get()]
        context=self._cycle_context()
        self._run_task(f"当前引擎 快速生成4步主变化 · {ms/1000:.1f}秒…",
            lambda:self._analyze_position(snap,ms,1,context),
            lambda a:self._apply_pv_four(a,snap,context))

    def _apply_ai_result(self,a,snap,opponent=False,context=None):
        if not self._analysis_matches(snap,context):return
        if not a.best:return
        current=self.cur()
        if current.fen()!=snap.fen():return
        if opponent and self.manual_takeover:return
        if a.best not in self._playable_moves():
            self._set_coach("已拒绝不可执行着法","引擎结果没有通过本地规则及双方往返校验。")
            return
        if not self.make_move(a.best,auto_ai=False,actor="ai"):return
        who="AI应手" if opponent else "AI代走"
        self._set_coach(who,
            f"{move_desc(snap,a.best)}\n\n"
            f"引擎深度：{a.depth}，思考：{a.elapsed:.1f} 秒\n\n"
            f"{explain_move(snap,a.best)}")
        if not opponent and self.sim is None and self._is_ai_turn():
            self._schedule_opponent()

    def _clear_plan_buttons(self):
        if not hasattr(self,"plan_buttons"):return
        for i,b in enumerate(self.plan_buttons):
            b.configure(text=f"候选 {i+1}",state="disabled",bg="#EAE5DB",fg=MUTED)

    def _set_plan_buttons(self,a,snap):
        self._clear_plan_buttons()
        for i,cand in enumerate(a.candidates[:3]):
            label=chinese_notation(snap,cand.move)
            prefix="引擎首选" if i==0 else f"候选 {i+1}"
            self.plan_buttons[i].configure(text=f"{prefix}\n{label}",state="normal")
        self._paint_plan_buttons(0)

    def _paint_plan_buttons(self,active):
        for i,b in enumerate(self.plan_buttons):
            if str(b.cget("state"))=="disabled":continue
            if i==active:
                b.configure(bg=GREEN,fg=GOLD)
            else:
                b.configure(bg="#EAE5DB",fg=TEXT)

    def select_plan(self,index):
        if self._editing_blocked():return
        if self.busy or not self.analysis_result or not self.analysis_snapshot:return
        if index<0 or index>=len(self.analysis_result.candidates):return
        if index!=self.active_plan:self._invalidate_api_requests()
        self.active_plan=index
        self._paint_plan_buttons(index)
        cand=self.analysis_result.candidates[index]
        self.reco=cand.move
        self.pv_cache=cand.pv
        snap=self.analysis_snapshot
        lines=[candidate_report(snap,cand,index+1,index==0)]
        refs=self._master_references()
        if refs:
            lines += ["","【大师实战旁证】"]
            for x in refs[:3]:
                if x.get("next"):
                    try:
                        book_move=Move.from_ucci(x["next"])
                        book_text=chinese_notation(snap,book_move) if book_move else x["next"]
                    except Exception:
                        book_text=x["next"]
                    freq=(f"，出现 {x.get('count',1)}/{x.get('total',1)}" if x.get('total',1)>1 else "")
                    note="（当前不可走：双方重复往返）" if x.get("cycle_blocked") else ""
                    lines.append(f"· {book_text}{note}{freq}｜{x['source']}")
            lines.append("棋谱只说明大师实战偏好，不改变 当前引擎 的首选排序。")
        self._set_coach("当前引擎 首选" if index==0 else f"对比方案 {index+1}","\n".join(lines))
        self._cycle_analysis_notice(self.analysis_result)
        self.draw()

    def _show_recommendation(self,a,snap,context=None):
        if not self._analysis_matches(snap,context):return
        self.api_last_feedback=""
        self.analysis_snapshot=snap.copy()
        self.analysis_result=a
        self.active_plan=0
        self._set_plan_buttons(a,snap)
        if a.candidates:
            self.select_plan(0)
        else:
            self.reco=a.best
            self._set_coach("当前引擎 首选",best_explanation(snap,a))
            self._cycle_analysis_notice(a)
            self.draw()
        if self.api.configured() and self.api.settings.get("auto_explain"):
            self._defer_api_feedback(self.api_explain_current)

    def _defer_api_feedback(self,callback):
        token=self.api_request_token
        task=self.task_token
        fen=self.cur().fen()
        def run():
            if (not self._editing() and token==self.api_request_token
                    and task==self.task_token and fen==self.cur().fen()):
                callback()
        self.after(80,run)

    def _opening_reference(self):
        refs=self._master_references()
        if not refs:
            return "当前局面没有精确匹配本地棋谱；最终选择仍以 当前引擎 为准。"
        return "；".join(x["name"] for x in refs[:3])

    def _pv_human(self,pos,pv,n=8):
        out=[];p=pos.copy()
        for i,u in enumerate(pv[:n],1):
            m=Move.from_ucci(u)
            if not m:break
            side="红" if p.red else "黑"
            out.append(f"{i}. {side}：{move_desc(p,m)}")
            if m in legal_moves(p):p.apply_unchecked(m)
            else:break
        return out or ["（本次没有返回足够的主变化）"]

    def toggle_premove(self):
        if self._follow_blocked():return
        if not self._lan_analysis_allowed():return
        if self.busy:return
        self.premove_mode=not self.premove_mode
        self.premove_candidate=None
        self.selected=None;self.targets=[];self.reco=None
        self._refresh_board_caption()
        if self.premove_mode:
            self._set_coach("预走分析",
                "现在像正常下棋一样点一枚棋，再点一个合法落点。\n\n"
                "这一步不会落到正式棋盘，当前引擎 会比较：\n"
                "1. 当前真正首选\n2. 你想试的这一步\n3. 对方最强回应")
        else:
            self.reco=None;self.premove_candidate=None
            self._set_coach("预走分析已关闭","恢复正常落子。")
        self.draw()

    def analyze_premove(self,m):
        if not self._lan_analysis_allowed():return
        if self.busy:return
        if m not in self._playable_moves():
            self._reject_cycle()
            return
        self._invalidate_api_requests()
        before=self.cur().copy()
        context=self._cycle_context()
        stamp=self._position_stamp()
        ms=COACH_MS[self.coach.get()]
        forced_ms=FORCED_MS[self.coach.get()]
        self.premove_candidate=m;self.reco=None;self.draw()
        self.busy=True;self.task_token+=1;token=self.task_token
        self.status.configure(text="当前引擎 正在快速预演…")
        ticket=self._engine_ticket()
        def work():
            try:
                with self._engine_session(ticket):
                    best=self._analyze_position(before,ms,1,context)
                    if best.best==m:
                        forced=best
                    else:
                        forced=self._analyze_position(before,forced_ms,1,context,searchmoves=[m.ucci()])
                err=None
            except Exception as e:
                best=forced=None;err=e
            def finish():
                if token!=self.task_token:return
                self.busy=False
                if stamp!=self._position_stamp():return
                if err:
                    self._set_coach("预走分析失败",str(err));self.status.configure(text="引擎异常");return
                self._set_coach("预走分析",premove_report(before,m,best,forced,None))
                self._cycle_analysis_notice(forced)
                self.reco=best.best;self.premove_candidate=m;self.draw()
                self.status.configure(text="预走分析完成 · 棋盘未改变")
            self._post_ui(finish)
        threading.Thread(target=work,daemon=True).start()

    def scan_threats(self):
        if not self._lan_analysis_allowed():return
        if self.busy:return
        snap=self.cur().copy()
        context=self._cycle_context()
        ms=COACH_MS[self.coach.get()]
        self._run_task(f"当前引擎 快速扫描威胁 · 约{ms/1000:.1f}秒…",
            lambda:self._analyze_position(snap,ms,3,context),
            lambda a:self._show_threat_scan(a,snap,context))

    def _show_threat_scan(self,a,snap,context=None):
        if not self._analysis_matches(snap,context):return
        self.analysis_snapshot=snap.copy();self.analysis_result=a;self.active_plan=0
        self._set_plan_buttons(a,snap)
        self.reco=a.best
        self.premove_candidate=None
        self._set_coach("当前威胁扫描",threat_report(snap,a)+"\n\n上方可以切换方案1/2/3查看不同强着。")
        self._cycle_analysis_notice(a)
        self.draw()

    def analyze_my_move(self):
        if not self._lan_analysis_allowed():return
        if self.busy:return
        if self.last_user_before is None or self.last_user_move is None:
            self._set_coach("暂无可分析着法","请先自己走一步，再点“分析我刚才这步”。")
            return
        self._invalidate_api_requests()
        before=self.last_user_before.copy();user_move=self.last_user_move
        context=self.__dict__.get("last_user_cycle_context",())
        stamp=self._position_stamp()
        ms=COACH_MS[self.coach.get()];forced_ms=FORCED_MS[self.coach.get()]
        self.busy=True;self.task_token+=1;token=self.task_token
        self.status.configure(text="当前引擎 正在快速复盘…")
        ticket=self._engine_ticket()
        def work():
            try:
                with self._engine_session(ticket):
                    best=self._analyze_position(before,ms,1,context)
                    if best.best==user_move:
                        forced=best
                    else:
                        forced=self._analyze_position(before,forced_ms,1,context,searchmoves=[user_move.ucci()])
                err=None
            except Exception as e:
                best=forced=None;err=e
            def finish():
                if token!=self.task_token:return
                self.busy=False
                if stamp!=self._position_stamp():return
                if err:
                    self._set_coach("复盘失败",str(err));self.status.configure(text="引擎异常");return
                self._set_coach("复盘：你刚才这一步",user_move_report(before,user_move,best,forced,None))
                self._cycle_analysis_notice(forced)
                self.reco=best.best if best.best!=user_move else user_move
                self.draw();self.status.configure(text="复盘完成")
            self._post_ui(finish)
        threading.Thread(target=work,daemon=True).start()

    def _apply_pv_four(self,a,snap,context=None):
        if not self._analysis_matches(snap,context):return
        self.api_last_feedback=""
        pv=a.candidates[0].pv if a.candidates else []
        pv,truncated=trim_pv(snap,self._cycle_context(),pv)
        probe=snap.copy();moves=[]
        for u in pv[:4]:
            m=Move.from_ucci(u)
            if not m or m not in legal_moves(probe):break
            moves.append(m)
            probe.apply_unchecked(m)
        self.analysis_snapshot=snap.copy();self.analysis_result=a;self.active_plan=0
        self.playback_moves=moves
        self.playback_index=0
        self.playback_paused=False
        self.playback_explanations=[]
        self.reco=None;self.selected=None;self.targets=[]
        self._update_playback_controls()
        if not moves:
            self._set_coach("推演没有可播放着法","当前引擎 没有返回可在当前局面执行的主变化。")
            return
        self._set_coach("四步推演已生成",
            "不会再瞬间跳到第4步。现在将逐步播放；可随时暂停、上一步或下一步。")
        if truncated or getattr(a,"cycle_truncated",False):
            self._append_coach("\n\n主变化已在双方重复往返处截断。")
        self.status.configure(text=f"推演准备播放 · 共 {len(moves)} 步")
        self.playback_after=self.after(500,self._advance_playback)
        if self.api.configured() and self.api.settings.get("auto_explain"):
            ctx=self._context_for_analysis(a,snap,"四步推演评议")
            self._defer_api_feedback(lambda:self._request_api_context(ctx))

    def _cancel_playback_timer(self):
        if self.playback_after is not None:
            try:self.after_cancel(self.playback_after)
            except Exception:pass
            self.playback_after=None

    def _cancel_playback(self):
        self._cancel_playback_timer()
        self.playback_moves=[]
        self.playback_index=0
        self.playback_paused=False
        self.playback_explanations=[]
        if hasattr(self,"playback_pause"):
            self._update_playback_controls()

    def _update_playback_controls(self):
        active=bool(self.sim is not None and self.playback_moves)
        self.playback_back.configure(state="normal" if active and self.playback_index>0 else "disabled")
        self.playback_next.configure(
            state="normal" if active and self.playback_index<len(self.playback_moves) else "disabled")
        self.playback_pause.configure(
            state="normal" if active and self.playback_index<len(self.playback_moves) else "disabled",
            text="继续" if self.playback_paused else "暂停")

    def _advance_playback(self):
        if self._editing():return
        self.playback_after=None
        if self.sim is None or self.playback_paused:return
        if self.playback_index>=len(self.playback_moves):return
        m=self.playback_moves[self.playback_index]
        if m==blocked_cycle_move(self.sim,self._cycle_context()):
            self._set_coach("推演播放已停止",MESSAGE)
            self._cancel_playback()
            return
        if m not in legal_moves(self.sim):
            self._set_coach("推演播放已停止","当前沙盘局面已经变化，原主变化不再适用。")
            self._cancel_playback()
            return
        before=self.sim.copy()
        self.save_state()
        self.sim.apply_unchecked(m);self.sim_move_history.append(m.ucci())
        self.last=m;self.playback_index+=1
        self.playback_explanations.append(
            f"{self.playback_index}. {'红' if before.red else '黑'} {move_desc(before,m)}\n"
            f"   {explain_move(before,m)}")
        self.reco=(self.playback_moves[self.playback_index]
                   if self.playback_index<len(self.playback_moves) else None)
        self.selected=None;self.targets=[]
        self.draw();self._update_turn();self._update_playback_controls()
        body="蓝色箭头是下一步。\n\n"+"\n\n".join(self.playback_explanations)
        if self.api_last_feedback:
            body+="\n\n【API协作评议】\n"+self.api_last_feedback
        self._set_coach(
            f"推演进度 {self.playback_index}/{len(self.playback_moves)}",
            body)
        if self.playback_index<len(self.playback_moves):
            self.status.configure(text="推演播放中 · 可暂停或单步查看")
            self.playback_after=self.after(1100,self._advance_playback)
        else:
            self.status.configure(text="四步推演播放完成")

    def toggle_playback_pause(self):
        if self._editing_blocked():return
        if not self.playback_moves or self.playback_index>=len(self.playback_moves):return
        self.playback_paused=not self.playback_paused
        if self.playback_paused:
            self._cancel_playback_timer()
            self.status.configure(text="推演已暂停")
        else:
            self.status.configure(text="推演继续播放")
            self.playback_after=self.after(150,self._advance_playback)
        self._update_playback_controls()

    def playback_step_next(self):
        if self._editing_blocked():return
        if not self.playback_moves:return
        self._cancel_playback_timer()
        self.playback_paused=False
        self._advance_playback()
        self._cancel_playback_timer()
        self.playback_paused=True
        self._update_playback_controls()
        if self.playback_index<len(self.playback_moves):self.status.configure(text="推演已暂停 · 单步查看")

    def playback_step_back(self):
        if self._editing_blocked():return
        if self.sim is None or self.playback_index<=0 or not self.sim_history:return
        self._cancel_playback_timer();self.playback_paused=True
        self.sim,self.sim_move_history,self.last=self.sim_history.pop()
        self.playback_index-=1
        if self.playback_explanations:self.playback_explanations.pop()
        self.reco=self.playback_moves[self.playback_index]
        self.selected=None;self.targets=[]
        self.draw();self._update_turn();self._update_playback_controls()
        self._set_coach(
            f"推演进度 {self.playback_index}/{len(self.playback_moves)}",
            ("已退回上一步。蓝色箭头是下一步。\n\n"+
             "\n\n".join(self.playback_explanations)).rstrip())
        self.status.configure(text="推演已暂停 · 可继续或单步查看")

    def _run_task(self,label,fn,done,quiet_error=False,check_position=True):
        if self._editing_blocked():return
        if self.busy:return
        self._invalidate_api_requests()
        self.busy=True;self.task_token+=1;token=self.task_token
        stamp=self._position_stamp()
        self._refresh_player_side()
        self.status.configure(text=label)
        ticket=self._engine_ticket()
        def work():
            try:
                with self._engine_session(ticket):r=fn()
                err=None
            except Exception as e:r=None;err=e
            def finish():
                if token!=self.task_token:return
                self.busy=False
                if not check_position:self.switching_engine=False
                self._refresh_player_side()
                if check_position and stamp!=self._position_stamp():return
                if err:
                    if not check_position and self.mode=="follow":self.after(20,self._follow_request)
                    if getattr(self.engine,"startup_failed",False):
                        self.status.configure(text="离线引擎不可用，请检查引擎设置")
                        self._set_engine_badge("离线引擎不可用",False)
                        self._set_coach("需要检查离线引擎",str(err))
                        return
                    if isinstance(err,(NoLegalMoves,SearchCancelled,UnsupportedFeature)):
                        self.status.configure(text=str(err))
                        if not quiet_error:self._set_coach("引擎提示",str(err))
                        return
                    self.status.configure(text="本次引擎操作失败")
                    self._set_coach("引擎操作失败",
                        f"{err}\n\n当前保留 {self.engine.name}，可重试或打开“引擎”设置切换。\n"
                        "Px0 需要独立程序和配套中国象棋模型。")
                    self._set_engine_badge(self.engine.name+" · 可重试",False)
                else:
                    self.status.configure(text="分析完成")
                    if done:done(r)
            self._post_ui(finish)
        threading.Thread(target=work,daemon=True).start()

    def _set_coach(self,title,body):
        self.coach_title.configure(text=title)
        self.coach_text.configure(state="normal")
        self.coach_text.delete("1.0","end")
        self.coach_text.insert("1.0",body)
        self.coach_text.configure(state="disabled")

    def _update_turn(self):
        self._refresh_player_side()
        if self.__dict__.get("follow_session") is not None and self.sim is None:
            self._refresh_board_caption()
        if self._editing():
            self.turn_label.configure(text="创造模式",fg="#FFD36A")
            return
        if self.mode=="lan" and self.sim is None and self.lan_state:
            state=self.lan_state
            text=state["result"] or ("等待对方连接" if not state["connected"] else
                    "等待协商答复" if state["pending"] else
                    "轮到你行棋" if self.official.red==self.human_red else "等待对方行棋")
            self.turn_label.configure(text=text,fg="#FFDDDA" if self.human_red else "white")
            return
        p=self.cur()
        side="红方" if p.red else "黑方"
        moves=self._playable_moves()
        checked=in_check(p,p.red)
        if not moves:
            text=(f"■ {side}被将死" if checked else f"■ {side}无合法着法")
            color="#FFD36A"
        elif checked:
            text=f"⚠ {side}被将军 · {len(moves)} 种解法"
            color="#FFD36A"
        elif self.manual_takeover and self.sim is None:
            text=f"☝ {side}行棋 · 人工接管"
            color="#FFD36A"
        else:
            text=f"● {side}行棋"
            color="#FFDDDA" if p.red else "white"
        self.turn_label.configure(text=text,fg=color)

    def on_board_click(self,e):
        if self._follow_blocked():return
        if self._editing():
            self._editor_click(e)
            return
        if self.busy:return
        if self.mode=="lan" and self.sim is None and not self._lan_can_move():
            self._set_coach("暂时不能落子", "请等待对方连接、完成协商或轮到你行棋。")
            return
        if self.playback_moves and not self.playback_paused:
            self.toggle_playback_pause()
        if self.sim is None and self._is_ai_turn() and not self.manual_takeover:
            self._set_coach("等待 当前引擎 落子",
                "现在是 AI 回合。若想替 AI 走这一步，请点左侧“人工接管本回合”。")
            return
        geo=self._geo()
        if not geo:return
        ox,oy,dx,dy=geo
        c=round((e.x-ox)/dx);r=round((e.y-oy)/dy)
        if not (0<=r<10 and 0<=c<9):return
        r,c=self._view_to_board(r,c)
        p=self.cur()
        moves=self._playable_moves()
        if not moves:
            self._set_coach("棋局已经结束",
                "当前一方没有合法着法。"+("这是将死局面。" if in_check(p,p.red) else "这是无子可动局面。"))
            return
        if self.selected:
            if (self.mode!="follow" and Move(self.selected[0],self.selected[1],r,c)==blocked_cycle_move(p,self._cycle_context())):
                self._reject_cycle()
                return
            for m in self.targets:
                if m.tr==r and m.tc==c:
                    if self.premove_mode:
                        self.selected=None;self.targets=[]
                        self.analyze_premove(m)
                    else:
                        self.make_move(m)
                    return
        q=p.b[r][c]
        if q and Position.is_red(q)==p.red:
            self.selected=(r,c);self.targets=[m for m in moves if (m.fr,m.fc)==(r,c)];self.reco=None
            if not self.premove_mode:self.premove_candidate=None
            options=[]
            for mm in self.targets[:12]:
                options.append("· "+chinese_notation(p,mm))
            more="" if len(self.targets)<=12 else f"\n……另外还有 {len(self.targets)-12} 个"
            if in_check(p,p.red) and not self.targets:
                self._set_coach(f"这枚{piece_name(q)}不能解将",
                    "当前正在被将军，只能选择能解除将军的棋子。\n\n"
                    "棋盘上带金色外圈的己方棋子可以处理当前将军。")
            else:
                prefix="当前正在被将军；以下落点都能解除将军。\n\n" if in_check(p,p.red) else ""
                self._set_coach(f"已选中 {piece_name(q)}",
                    prefix+f"棋盘已经标出 {len(self.targets)} 个合法落点。\n"
                    "绿色圆点可以直接走；红圈表示可以吃子。\n\n"
                    "当前可走：\n" + "\n".join(options) + more)
        else:
            self.selected=None;self.targets=[]
        self.draw()

    def _view_flipped(self):
        choice=self.__dict__.get("board_view_mode","auto")
        if choice=="red_bottom":return False
        if choice=="black_bottom":return True
        follow=self.__dict__.get("follow_session")
        if follow is not None:return not follow.own_red
        return (self.__dict__.get("mode") in ("ai","lan")
                and self.__dict__.get("human_red",True))

    def _view_name(self):
        return "黑方在下" if self._view_flipped() else "红方在下"

    def _flip_board(self):
        self._set_board_view_mode("red_bottom" if self._view_flipped() else "black_bottom")

    def _set_board_view_mode(self,choice):
        if choice not in BOARD_VIEWS:raise ValueError("未知棋盘朝向")
        self.board_view_mode=choice
        if self._editing():self.editor.selected=None
        self.selected=None;self.targets=[]
        self._refresh_board_caption()
        self.draw()

    def _refresh_board_caption(self):
        self._refresh_player_side()
        if "board_view_var" in self.__dict__:
            self.board_view_var.set(self._view_name())
        if "sim_label" not in self.__dict__:return
        if self.__dict__.get("follow_session") is not None and self.sim is None and not self._editing():
            text=self._follow_caption()
        elif self._editing():
            text=f"创造模式 · {self._view_name()} · 自由摆子 · Esc 取消"
        elif self.__dict__.get("premove_mode"):
            text=f"预走分析 · {self._view_name()} · 只分析，不真正落子"
        elif self.__dict__.get("sim") is not None:
            text=f"推演沙盘 · {self._view_name()} · 不影响正式棋局 · Esc 退出"
        elif self.__dict__.get("mode")=="lan" and self.__dict__.get("lan_state"):
            side="红方" if self.human_red else "黑方"
            guidance="允许指导" if self.lan_state["allow_guidance"] else "关闭指导"
            text=f"局域网 · 你执{side} · {guidance} · {self._view_name()}"
        else:
            source={"image":"图片残局","creative":"自定义棋局"}.get(self.__dict__.get("game_source"),"正式棋局")
            text=f"{source}  ·  {self._view_name()}"
        self.sim_label.configure(text=text)

    def _board_to_view(self,r,c):
        return (9-r,8-c) if self._view_flipped() else (r,c)

    def _view_to_board(self,r,c):
        return self._board_to_view(r,c)

    def _board_to_canvas(self,r,c,geo):
        row,col=self._board_to_view(r,c)
        ox,oy,dx,dy=geo
        return ox+col*dx,oy+row*dy

    def _geo(self):
        w=max(1,self.canvas.winfo_width());h=max(1,self.canvas.winfo_height())
        margin=max(18,min(w,h)*.06)
        unit=max(1,min((w-2*margin)/8,(h-2*margin)/9))
        return (w-unit*8)/2,(h-unit*9)/2,unit,unit

    def _resize_board(self): pass

    def draw(self):
        c=self.canvas;c.delete("all")
        p=self.cur();w=c.winfo_width();h=c.winfo_height()
        follow=self.__dict__.get("follow_session") if self.sim is None else None
        display_last=self.last
        if follow and follow.proposal:
            p=follow.displayed();display_last=follow.proposal.move
        ring_color="#000000" if follow and follow.last_observed_own and not follow.proposal else "#FFFFFF"
        editing=self._editing()
        board=self.editor.board if editing else p.b
        if w<100 or h<100:return
        geo=self._geo()
        ox,oy,dx,dy=geo
        X=lambda col:ox+col*dx;Y=lambda row:oy+row*dy

        # Keep square cells at every window size and paint a board surface only
        # behind the playable area.  The previous canvas stretched the board.
        edge=dx*.48
        c.create_rectangle(X(0)-edge+4,Y(0)-edge+6,X(8)+edge+4,Y(9)+edge+6,
                           fill="#090E0B",outline="")
        c.create_rectangle(X(0)-edge,Y(0)-edge,X(8)+edge,Y(9)+edge,
                           fill=WOOD,outline=WOOD_DARK,width=5)
        for r in range(10):
            if r%2:
                c.create_rectangle(X(0)-edge+3,Y(r)-dy*.5,X(8)+edge-3,Y(r)+dy*.5,
                                   fill="#D9A159",outline="")

        # grid
        for r in range(10):c.create_line(X(0),Y(r),X(8),Y(r),fill=LINE,width=2)
        for col in range(9):
            c.create_line(X(col),Y(0),X(col),Y(4),fill=LINE,width=2)
            c.create_line(X(col),Y(5),X(col),Y(9),fill=LINE,width=2)
        c.create_line(X(0),Y(4),X(0),Y(5),fill=LINE,width=2)
        c.create_line(X(8),Y(4),X(8),Y(5),fill=LINE,width=2)
        c.create_line(X(3),Y(0),X(5),Y(2),fill=LINE,width=2)
        c.create_line(X(5),Y(0),X(3),Y(2),fill=LINE,width=2)
        c.create_line(X(3),Y(7),X(5),Y(9),fill=LINE,width=2)
        c.create_line(X(5),Y(7),X(3),Y(9),fill=LINE,width=2)
        c.create_text((X(2)+X(3))/2,(Y(4)+Y(5))/2,text="楚 河",
                      fill="#855024",font=("SimSun",-max(10,round(dx*.6)),"bold"))
        c.create_text((X(5)+X(6))/2,(Y(4)+Y(5))/2,text="汉 界",
                      fill="#855024",font=("SimSun",-max(10,round(dx*.6)),"bold"))

        # restrained point markers make the board easier to scan without adding clutter
        dot=max(2,min(dx,dy)*.035)
        for r,col in ((2,1),(2,7),(3,0),(3,2),(3,4),(3,6),(3,8),
                      (6,0),(6,2),(6,4),(6,6),(6,8),(7,1),(7,7)):
            c.create_oval(X(col)-dot,Y(r)-dot,X(col)+dot,Y(r)+dot,
                          fill=LINE,outline="")

        # last
        if display_last and not editing:
            for r,col in ((display_last.fr,display_last.fc),(display_last.tr,display_last.tc)):
                rr=min(dx,dy)*.23
                x,y=self._board_to_canvas(r,col,geo)
                c.create_rectangle(x-rr,y-rr,x+rr,y+rr,
                                   outline="#39785F",width=2,tags=("last",))

        # legal targets
        for m in ([] if editing else self.targets):
            capture=bool(p.b[m.tr][m.tc])
            rad=min(dx,dy)*(.14 if capture else .10)
            x,y=self._board_to_canvas(m.tr,m.tc,geo)
            if capture:
                c.create_oval(x-rad,y-rad,x+rad,y+rad,
                              outline=RED,width=3,tags=("target","capture"))
            else:
                c.create_oval(x-rad,y-rad,x+rad,y+rad,
                              fill=TARGET,outline="",tags=("target",))

        # pre-move candidate: amber arrow, does not change the board
        if self.premove_candidate and not editing:
            m=self.premove_candidate
            x1,y1=self._board_to_canvas(m.fr,m.fc,geo)
            x2,y2=self._board_to_canvas(m.tr,m.tc,geo)
            amber="#C78324"
            c.create_line(x1+2,y1+3,x2+2,y2+3,fill="#74491E",width=7,
                          arrow=tk.LAST,arrowshape=(15,18,7))
            c.create_line(x1,y1,x2,y2,fill=amber,width=5,arrow=tk.LAST,arrowshape=(15,18,7),tags=("premove",))
            rr=min(dx,dy)*.25
            c.create_oval(x1-rr,y1-rr,x1+rr,y1+rr,outline=amber,width=3)
            c.create_oval(x2-rr,y2-rr,x2+rr,y2+rr,outline=amber,width=3)
            c.create_text((x1+x2)/2,(y1+y2)/2-12,text="你想试",
                          fill="#8A591B",font=("Microsoft YaHei",9,"bold"))

        # 当前引擎 recommendation: blue -> green
        if self.reco and not editing:
            x1,y1=self._board_to_canvas(self.reco.fr,self.reco.fc,geo)
            x2,y2=self._board_to_canvas(self.reco.tr,self.reco.tc,geo)
            c.create_line(x1+2,y1+3,x2+2,y2+3,fill="#244D71",width=8,
                          arrow=tk.LAST,arrowshape=(16,20,7))
            c.create_line(x1,y1,x2,y2,fill=BLUE,width=6,arrow=tk.LAST,arrowshape=(16,20,7),tags=("recommendation",))
            rr=min(dx,dy)*.28
            c.create_oval(x1-rr,y1-rr,x1+rr,y1+rr,outline=BLUE,width=3)
            c.create_oval(x2-rr,y2-rr,x2+rr,y2+rr,outline="#179F6B",width=3)
            if self.premove_candidate and self.reco != self.premove_candidate:
                c.create_text((x1+x2)/2,(y1+y2)/2+14,text="AI首选",
                              fill=BLUE,font=("Microsoft YaHei",9,"bold"))

        # pieces
        checked=not editing and in_check(p,p.red)
        legal_sources={(m.fr,m.fc) for m in (legal_moves(p) if follow and follow.proposal else self._playable_moves())} if checked else set()
        radius=min(dx,dy)*.36
        for r in range(10):
            for col in range(9):
                q=board[r][col]
                if not q:continue
                x,y=self._board_to_canvas(r,col,geo)
                c.create_oval(x-radius+3,y-radius+5,x+radius+3,y+radius+5,
                              fill="#744923",outline="")
                c.create_oval(x-radius,y-radius,x+radius,y+radius,
                              fill="#F7E1AD",outline=RED if q.isupper() else BLACK,width=3)
                inner=radius*.82
                c.create_oval(x-inner,y-inner,x+inner,y+inner,
                              outline="#C69655",width=1)
                if (not editing and display_last is not None
                        and (r,col)==(display_last.tr,display_last.tc)):
                    c.create_oval(x-radius-4,y-radius-4,x+radius+4,y+radius+4,
                                  outline=ring_color,width=4,fill="",
                                  tags=("last_move_ring",))
                if checked and q.isupper()==p.red and (r,col) in legal_sources:
                    c.create_oval(x-radius-4,y-radius-4,x+radius+4,y+radius+4,
                                  outline=GOLD,width=4,tags=("check_escape",))
                if (self.editor.selected if editing else self.selected)==(r,col):
                    c.create_oval(x-radius-4,y-radius-4,x+radius+4,y+radius+4,
                                  outline="#FFF0A8",width=4,tags=("selected",))
                c.create_text(x,y,text=piece_name(q),
                              fill=RED if q.isupper() else BLACK,
                              # Canvas geometry is pixels; negative font sizes
                              # prevent high-DPI point scaling from overflowing pieces.
                              font=("SimSun",-max(9,int(radius*1.4)),"bold"),tags=("piece",f"piece:{r}:{col}"))

    def _close(self):
        if self.closing:return
        self.closing=True
        self._follow_close()
        win=self.__dict__.get("endgame_window")
        if win is not None:win.close()
        self._lan_shutdown()
        self._cancel_opponent_job()
        self._cancel_playback()
        self.api_request_token+=1
        self.task_token+=1
        for name in ("engine_check_after","book_refresh_after"):
            job=self.__dict__.get(name)
            if job is not None:
                try:self.after_cancel(job)
                except tk.TclError:pass
                setattr(self,name,None)
        if self.ui_pump_after is not None:
            try:self.after_cancel(self.ui_pump_after)
            except Exception:pass
        self.engine.stop()
        threading.Thread(target=self.engine.close,daemon=False).start()
        self.destroy()

if __name__=="__main__":
    App().mainloop()
