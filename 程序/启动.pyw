"""象棋指导棋的无控制台启动入口。

由根目录的“象棋指导棋.exe”用 python.exe 启动；本文件只负责切目录、
把程序根加入 sys.path、启动主窗口，并在启动失败时写日志并弹窗，
避免用户看到一闪而过的黑窗口却不知道发生了什么。
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def fail(exc: BaseException) -> None:
    message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log = ROOT / "data" / "启动失败.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(message, encoding="utf-8")
    except OSError:
        log = None
    text = (
        "象棋指导棋启动失败。\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        + (f"详细日志：{log}\n\n" if log else "")
        + "请先尝试运行“程序\\启动（控制台诊断）.bat”查看完整错误信息。"
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, "象棋指导棋", 0x10)
    except Exception:
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import App

    App().mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:
        fail(exc)
        raise SystemExit(1)
