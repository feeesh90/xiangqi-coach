"""Small Tk integration; confirmed state is independent of capture and preview."""
import tkinter as tk
from time import monotonic
from tkinter import ttk,messagebox
from rules import Move,legal_moves,move_desc
from screen_follow.session import FollowSession

class FollowUI:
    def _follow_locked(self):
        s=self.__dict__.get("follow_session")
        return s is not None and s.phase in ("following","paused")

    def _follow_blocked(self):
        if self.__dict__.get("follow_setup") is not None:
            self.status.configure(text="请先完成或取消投屏校准");return True
        if not self._follow_locked():return False
        self.status.configure(text="请先停止跟随并手动接管，再操作本地棋局")
        return True

    def _build_follow_controls(self,parent):
        self.follow_session=None;self.follow_process=None;self.follow_setup=None
        self.follow_button=ttk.Button(parent,text="投屏跟随",command=self.open_follow,style="Board.TButton")
        self.follow_button.pack(side="left",padx=(5,0))
        self.follow_actions=tk.Frame(self.board_stage,bg="#202622")
        self.follow_actions.grid(row=4,column=0,sticky="ew")
        self.follow_resync=ttk.Button(self.follow_actions,text="重新同步",command=self.open_follow)
        self.follow_resync.pack(side="left")
        self.follow_stop=ttk.Button(self.follow_actions,text="停止跟随并手动接管",command=self.stop_follow)
        self.follow_stop.pack(side="left",padx=6)
        self.follow_actions.grid_remove()

    def open_follow(self):
        if self.__dict__.get("follow_setup") is not None:
            self.follow_setup.lift();return
        if self.mode=="lan" or self.lan_connecting or self._editing() or self.sim is not None:
            self.status.configure(text="请先退出联机、创造或推演模式");return
        if self.endgame_window is not None or self.engine_settings_window is not None:
            self.status.configure(text="请先关闭图片导入或引擎设置窗口");return
        self._cancel_current_task();self._cancel_opponent_job();self._cancel_playback()
        old=self.__dict__.get("follow_session")
        if old:
            old.phase="paused"
            process=self.__dict__.get("follow_process")
            if process:process.close();self.follow_process=None
        from screen_follow.setup import FollowSetup
        self.follow_setup=FollowSetup(self);self._refresh_player_side()
        self._refresh_board_caption()

    def _follow_begin(self,pos,own_red,view="auto",worker=None,capture=None,corners=None,red_bottom=True):
        old=self.__dict__.get("follow_session")
        preserve=False;move=None
        if old:
            kind,move=old.match_board(pos.b)
            preserve=(not old.requires_resync and kind in ("unchanged","move") and (pos.red==old.position.red if kind=="unchanged" else pos.red!=old.position.red))
            if not preserve and not messagebox.askyesno("以屏幕局面建立新起点", "无法补齐中间历史。以当前屏幕建立新起点会清空走棋历史，是否继续？",parent=self):return False
        self._cancel_current_task();self._cancel_opponent_job();self._invalidate_api_requests()
        if preserve:
            s=old
            if move:self._follow_commit(move,observed=True)
            pending=s.proposal.move if s.proposal and s.proposal.stamp==s.stamp else None
            s.own_red=own_red;s.phase="following";s.epoch=__import__("uuid").uuid4().hex
            s.proposal=None
            if pending:s.offer(pending,s.stamp)
        else:
            self.follow_session=None;self.mode="follow"
            self._reset_game_from_fen(pos.fen(),"screen")
            s=self.follow_session=FollowSession(pos,own_red)
        self.mode="follow";s.notice="";s.tracking="unchanged";s.requires_resync=False;self.board_view_mode=view
        if self.follow_process and self.follow_process is not worker:self.follow_process.close()
        self.follow_process=worker
        self.follow_last_message=monotonic();self.follow_last_heartbeat=self.follow_last_message
        if worker:
            worker.callback=lambda event:self._post_ui(lambda:self._follow_event(event))
            try:
                worker.send("start",hwnd=capture.hwnd,roi=capture.roi,geometry=capture.baseline,
                    corners=corners,red_bottom=red_bottom,fen=s.position.fen(),epoch=s.epoch,version=s.version)
            except Exception as e:self._follow_pause(str(e))
        self.follow_actions.grid();self._follow_refresh();self.draw();self._follow_request()
        if worker:self.follow_watch_after=self.after(1000,lambda:self._follow_watch(s,worker))
        return True

    def _follow_event(self,event):
        s=self.__dict__.get("follow_session")
        if not s or s.phase!="following" or event.get("epoch")!=s.epoch:return
        self.follow_last_message=monotonic()
        # Health is scoped to the capture session, not the chess version. A
        # heartbeat queued across an accept acknowledgement still proves life.
        if event.get("kind") in ("heartbeat","started"):
            self.follow_last_heartbeat=self.follow_last_message
            return
        if event.get("version")!=s.version:return
        s.diagnostics.append({"time":self.follow_last_message,**{k:event[k] for k in
            ("kind","state","epoch","version","candidate_count","changed_cells","mismatch_cells","max_error","visual_error","empty_error","reason","message") if k in event}})
        if event["kind"]=="move":
            if s.requires_resync:return
            move=Move.from_ucci(event.get("move",""))
            if move not in legal_moves(self.official):self._follow_pause("识别着法不合法，未改动棋局");return
            if not self.__dict__.get("switching_engine",False):self._cancel_current_task()
            if self._follow_commit(move,observed=True):
                try:self.follow_process.send("accept",epoch=s.epoch,version=s.version,move=move.ucci())
                except Exception as e:self._follow_pause(str(e));return
                self._follow_request()
        elif event["kind"]=="error":
            self._follow_pause(event.get("reason",event.get("message","需要重新同步")))
        elif event["kind"]=="resync_required":
            self._follow_require_resync(event.get("reason","需要重新同步；仍在采集"))
        elif event["kind"]=="uncertain":
            if not s.requires_resync:s.tracking="waiting_clear";self._follow_refresh()
        elif event["kind"]=="tracking":
            state=event.get("state","unchanged")
            if state=="resync_required":
                if not s.requires_resync:self._follow_require_resync(event.get("reason","需要重新同步；仍在采集"))
            elif not s.requires_resync and state in ("checking","waiting_clear","unchanged") and s.tracking!=state:
                s.tracking=state;self._follow_refresh()

    def _follow_watch(self,session,worker):
        self.follow_watch_after=None
        if self.closing or self.__dict__.get("follow_session") is not session or self.follow_process is not worker:return
        if session.phase!="following":return
        latest=self.follow_last_heartbeat
        reader_health=getattr(worker,"last_heartbeat_at",None)
        if reader_health:
            received=reader_health(session.epoch)
            if received is not None:latest=max(latest,received)
        if monotonic()-latest>5:
            worker.close();self._follow_pause("识别任务超时，请重新同步或手动接管");return
        self.follow_watch_after=self.after(1000,lambda:self._follow_watch(session,worker))

    def _follow_pause(self,reason):
        s=self.__dict__.get("follow_session")
        if not s:return
        s.phase="paused";s.notice=reason
        s.diagnostics.append(dict(time=monotonic(),kind="paused",version=s.version,reason=reason))
        if self.follow_process:
            try:self.follow_process.send("pause")
            except Exception:pass
        self.status.configure(text=reason)
        self._follow_refresh()

    def _follow_require_resync(self,reason):
        s=self.__dict__.get("follow_session")
        if not s or s.phase!="following":return
        s.requires_resync=True;s.tracking="resync_required";s.notice=reason
        if self.follow_process:
            try:self.follow_process.send("freeze_sync",epoch=s.epoch)
            except Exception as e:self._follow_pause(str(e));return
        self._follow_refresh()

    def _follow_commit(self,move,observed=False):
        s=self.follow_session
        if observed and s.requires_resync:return False
        if not self.make_move(move,auto_ai=False,actor="screen"):return False
        if not s.commit(move,observed):raise RuntimeError("跟随状态不同步")
        self._follow_refresh();self.draw();return True

    def _follow_after_local_move(self,move):
        s=self.__dict__.get("follow_session")
        if s and self.sim is None and s.phase=="manual":
            if not s.commit(move):raise RuntimeError("手动续局状态不同步")
            self._follow_refresh()

    def _follow_request(self):
        s=self.__dict__.get("follow_session")
        if self.sim is not None or self._editing():return
        if (not s or not s.own_turn or s.proposal or self.busy or s.phase=="paused" or s.requires_resync
                or self.__dict__.get("follow_setup") is not None
                or not legal_moves(s.position) or getattr(self.engine,"startup_failed",False)):return
        from app import LEVELS
        stamp=s.stamp;snap=s.position.copy();context=s.context
        ms=LEVELS[self.level.get()];ticket=self.engine.ticket()
        def done(result):
            if self.__dict__.get("follow_session") is not s or s.stamp!=stamp or self.engine.ticket()!=ticket:return
            if s.offer(result.best,stamp):
                manual=s.phase=="manual"
                if manual:self._follow_commit(result.best)
                self._set_coach("我方推荐",move_desc(snap,result.best)+"\n\n"+(
                    "请在手机走出电脑的这一步；对手落子后，在电脑录入对手走法。" if manual else
                    "主棋盘已展示建议。请在手机落子；你也可以另选走法，电脑将以识别确认的实际落子为准。"))
                self._follow_refresh();self.draw()
                if s.phase=="paused":self.status.configure(text=s.notice)
        self._run_task("投屏我方推荐计算中",lambda:self._analyze_position(snap,ms,1,context),done)
        self._follow_refresh()

    def stop_follow(self):
        s=self.__dict__.get("follow_session")
        if not s or s.phase=="manual":return
        setup=self.__dict__.get("follow_setup")
        if setup:setup.close()
        process=self.__dict__.get("follow_process")
        if process:process.close();self.follow_process=None
        s.phase="manual";s.notice="";s.requires_resync=False;s.tracking="unchanged"
        # Do not invalidate an in-flight search for the same confirmed position.
        if s.proposal:self._follow_commit(s.proposal.move)
        self.follow_actions.grid();self._follow_refresh();self.draw();self._follow_request()

    def _follow_refresh(self):
        s=self.__dict__.get("follow_session")
        if not s:return
        if s.phase=="manual":
            self.follow_actions.grid_remove();self.action_bar.grid();self.playbar.grid()
        else:
            self.follow_actions.grid();self.action_bar.grid_remove();self.playbar.grid_remove()
        for widget in (self.creative_button,self.ai_btn,self.pvp_btn):
            widget.configure(state="disabled" if self._follow_locked() else "normal")
        self._refresh_board_caption();self._update_turn()
        self.follow_stop.configure(state="disabled" if s.phase=="manual" else "normal")

    def _follow_caption(self):
        s=self.follow_session;side="红方" if s.own_red else "黑方"
        if s.phase=="manual":return f"手动续局 · JJ我方{side}由电脑走"
        if s.phase=="paused":return f"投屏跟随 · 我方{side} · 跟随已暂停\n{s.notice or '需要重新同步'}"
        if s.requires_resync:return f"投屏跟随 · 我方{side} · 需要重新同步（仍在采集）\n{s.notice}"
        if s.tracking=="waiting_clear":return f"投屏跟随 · 我方{side} · 等待画面清晰，仍在监听"
        state="正在核对手机画面" if s.tracking=="checking" else (
            "正在计算推荐" if self.busy and s.own_turn else "等待手机落子" if s.own_turn else "等待对手落子")
        return f"投屏跟随 · 我方{side} · {state}"

    def _follow_end(self):
        watch=self.__dict__.get("follow_watch_after")
        if watch:
            try:self.after_cancel(watch)
            except tk.TclError:pass
        self.follow_watch_after=None
        for name in ("action_bar","playbar"):
            widget=self.__dict__.get(name)
            if widget:widget.grid()
        for name in ("creative_button","ai_btn","pvp_btn"):
            widget=self.__dict__.get(name)
            if widget:widget.configure(state="normal")
        process=self.__dict__.get("follow_process")
        if process:process.close()
        self.follow_process=None;self.follow_session=None
        actions=self.__dict__.get("follow_actions")
        if actions:actions.grid_remove()
        if self.__dict__.get("mode")=="follow":self.mode="ai"

    def _follow_close(self):
        setup=self.__dict__.get("follow_setup")
        if setup:setup.close()
        self._follow_end()
