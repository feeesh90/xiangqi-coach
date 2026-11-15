"""Visible Windows client-region capture; no remote-control or network access."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from PIL import ImageGrab

class CaptureError(RuntimeError):
    def __init__(self,message,code="capture_failed",details=None):
        super().__init__(message)
        self.code=code
        self.details=details or {}


def api():
    u=ctypes.WinDLL("user32",use_last_error=True)
    u.GetWindowLongPtrW.argtypes=[wintypes.HWND,ctypes.c_int];u.GetWindowLongPtrW.restype=ctypes.c_ssize_t
    u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    u.GetForegroundWindow.restype=wintypes.HWND
    u.SetForegroundWindow.argtypes=[wintypes.HWND]
    u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
    u.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    u.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    if hasattr(u,"GetDpiForWindow"):u.GetDpiForWindow.argtypes=[wintypes.HWND]
    u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    u.GetClientRect.argtypes=u.GetWindowRect.argtypes
    u.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
    u.GetWindow.argtypes=[wintypes.HWND,wintypes.UINT];u.GetWindow.restype=wintypes.HWND
    u.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];u.GetAncestor.restype=wintypes.HWND
    u.IsWindow.argtypes=u.IsWindowVisible.argtypes=u.IsIconic.argtypes=[wintypes.HWND]
    return u


@contextmanager
def physical_pixels():
    u=api();setter=getattr(u,"SetThreadDpiAwarenessContext",None)
    previous=None
    if setter:
        setter.argtypes=[ctypes.c_void_p];setter.restype=ctypes.c_void_p
        previous=setter(ctypes.c_void_p(-4))
    try:yield
    finally:
        if previous:setter(previous)


def process_id(hwnd):
    value=wintypes.DWORD();api().GetWindowThreadProcessId(hwnd,ctypes.byref(value))
    return value.value


def process_name(hwnd):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,process_id(hwnd))
    if not handle:return ""
    try:
        name=ctypes.create_unicode_buffer(4096);size=wintypes.DWORD(4096)
        return name.value.replace("\\","/").split("/")[-1].lower() if kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size)) else ""
    finally:kernel.CloseHandle(handle)


def clickthrough_overlay(style):
    # All three flags: layered, click-through and non-activating. Layered alone
    # is not enough: ordinary translucent dialogs can still cover the board.
    flags=0x80000|0x20|0x08000000
    return style & flags == flags


def windows():
    u=api();result=[]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,_):
        if (u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd)
                and not clickthrough_overlay(u.GetWindowLongPtrW(hwnd,-20))):
            text=ctypes.create_unicode_buffer(512);u.GetWindowTextW(hwnd,text,512)
            if text.value:result.append((int(hwnd),text.value))
        return True
    u.EnumWindows(callback(visit),0)
    return result


def phone_window_choices():
    candidates=windows()
    remote=[item for item in candidates if process_name(item[0])=="todesk.exe"]
    candidates=remote or candidates
    return sorted(candidates,key=lambda item:(item[1].strip().lower()=="todesk",item[1].lower()))


def source_is_foreground(hwnd):
    u=api()
    return bool(u.IsWindow(hwnd) and
        u.GetAncestor(u.GetForegroundWindow(),2)==u.GetAncestor(hwnd,2))


def activate_source(hwnd,*,verify=True):
    """Only during explicit initial capture; never steal focus in the live loop."""
    u=api()
    if not u.IsWindow(hwnd):raise CaptureError("手机画面窗口已关闭，请重新选择窗口", "target_closed")
    if u.IsIconic(hwnd):u.ShowWindow(hwnd,9)
    u.SetForegroundWindow(hwnd)
    if verify and not source_is_foreground(hwnd):
        raise CaptureError("未能将手机窗口置前，请先点一下手机画面，再重试采集", "not_foreground")


def window_cloaked(hwnd):
    """None means unknown: never ignore a window on a failed DWM query."""
    try:
        dwm=ctypes.WinDLL("dwmapi",use_last_error=True)
        query=dwm.DwmGetWindowAttribute
        query.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
        query.restype=ctypes.c_long
        value=wintypes.DWORD()
        return bool(value.value) if query(hwnd,14,ctypes.byref(value),ctypes.sizeof(value))==0 else None
    except (OSError,AttributeError):return None


def window_details(hwnd):
    u=api();title=ctypes.create_unicode_buffer(256);name=ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(hwnd,title,256)
    if hasattr(u,"GetClassNameW"):u.GetClassNameW(hwnd,name,256)
    return dict(hwnd=int(hwnd),title=title.value,process=process_name(hwnd),window_class=name.value)


def occlusion_details(hwnd,box,initial=False):
    u=api();above=u.GetWindow(hwnd,3);seen=set();result=[]
    pid=process_id(hwnd)
    while above and above not in seen:
        seen.add(above)
        if u.IsWindowVisible(above) and not u.IsIconic(above) and window_cloaked(above) is not True:
            style=u.GetWindowLongPtrW(above,-20)
            own_tool=process_id(above)==pid and (bool(style & 0x80) or
                (u.GetWindow(above,4)==hwnd and bool(style & 0x08000000)))
            if not clickthrough_overlay(style) and not (initial and own_tool):
                rect=wintypes.RECT()
                read=u.GetWindowRect(above,ctypes.byref(rect))
                if read or read is None:
                    overlap=(max(box[0],rect.left),max(box[1],rect.top),
                        min(box[2],rect.right),min(box[3],rect.bottom))
                    if overlap[2]>overlap[0] and overlap[3]>overlap[1]:
                        item=window_details(above)
                        label=item["title"] or (item["process"] or item["window_class"] or f"HWND {int(above)}")+"／无标题窗口"
                        result.append(dict(**item,label=label,intersection=overlap,
                            checked_at=datetime.now(timezone.utc).isoformat()))
        above=u.GetWindow(above,3)
    return result


def occluding_windows(hwnd,box,initial=False):
    # Keep the existing text-list interface for callers and older tests.
    return [item["label"] for item in occlusion_details(hwnd,box,initial)]


def geometry(hwnd):
    with physical_pixels():return _geometry(hwnd)

def _geometry(hwnd):
    u=api()
    if not u.IsWindow(hwnd):raise CaptureError("手机画面窗口已关闭，请重新选择窗口", "target_closed")
    if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
        raise CaptureError("目标窗口已隐藏或最小化", "target_unavailable")
    rect=wintypes.RECT();point=wintypes.POINT(0,0)
    if not u.GetClientRect(hwnd,ctypes.byref(rect)) or not u.ClientToScreen(hwnd,ctypes.byref(point)):
        raise CaptureError("无法读取窗口位置", "geometry_failed")
    dpi=u.GetDpiForWindow(hwnd) if hasattr(u,"GetDpiForWindow") else 96
    return point.x,point.y,rect.right,rect.bottom,dpi


class WindowCapture:
    def __init__(self,hwnd,roi=None):
        self.hwnd=int(hwnd);self.baseline=geometry(hwnd)[2:]
        w,h=self.baseline[:2]
        self.roi=tuple(roi or (0,0,w,h));self.guard=None
        l,t,r,b=self.roi
        if not (0<=l<r<=w and 0<=t<b<=h):raise CaptureError("棋盘区域越界", "invalid_roi")

    def set_grid(self,corners):
        # Protect the actual board glyph area, not unrelated client-side chrome.
        xs=[float(p[0]) for p in corners];ys=[float(p[1]) for p in corners]
        px=(max(xs)-min(xs))/8;py=(max(ys)-min(ys))/9
        w,h=self.roi[2]-self.roi[0],self.roi[3]-self.roi[1]
        self.guard=(max(0,min(xs)-px*.30),max(0,min(ys)-py*.30),
                    min(w,max(xs)+px*.30),min(h,max(ys)+py*.30))

    def grab(self,check_occlusion=True,initial=False):
        with physical_pixels():return self._grab(check_occlusion,initial)

    def check_ready(self,*,initial=False,require_foreground=False):
        with physical_pixels():
            return self._check_ready(True,initial,require_foreground)

    def _check_ready(self,check_occlusion,initial=False,require_foreground=False):
        x,y,w,h,dpi=geometry(self.hwnd)
        if (w,h,dpi)!=self.baseline:raise CaptureError("窗口尺寸或 DPI 改变，请重新校准", "size_changed")
        if require_foreground and not source_is_foreground(self.hwnd):
            raise CaptureError("手机窗口未置前，请点一下手机画面后重试采集", "not_foreground")
        l,t,r,b=self.roi;box=(x+l,y+t,x+r,y+b)
        if check_occlusion:
            guard=box if self.guard is None else (x+l+self.guard[0],y+t+self.guard[1],x+l+self.guard[2],y+t+self.guard[3])
            blockers=occlusion_details(self.hwnd,guard,initial)
            if blockers:raise CaptureError("棋盘被窗口遮挡："+blockers[0]["label"]+"。请移开后重试采集。",
                "occluded",dict(blockers=blockers))
        return box

    def _grab(self,check_occlusion,initial=False):
        box=self._check_ready(check_occlusion,initial,require_foreground=initial)
        image=ImageGrab.grab(bbox=box,all_screens=True)
        extrema=image.getextrema()
        if all(hi-lo<3 for lo,hi in extrema):
            image.close();raise CaptureError("采集结果为空白或黑屏，请保持手机画面窗口展开后重试", "blank_frame")
        return image
