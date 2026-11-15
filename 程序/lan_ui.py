"""Small Tk integration layer; network workers never touch Tk widgets."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from lan_network import interfaces, ensure_firewall
from lan_protocol import PORT
from lan_server import LanServer
from lan_client import LanClient
from rules import Position, Move, START_FEN


class LanUI:
    def _init_lan(self):
        self.lan_session = None
        self.lan_state = None
        self.lan_generation = 0
        self.lan_connecting = False
        self.lan_sending = False
        self.lan_role = "host"
        self.lan_buffer = []
        self.lan_prompted = set()
        self.lan_window = None
        self.lan_guidance_buttons = []
        self.lan_locked_buttons = []

    def lan_dialog(self):
        if self._follow_blocked():return
        if self._editing_blocked():return
        old = self.lan_window
        if old is not None:
            try:
                if old.winfo_exists():
                    old.lift()
                    return
            except tk.TclError:
                pass
        win = tk.Toplevel(self)
        self.lan_window = win
        win.title("局域网双人对战")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=20)
        body.pack(fill="both", expand=True)
        if self.lan_session:
            self._lan_room_controls(win, body)
            return
        selected = tk.StringVar(master=win)
        host = tk.StringVar(master=win)
        code = tk.StringVar(master=win)
        port = tk.StringVar(master=win, value=str(PORT))
        side = tk.StringVar(master=win, value="红方")
        guidance = tk.BooleanVar(master=win, value=False)
        status = tk.StringVar(master=win, value="正在读取本地网卡…")
        options = {}
        ttk.Label(body, text="本地网卡").grid(row=0, column=0, sticky="w", pady=5)
        combo = ttk.Combobox(body, textvariable=selected, state="readonly", width=58)
        combo.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(body, text="端口").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=port).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Separator(body).grid(row=2, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(body, text="房主执方").grid(row=3, column=0, sticky="w")
        ttk.Combobox(body, textvariable=side, values=("红方", "黑方"), state="readonly").grid(row=3, column=1, sticky="w")
        ttk.Checkbutton(body, text="允许双方使用推荐、棋谱及推演指导", variable=guidance).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)

        def start(create):
            if self.lan_connecting:
                return
            try:
                interface = options[selected.get()]
                interface.validate()
                number = int(port.get())
                if not 1024 <= number <= 65535:
                    raise ValueError("端口范围为 1024–65535")
                params = (interface, number, side.get() == "红方", guidance.get(), host.get().strip(), code.get().strip())
            except Exception as exc:
                status.set(str(exc) if not isinstance(exc, KeyError) else "请选择本地网卡")
                return
            self.lan_connecting = True
            self.lan_generation += 1
            generation = self.lan_generation
            self.lan_buffer = []
            create_btn.configure(state="disabled")
            join_btn.configure(state="disabled")
            status.set("正在创建房间；首次配置防火墙可能出现 Windows 管理员确认…" if create else "正在连接房主…")
            self._lan_open_worker(create, params, generation, win, status, (create_btn, join_btn))

        create_btn = ttk.Button(body, text="创建房间", command=lambda: start(True))
        create_btn.grid(row=5, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Separator(body).grid(row=6, column=0, columnspan=2, sticky="ew", pady=10)
        for row, label, var in ((7, "房主 IPv4", host), (8, "房间码", code)):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(body, textvariable=var, width=50).grid(row=row, column=1, sticky="ew", pady=5)
        join_btn = ttk.Button(body, text="加入房间", command=lambda: start(False))
        join_btn.grid(row=9, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(body, textvariable=status, wraplength=550).grid(row=10, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(body, text="仅支持同一私有 IPv4 子网的有线 / Wi-Fi 物理网卡。\n"
                            "双方网络须为 Windows“专用”。创建时只放行本机指定网卡、端口和子网。\n"
                            "不使用端口映射或内网穿透。指导开关是双方约定，不能阻止外部软件辅助。",
                  wraplength=550).grid(row=11, column=0, columnspan=2, sticky="w")

        def refresh():
            if self.lan_connecting:
                return
            status.set("正在读取网卡…")
            def work():
                try:
                    found, error = interfaces(), ""
                except Exception as exc:
                    found, error = [], str(exc)
                def done():
                    if not win.winfo_exists():
                        return
                    options.clear()
                    for item in found:
                        label = f"{item.name}  {item.ip}/{item.prefix}  ({item.category})"
                        options[label] = item
                    combo.configure(values=list(options))
                    if options:
                        selected.set(next((k for k, v in options.items() if v.category == "Private"), next(iter(options))))
                    status.set(error or ("请选择网卡后创建或加入房间。" if options else "没有找到可用的私有 IPv4 物理网卡"))
                self._post_ui(done)
            threading.Thread(target=work, daemon=True).start()
        ttk.Button(body, text="刷新网卡", command=refresh).grid(row=12, column=0, columnspan=2, sticky="e", pady=8)
        refresh()
        def cancel():
            self.lan_generation += 1
            self.lan_connecting = False
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", cancel)

    def _lan_open_worker(self, create, params, generation, win, status, buttons):
        interface, port, host_red, guidance, host, code = params
        def event(value):
            self._post_ui(lambda: self._lan_event(value, generation))
        def work():
            session = None
            error = ""
            try:
                if create:
                    ensure_firewall(interface, port)
                    if generation != self.lan_generation or self.closing:
                        return
                    session = LanServer(interface, host_red, guidance, port, event)
                    session.start()
                else:
                    session = LanClient(interface, host, code, port, event)
                    session.connect()
            except Exception as exc:
                error = str(exc)
                if session:
                    session.close()
                session = None
            if generation != self.lan_generation or self.closing:
                if session:
                    session.close()
                return
            def done():
                if generation != self.lan_generation or self.closing:
                    if session:
                        threading.Thread(target=session.close, daemon=True).start()
                    return
                self.lan_connecting = False
                if error:
                    self.lan_buffer = []
                    if win.winfo_exists():
                        status.set("连接未建立：" + error + "。请检查房间码、专用网络及房主状态。")
                        for b in buttons:
                            b.configure(state="normal")
                    return
                self._cancel_current_task()
                self.engine.stop()
                self._cancel_opponent_job()
                self._cancel_playback()
                self._invalidate_api_requests()
                self.lan_session, self.lan_role = session, "host" if create else "guest"
                self.lan_state = None
                self.lan_sending = False
                self.mode = "lan"
                self.sim = None
                self.manual_takeover = False
                for button in (self.ai_btn, self.pvp_btn):
                    button.configure(bg="#26342D", fg="#BFD0C9")
                self._set_coach("局域网对战", "房间已建立，等待棋局同步。")
                pending, self.lan_buffer = self.lan_buffer, []
                if create:
                    pending.append(session.room.snapshot())
                for value in pending:
                    self._lan_event(value, generation)
                self._lan_refresh_controls()
                if win.winfo_exists():
                    win.destroy()
                self.lan_window = None
                self.lan_dialog()
            self._post_ui(done)
        threading.Thread(target=work, daemon=True).start()

    def _lan_room_controls(self, win, body):
        session = self.lan_session
        host = session.interface.ip if self.lan_role == "host" else session.host
        text = (f"房主地址：{host}    端口：{session.port}\n"
                f"房间码：{session.code}\n"
                "房间码区分大小写，只分享给本局对手。\n"
                "断线后双方暂停；加入方可在本窗口重连。关闭此窗口不退出对局。")
        ttk.Label(body, text=text, font=("Microsoft YaHei", 11), padding=5).pack(anchor="w")
        def copy():
            self.clipboard_clear()
            self.clipboard_append(f"{host}:{session.port}\n{session.code}")
        ttk.Button(body, text="复制房间信息", command=copy).pack(fill="x", pady=5)
        for label, action in (("申请悔棋（撤回最后一步）", "undo"), ("申请重新开局", "restart"), ("申请和棋", "draw")):
            ttk.Button(body, text=label, command=lambda a=action: self._lan_request(a)).pack(fill="x", pady=4)
        def resign():
            if messagebox.askyesno("认输", "确定认输并结束本局？", parent=win):
                self._lan_send("resign")
        ttk.Button(body, text="认输", command=resign).pack(fill="x", pady=4)
        if self.lan_role == "guest":
            ttk.Button(body, text="断线后重连", command=self._lan_reconnect).pack(fill="x", pady=4)
        def leave():
            if messagebox.askyesno("退出局域网对局", "退出后房主房间会关闭，或对方进入等待重连状态。确定退出？", parent=win):
                self._lan_leave()
                self._set_mode("pvp")
        ttk.Button(body, text="退出房间", command=leave).pack(fill="x", pady=(12, 0))

    def _lan_event(self, value, generation):
        if generation != self.lan_generation or self.closing:
            return
        if self.lan_session is None:
            if self.lan_connecting and len(self.lan_buffer) < 8:
                self.lan_buffer.append(value)
            return
        kind = value.get("type")
        if kind == "state":
            old = self.lan_state
            if old and value["revision"] < old["revision"]:
                return
            self._cancel_current_task()
            self._cancel_playback()
            self._invalidate_api_requests()
            self.lan_state = value
            self.lan_sending = False
            self.sim = None
            self.premove_mode = False
            self.premove_candidate = None
            self.sim_history, self.sim_move_history = [], []
            self.sim_cycle_base = ()
            self.official = Position()
            self.game_start_fen, self.game_source = START_FEN, "standard"
            self.history, self.move_history = [], []
            self.last = None
            for encoded in value["moves"]:
                move = Move.from_ucci(encoded)
                self.history.append((self.official.copy(), list(self.move_history), self.last))
                self.official.apply_unchecked(move)
                self.move_history.append(encoded)
                self.last = move
            self.human_red = value["host_red"] if self.lan_role == "host" else not value["host_red"]
            if (old and len(value["moves"]) == len(old["moves"]) + 1 and self.history
                    and self.history[-1][0].red == self.human_red):
                self.last_user_before, self.last_user_move = self.history[-1][0].copy(), self.last
                from repetition import from_history
                self.last_user_cycle_context = from_history(self.history[:-1], self.move_history[:-1])
            else:
                self.last_user_before = self.last_user_move = None
                self.last_user_cycle_context = ()
            self.reco = self.selected = None
            self.targets = []
            self.analysis_snapshot = self.analysis_result = None
            self._clear_plan_buttons()
            self._lan_refresh_controls()
            self.draw()
            self._update_turn()
            side = "红方" if self.human_red else "黑方"
            title = value["result"] or ("对方已连接" if value["connected"] else "等待对方连接 / 重连")
            guidance = "允许指导" if value["allow_guidance"] else "关闭指导"
            self.status.configure(text=f"局域网 · 你执{side} · {title}")
            self._refresh_board_caption()
            self._set_coach("局域网棋局", f"{title}\n\n你执{side}，本房间{guidance}。\n"
                            "新局和悔棋需要对方同意；求和、认输和重连请点“联机”。")
            request = value.get("pending")
            if request and request["by"] != self.lan_role and request["id"] not in self.lan_prompted:
                self.lan_prompted.add(request["id"])
                if len(self.lan_prompted) > 100:
                    self.lan_prompted = {request["id"]}
                label = {"undo": "撤回最后一步", "restart": "重新开局", "draw": "和棋"}[request["action"]]
                answer = messagebox.askyesno("对方申请", f"对方申请{label}，是否同意？", parent=self)
                current = self.lan_state
                if (generation == self.lan_generation and current and current.get("pending")
                        and current["pending"]["id"] == request["id"]):
                    self._lan_send("reply", id=request["id"], accept=answer)
        elif kind == "error":
            self.lan_sending = False
            self.status.configure(text="联机操作未完成")
            self._set_coach("局域网提示", str(value.get("message", "未知消息"))[:500])
        elif kind in ("disconnected", "closed"):
            self._cancel_current_task()
            self._cancel_playback()
            self._invalidate_api_requests()
            self.lan_sending = False
            if self.lan_state:
                self.lan_state = {**self.lan_state, "connected": False, "pending": None}
            self.status.configure(text="连接已中断 · 棋局暂停")
            self._set_coach("局域网提示", value["message"])
            self._update_turn()

    def _lan_send(self, kind, **extra):
        state, session = self.lan_state, self.lan_session
        if not state or not session or not state["connected"]:
            self._set_coach("对局暂停", "请等待对方连接，或在“联机”窗口中重连。")
            return
        if self.lan_sending:
            return
        self.lan_sending = True
        command = {"type": kind, "game": state["game"], "revision": state["revision"], **extra}
        generation = self.lan_generation
        def work():
            try:
                if isinstance(session, LanServer):
                    session.submit(command)
                else:
                    session.send(command)
            except Exception as exc:
                event = {"type": "error", "message": str(exc)}
                self._post_ui(lambda: self._lan_event(event, generation))
        threading.Thread(target=work, daemon=True).start()

    def _lan_request(self, action):
        self._lan_send("request", action=action)

    def _lan_can_move(self):
        state = self.lan_state
        return bool(state and state["connected"] and not state["result"] and not state["pending"]
                    and not self.lan_sending and self.official.red == self.human_red)

    def _lan_analysis_allowed(self):
        if self._editing_blocked():return False
        if self.mode != "lan":
            return True
        if self.lan_state and self.lan_state["allow_guidance"] and self.lan_state["connected"]:
            return True
        self._set_coach("本房间关闭指导或已暂停", "房间由房主在创建时选择是否允许双方使用指导功能。")
        return False

    def _lan_refresh_controls(self):
        active = self.mode == "lan"
        guidance = not active or bool(self.lan_state and self.lan_state["allow_guidance"])
        for b in self.lan_guidance_buttons:
            b.configure(state="normal" if guidance else "disabled")
        for b in self.lan_locked_buttons:
            b.configure(state="disabled" if active else "normal")
        self.api_explain_btn.configure(state="normal" if guidance else "disabled")
        self.sim_btn.configure(text="推演沙盘", bg="#36423B", fg="#EDF1EF")
        self._refresh_player_side()

    def _lan_reconnect(self):
        session = self.lan_session
        if not isinstance(session, LanClient) or self.lan_connecting:
            return
        self.lan_connecting = True
        generation = self.lan_generation
        self.status.configure(text="正在重连房主…")
        def work():
            error = ""
            try:
                session.connect()
            except Exception as exc:
                error = str(exc)
            def done():
                if generation != self.lan_generation:
                    return
                self.lan_connecting = False
                if error:
                    self._set_coach("重连失败", error)
            self._post_ui(done)
        threading.Thread(target=work, daemon=True).start()

    def _lan_shutdown(self):
        self.lan_generation += 1
        session = self.lan_session
        self.lan_session = None
        self.lan_connecting = self.lan_sending = False
        self.lan_buffer = []
        if session:
            threading.Thread(target=session.close, daemon=False).start()

    def _lan_leave(self):
        self._lan_shutdown()
        self.lan_state = None
        self.lan_prompted.clear()
        self.mode = "pvp"
        if self.lan_window is not None:
            try:
                self.lan_window.destroy()
            except tk.TclError:
                pass
            self.lan_window = None
        self._lan_refresh_controls()
