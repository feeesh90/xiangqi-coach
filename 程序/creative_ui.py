"""Main-board creative editing; live game data remains untouched until apply."""
import tkinter as tk
from tkinter import ttk

from board_editor import BoardEditor
from rules import legal_moves, piece_name

PIECE_CHOICES = {("红" if p.isupper() else "黑") + piece_name(p): p
                 for p in "RNBAKCPrnbakcp"}


class CreativeUI:
    def _editing(self):
        return self.__dict__.get("editor") is not None

    def _editing_blocked(self):
        if not self._editing():
            return False
        self.status.configure(text="请先应用或取消创造模式")
        return True

    def _build_editor_controls(self, parent):
        self.editor_toolbar = tk.Frame(parent, bg="#2C342F", padx=6, pady=5)
        self.editor_toolbar.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        self.editor_tool_var = tk.StringVar(master=self, value="move")
        self.editor_piece_var = tk.StringVar(master=self, value="红车")
        self.editor_turn_var = tk.StringVar(master=self, value="红方")
        tools = tk.Frame(self.editor_toolbar, bg="#2C342F")
        tools.pack(fill="x")
        for text, value in (("移动", "move"), ("添加", "add"), ("删除", "delete")):
            ttk.Radiobutton(tools, text=text, value=value, variable=self.editor_tool_var,
                            command=self._editor_tool_changed).pack(side="left", padx=(0, 5))
        self.editor_piece_selector = ttk.Combobox(
            tools, textvariable=self.editor_piece_var, values=list(PIECE_CHOICES),
            state="readonly", width=5)
        self.editor_piece_selector.pack(side="left", padx=(0, 8))
        self.editor_piece_selector.bind("<<ComboboxSelected>>", self._editor_piece_changed)
        tk.Label(tools, text="轮到", bg="#2C342F", fg="white").pack(side="left")
        self.editor_turn_selector = ttk.Combobox(
            tools, textvariable=self.editor_turn_var, values=("红方", "黑方"),
            state="readonly", width=5)
        self.editor_turn_selector.pack(side="left", padx=4)
        self.editor_turn_selector.bind("<<ComboboxSelected>>", self._editor_turn_changed)
        actions = tk.Frame(self.editor_toolbar, bg="#2C342F")
        actions.pack(fill="x", pady=(4, 0))
        self.editor_apply_button = ttk.Button(actions, text="应用并继续", command=self.apply_creative)
        self.editor_apply_button.pack(side="left")
        self.editor_cancel_button = ttk.Button(actions, text="取消", command=self.cancel_creative)
        self.editor_cancel_button.pack(side="left", padx=5)
        tk.Label(actions, text="移到已有棋子处会交换；添加会替换",
                 bg="#2C342F", fg="#D9E5DF", font=("Microsoft YaHei", 8)).pack(side="left")
        self.editor_toolbar.grid_remove()

    def _editor_tool_changed(self):
        if self._editing():
            self.editor.tool = self.editor_tool_var.get()
            self.editor.selected = None
            self.draw()

    def _editor_piece_changed(self, event=None):
        if self._editing():
            self.editor.piece = PIECE_CHOICES[self.editor_piece_var.get()]
            self.editor_tool_var.set("add")
            self._editor_tool_changed()

    def _editor_turn_changed(self, event=None):
        if self._editing():
            self.editor.red_to_move = self.editor_turn_var.get() == "红方"
            self._update_turn()

    def _editor_widgets(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self._editor_widgets(child)

    def _suspend_editor_controls(self):
        allowed = set(self._editor_widgets(self.editor_toolbar)) | {self.flip_board_button}
        states = {}
        for widget in self._editor_widgets(self):
            if widget in allowed:
                continue
            if isinstance(widget, (tk.Button, ttk.Button, ttk.Combobox, ttk.Menubutton)):
                states[widget] = widget.cget("state")
                widget.configure(state="disabled")
        return states

    def _restore_editor_controls(self, states):
        for widget, state in states.items():
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def enter_creative(self):
        if self._follow_blocked():return
        if self._editing():
            return
        if self.mode == "lan" or self.__dict__.get("lan_connecting"):
            self._set_coach("创造模式", "创造模式仅用于本地棋局，请先退出联机。")
            return
        if self.sim is not None or self.playback_moves:
            self._set_coach("创造模式", "请先退出推演或回放，再编辑正式棋局。")
            return
        if self.__dict__.get("endgame_window") is not None:
            self._set_coach("创造模式", "请先完成或取消图片导入。")
            return
        self.editor_snapshot = {
            "title": self.coach_title.cget("text"),
            "body": self.coach_text.get("1.0", "end-1c"),
            "api_feedback": self.api_last_feedback,
        }
        self._cancel_opponent_job()
        self._cancel_current_task()
        # Also invalidate a queued callback when busy has already become false.
        self.task_token += 1
        self.engine.stop()
        self._invalidate_api_requests()
        self._cancel_playback()
        self.premove_mode = False
        self.premove_candidate = None
        self.selected = None
        self.targets = []
        self.editor = BoardEditor.from_position(self.official)
        self.editor_tool_var.set("move")
        self.editor_turn_var.set("红方" if self.official.red else "黑方")
        self.editor.piece = PIECE_CHOICES[self.editor_piece_var.get()]
        self.editor_snapshot["controls"] = self._suspend_editor_controls()
        # Guidance and playback are suspended; give their space to the board.
        self.action_bar.grid_remove()
        self.playbar.grid_remove()
        self.editor_toolbar.grid()
        self._refresh_board_caption()
        self._set_coach("创造模式",
            "移动：点棋子，再点目标位置；目标有子时交换。\n\n"
            "添加：选择红黑棋子，再点位置；已有棋子会被替换。\n"
            "删除：切换到删除工具，再点棋子。\n\n"
            "应用后以新局面继续，旧历史清空；取消则回到原棋局。\n"
            "编辑中可暂时缺将或多子，应用时会检查规则。")
        self.status.configure(text="创造模式 · 自由摆子 · Esc 取消")
        self.draw()
        self._update_turn()

    def _leave_editor(self):
        snapshot = self.editor_snapshot
        self.editor = None
        self.editor_snapshot = None
        self.editor_toolbar.grid_remove()
        self.action_bar.grid()
        self.playbar.grid()
        self._restore_editor_controls(snapshot["controls"])
        self.selected = None
        self.targets = []
        return snapshot

    def apply_creative(self):
        if not self._editing():
            return
        try:
            self.editor.red_to_move = self.editor_turn_var.get() == "红方"
            position = self.editor.position()
        except ValueError as exc:
            self._set_coach("局面尚不能应用", str(exc))
            self.status.configure(text="请修正局面后重试，或取消创造模式")
            return
        self._leave_editor()
        self._reset_game_from_fen(position.fen(), "creative")

    def cancel_creative(self):
        if not self._editing():
            return
        snapshot = self._leave_editor()
        self.api_last_feedback = snapshot["api_feedback"]
        self._set_coach(snapshot["title"], snapshot["body"])
        self._refresh_board_caption()
        self.draw()
        self._update_turn()
        self.status.configure(text="已取消创造模式，恢复原棋局")
        if self._is_ai_turn() and not self.manual_takeover and legal_moves(self.official):
            self._schedule_opponent()

    def _editor_click(self, event):
        ox, oy, dx, dy = self._geo()
        c, r = round((event.x-ox)/dx), round((event.y-oy)/dy)
        if not (0 <= r < 10 and 0 <= c < 9):
            return
        if abs(event.x-(ox+c*dx)) > dx*.45 or abs(event.y-(oy+r*dy)) > dy*.45:
            return
        self.editor.click(*self._view_to_board(r, c))
        self.draw()
