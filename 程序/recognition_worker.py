"""One-shot local worker. Input/result JSON only; images are never written."""
import json
import sys
import time


def memory_mb():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD)] + [
            (name,ctypes.c_size_t) for name in ("PeakWorkingSetSize","WorkingSetSize",
            "QuotaPeakPagedPoolUsage","QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage","PagefileUsage","PeakPagefileUsage")]
    c = Counters()
    c.cb = ctypes.sizeof(c)
    current=ctypes.windll.kernel32.GetCurrentProcess
    current.restype=wintypes.HANDLE
    get_memory=ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    get_memory.restype=wintypes.BOOL
    handle = current()
    if get_memory(handle,ctypes.byref(c),c.cb):
        return {"current_mb":round(c.WorkingSetSize/1048576,1),"peak_mb":round(c.PeakWorkingSetSize/1048576,1)}
    return None


def main():
    started = time.monotonic()
    try:
        raw = sys.stdin.buffer.read(16_385)
        if len(raw)>16_384:
            raise ValueError("识别请求过长。")
        request = json.loads(raw)
        if not isinstance(request,dict) or not isinstance(request.get("image"),str):
            raise ValueError("识别请求格式错误。")
        from board_recognizer import BoardRecognizer
        model = BoardRecognizer()
        loaded = time.monotonic()
        result = model.recognize(request["image"],request.get("corners"))
        result["model_load_seconds"] = round(loaded-started,3)
        result["total_seconds"] = round(time.monotonic()-started,3)
        result["memory"] = memory_mb()
        payload = {"ok":True,"result":result}
    except ModuleNotFoundError as exc:
        payload = {"ok":False,"error":f"图片识别依赖缺失：{exc.name}。请运行“安装图片识别.bat”后重试。"}
    except Exception as exc:
        payload = {"ok":False,"error":str(exc)[:2000]}
    sys.stdout.buffer.write(json.dumps(payload,ensure_ascii=False).encode("utf-8"))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
