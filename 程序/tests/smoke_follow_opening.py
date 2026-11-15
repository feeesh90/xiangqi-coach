"""Synthetic recognized snapshots + real Tk controls and engines, not ToDesk."""
import gc
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from time import monotonic

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))

from app import App
from rules import Move, Position, legal_moves
from smoke_player_settings import wait
from smoke_screen_follow import CORNERS, render, snapshot


class TestTransport:
    """No real phone/window worker; observes the same start/accept protocol."""
    def __init__(self):self.sent=[];self.closed=False
    def send(self,*args,**kwargs):self.sent.append((args,kwargs))
    def close(self):self.closed=True
    def last_heartbeat_at(self,epoch):return monotonic()


def main():
    out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'data/screen_follow_verification/20261028_opening_defaults/engine_smoke'
    out.mkdir(parents=True,exist_ok=True)
    settings=Path.home()/'.xiangqi-coach'
    def hashes():
        return {name:hashlib.sha256((settings/name).read_bytes()).hexdigest() if (settings/name).exists() else None
            for name in ('engine_settings.json','ui_preferences.json','api_settings.json')}
    before=hashes()
    report=dict(source='labelled synthetic recognition results; actual Tk selection/start and real engines; simulated capture transport, not live JJ/ToDesk',cases=[])
    profiles=json.loads((ROOT/'engine_settings.gpu.example.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='xiangqi-opening-smoke-') as temp:
        temp=Path(temp)
        for engine in ('pikafish','px0'):
            config=temp/f'{engine}.json';config.write_text(json.dumps({**profiles,'selected':engine}),encoding='utf-8')
            app=App(preferences_path=temp/'ui.json',engine_config_path=config)
            app.geometry('1080x720+20+20');app.api.configured=lambda:False
            try:
                wait(app,lambda:not app.busy and app.engine.ready)
                assert app.engine.selected==engine
                app.level.set('入门')
                for own in (True,False):
                    for first_move in (False,True):
                        app._cancel_current_task();app._follow_end();app.mode='pvp';app.new_game()
                        app.board_view_mode='auto'
                        position=Position()
                        if first_move:assert position.apply(Move.from_ucci('b0c2'))
                        raw=position.copy()
                        if not own:raw.b=[row[::-1] for row in raw.b[::-1]]
                        app.open_follow();setup=app.follow_setup
                        with render(raw) as frame:setup.set_frame(frame)
                        setup.set_result(dict(board=raw.b,uncertain=[],corners=CORNERS,image_size=[592,656],total_seconds=.1))
                        app.update()
                        setup.geometry('960x640');app.update()
                        button=setup.apply_button
                        assert button.winfo_ismapped()
                        assert button.winfo_rooty()-setup.winfo_rooty()+button.winfo_height()<=setup.winfo_height()
                        setup.geometry('1000x800');app.update()
                        assert setup.human_var.get()==('红方' if own else '黑方')
                        assert setup.turn_var.get()==('黑方' if first_move else '红方')
                        assert setup.orientation_var.get()==('红方' if own else '黑方')
                        assert app.follow_session is None and not app.move_history
                        if engine=='pikafish' and (own,first_move) in ((True,False),(False,True)):
                            setup.lift();setup.attributes('-topmost',True);app.update()
                            snapshot(setup,out/('auto_red_initial.png' if own else 'auto_black_first_move.png'))
                        transport=TestTransport();setup.worker=transport
                        setup.capture=type('TestCapture',(),dict(hwnd=123,roi=(0,0,592,656),baseline=(0,0,592,656,96)))()
                        setup.apply_button.invoke();app.update()
                        session=app.follow_session
                        assert app.follow_setup is None and session.own_red==own
                        assert app.official.fen()==position.fen() and not app.move_history
                        if not session.own_turn:
                            assert session.proposal is None
                            move=legal_moves(position)[0];assert position.apply(move)
                            app._follow_event(dict(kind='move',epoch=session.epoch,version=session.version,move=move.ucci()))
                        wait(app,lambda:not app.busy and session.proposal is not None,timeout=30)
                        assert session.position.fen()==app.official.fen()==position.fen()
                        assert session.proposal.move in legal_moves(position)
                        assert transport.sent[0][0]==('start',)
                        report['cases'].append(dict(engine=engine,own_red=own,red_first_move=first_move,
                            initial_turn='black' if first_move else 'red',initial_fen=app.game_start_fen,
                            recommendation=session.proposal.move.ucci(),history=app.move_history[:],
                            recommendation_legal=True,started_only_after_button=True))
            finally:
                app._close();app.engine.close();app.__dict__.clear();gc.collect()
    assert before==hashes();report['real_user_configs_unchanged']=True
    (out/'real_engine_opening_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
