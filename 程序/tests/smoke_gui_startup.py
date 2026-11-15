import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import App


ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
app = App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
app.geometry("1080x720")
try:
    def poll_ready():
        if "已就绪" in app.engine_badge.cget("text"):
            app.quit()
        else:
            app.after(50, poll_ready)

    app.after(50, poll_ready)
    app.after(10000, app.quit)
    app.mainloop()

    engine_text = app.engine_badge.cget("text")
    book_text = app.book_badge.cget("text")
    print("ENGINE_BADGE", engine_text)
    print("BOOK_BADGE", book_text)
    print("DEFAULT_SPEED", app.coach.get())
    assert "已就绪" in engine_text
    assert "27369" in book_text
    assert app.coach.get() == "标准"

    # V8.3 regression: the two-pane workbench must remain fully visible at the
    # supported minimum size, and the chessboard cells must stay square.
    app.update_idletasks()
    root_x=app.winfo_rootx();root_y=app.winfo_rooty()
    panel_right=app.analysis_panel.winfo_rootx()-root_x+app.analysis_panel.winfo_width()
    action_bottom=app.action_bar.winfo_rooty()-root_y+app.action_bar.winfo_height()
    _,_,dx,dy=app._geo()
    print("MIN_SIZE_LAYOUT",app.winfo_width(),panel_right,action_bottom,dx,dy)
    assert panel_right <= app.winfo_width()
    assert action_bottom <= app.winfo_height()
    assert abs(dx-dy)<0.01
    print("MIN_SIZE_LAYOUT_OK")
    print("GUI_STARTUP_OK")
finally:
    app._close()
