# -*- coding: utf-8 -*-
"""显示器控制模块 —— 真正「断开/连接」显示器（SetDisplayConfig），可被保活主程序导入。

与上一版的区别：
  * 旧版只有 DDC 关屏（背光灭，但桌面空间仍在，鼠标能飘过去）
  * 新版主操作是 **断开 DISPLAYCONFIG_PATH**：系统里这台屏消失，鼠标不会移过去
  * DDC 关屏降级为右键附加功能

安全约束：
  - 最后一台亮着的屏不允许断开
  - 断开前记下窗口位置；若窗口在被断开的屏上，自动迁到仍连接的屏
  - 断开状态可逆：用 GET_TARGET_PREFERRED_MODE 重建模式后重新连接
"""
import ctypes
import json
import os
import re
import subprocess
import time
import tkinter as tk
from ctypes import wintypes, byref, POINTER, sizeof, memmove

# ---------------- 常量 ----------------
QDC_ALL_PATHS = 1
SDC_USE_SUPPLIED = 0x20
SDC_APPLY = 0x80
SDC_SAVE = 0x200
SDC_ALLOW_CHANGES = 0x400
PATH_ACTIVE = 0x1
INVALID_IDX = 0xFFFFFFFF

GET_SOURCE_NAME = 1
GET_TARGET_NAME = 2
GET_TARGET_PREFERRED_MODE = 3

VCP_POWER = 0xD6
PWR_ON = 0x01
PWR_OFF_SOFT = 0x05

BG = "#0b0f14"
PANEL = "#10161e"
LINE = "#1e2836"
FG = "#d7dee8"
DIM = "#5f6f80"
ACC = "#35c2d4"
OK = "#3ddc97"
WARN = "#e0a33c"
CRIT = "#e05a5a"

_DATA_DIR = os.environ.get("POWERSHARE_DATA") or os.path.expanduser("~")
STATE_FILE = os.path.join(_DATA_DIR, "displays.json")

# ---------------- 结构体 ----------------

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_dxva2 = ctypes.WinDLL("dxva2")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

RECT = wintypes.RECT
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     POINTER(RECT), wintypes.LPARAM)


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class REGION2D(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class POINTL(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECTL(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT), ("modeInfoIdx", wintypes.UINT),
                ("outputTechnology", wintypes.UINT), ("rotation", wintypes.UINT),
                ("scaling", wintypes.UINT), ("refreshRate", RATIONAL),
                ("scanLineOrdering", wintypes.UINT), ("targetAvailable", wintypes.BOOL),
                ("statusFlags", wintypes.UINT)]


class PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", SOURCE_INFO), ("targetInfo", TARGET_INFO), ("flags", wintypes.UINT)]


class VSIGNAL(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_uint64), ("hSyncFreq", RATIONAL), ("vSyncFreq", RATIONAL),
                ("activeSize", REGION2D), ("totalSize", REGION2D),
                ("videoStandard", wintypes.UINT), ("scanLineOrdering", wintypes.UINT)]


class TARGET_MODE(ctypes.Structure):
    _fields_ = [("targetVideoSignalInfo", VSIGNAL)]


class SOURCE_MODE(ctypes.Structure):
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT),
                ("pixelFormat", wintypes.UINT), ("position", POINTL)]


class DESKTOP_IMAGE_INFO(ctypes.Structure):
    _fields_ = [("PathSourceSize", POINTL), ("DesktopImageRegion", RECTL),
                ("DesktopImageClip", RECTL)]


class _MODE_UNION(ctypes.Union):
    _fields_ = [("targetMode", TARGET_MODE), ("sourceMode", SOURCE_MODE),
                ("desktopImageInfo", DESKTOP_IMAGE_INFO), ("raw", ctypes.c_ubyte * 48)]


class MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", LUID), ("u", _MODE_UNION)]


class DEVINFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", LUID), ("id", wintypes.UINT)]


class TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT), ("edidManufactureId", wintypes.WORD),
                ("edidProductCodeId", wintypes.WORD), ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]


class SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class TARGET_PREFERRED_MODE(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("width", wintypes.UINT),
                ("height", wintypes.UINT), ("targetMode", TARGET_MODE)]


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE),
                ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]


_u32.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT, POINTER(wintypes.UINT), POINTER(wintypes.UINT)]
_u32.QueryDisplayConfig.argtypes = [wintypes.UINT, POINTER(wintypes.UINT), POINTER(PATH_INFO),
                                    POINTER(wintypes.UINT), POINTER(MODE_INFO), ctypes.c_void_p]
_u32.SetDisplayConfig.argtypes = [wintypes.UINT, POINTER(PATH_INFO), wintypes.UINT,
                                  POINTER(MODE_INFO), wintypes.UINT]
_u32.SetDisplayConfig.restype = wintypes.LONG
_u32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
_u32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG
_u32.EnumDisplayMonitors.restype = wintypes.BOOL
_u32.EnumDisplayMonitors.argtypes = [wintypes.HDC, POINTER(RECT), MONITORENUMPROC, wintypes.LPARAM]
_u32.GetMonitorInfoW.restype = wintypes.BOOL
_u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, POINTER(MONITORINFOEX)]
_u32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                     POINTER(wintypes.WPARAM)]
_dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
_dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, POINTER(wintypes.DWORD)]
_dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
_dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, wintypes.DWORD,
                                                   POINTER(PHYSICAL_MONITOR)]
_dxva2.DestroyPhysicalMonitors.restype = wintypes.BOOL
_dxva2.DestroyPhysicalMonitors.argtypes = [wintypes.DWORD, POINTER(PHYSICAL_MONITOR)]
_dxva2.CapabilitiesRequestAndCapabilitiesReply.restype = wintypes.BOOL
_dxva2.CapabilitiesRequestAndCapabilitiesReply.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD]
_dxva2.SetVCPFeature.restype = wintypes.BOOL
_dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, wintypes.BYTE, wintypes.DWORD]
try:
    _GetVCP = _dxva2.GetVCPFeatureAndVCPFeatureVCPCode
except AttributeError:
    _GetVCP = _dxva2.GetVCPFeatureAndVCPFeatureReply
_GetVCP.restype = wintypes.BOOL
_GetVCP.argtypes = [wintypes.HANDLE, wintypes.BYTE, POINTER(wintypes.DWORD),
                    POINTER(wintypes.DWORD), POINTER(wintypes.DWORD)]


def _read_vcp(h, code):
    vct, cur, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if _GetVCP(h, code, byref(vct), byref(cur), byref(mx)):
        return cur.value, mx.value, vct.value
    return None


# ---------------- 查询 ----------------

def _query():
    np, nm = wintypes.UINT(), wintypes.UINT()
    if _u32.GetDisplayConfigBufferSizes(QDC_ALL_PATHS, byref(np), byref(nm)) != 0:
        return None, 0, None, 0
    paths = (PATH_INFO * np.value)()
    modes = (MODE_INFO * nm.value)()
    rc = _u32.QueryDisplayConfig(QDC_ALL_PATHS, byref(np), paths, byref(nm), modes, None)
    if rc != 0:
        return None, 0, None, 0
    return paths, np.value, modes, nm.value


def _gdi_name(p):
    sn = SOURCE_DEVICE_NAME()
    sn.header.type = GET_SOURCE_NAME
    sn.header.size = sizeof(SOURCE_DEVICE_NAME)
    sn.header.adapterId = p.sourceInfo.adapterId
    sn.header.id = p.sourceInfo.id
    if _u32.DisplayConfigGetDeviceInfo(byref(sn)) == 0:
        return sn.viewGdiDeviceName
    return ""


def _target_name(adapter, tid):
    tn = TARGET_DEVICE_NAME()
    tn.header.type = GET_TARGET_NAME
    tn.header.size = sizeof(TARGET_DEVICE_NAME)
    tn.header.adapterId = adapter
    tn.header.id = tid
    if _u32.DisplayConfigGetDeviceInfo(byref(tn)) == 0:
        return tn.monitorFriendlyDeviceName, tn.monitorDevicePath, tn.outputTechnology
    return "", "", 0


def _preferred(adapter, tid):
    pf = TARGET_PREFERRED_MODE()
    pf.header.type = GET_TARGET_PREFERRED_MODE
    pf.header.size = sizeof(TARGET_PREFERRED_MODE)
    pf.header.adapterId = adapter
    pf.header.id = tid
    if _u32.DisplayConfigGetDeviceInfo(byref(pf)) != 0:
        return None
    return pf


def _monitor_rects():
    """{gdi名: (x,y,w,h,is_primary, hmon)}"""
    out = {}
    hmons = []

    def _cb(h, hdc, r, l):
        hmons.append(h)
        return True

    _u32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    for hm in hmons:
        mi = MONITORINFOEX()
        mi.cbSize = sizeof(MONITORINFOEX)
        if _u32.GetMonitorInfoW(hm, byref(mi)):
            r = mi.rcMonitor
            out[mi.szDevice] = (r.left, r.top, r.right - r.left, r.bottom - r.top,
                                bool(mi.dwFlags & 1), hm)
    return out


def _ddc_state(gdi):
    """返回 (supported, model, power)；不支持 DDC 时 supported=False"""
    info = _monitor_rects().get(gdi)
    if not info:
        return False, "", None
    hm = info[5]
    n = wintypes.DWORD()
    if not _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hm, byref(n)) or not n.value:
        return False, "", None
    arr = (PHYSICAL_MONITOR * n.value)()
    if not _dxva2.GetPhysicalMonitorsFromHMONITOR(hm, n.value, arr):
        return False, "", None
    h = arr[0].hPhysicalMonitor
    supported = False
    model = ""
    power = None
    buf = ctypes.create_string_buffer(2048)
    if _dxva2.CapabilitiesRequestAndCapabilitiesReply(h, buf, 2048):
        caps = buf.value.decode("ascii", "ignore")
        m = re.search(r"model\(([^)]*)\)", caps)
        if m:
            model = m.group(1).strip()
        supported = "D6" in caps
    rv = _read_vcp(h, VCP_POWER)
    if rv:
        power = 1 if rv[0] in (1, 2, 3) else 0
    marr = (PHYSICAL_MONITOR * n.value)()
    for i in range(n.value):
        marr[i] = arr[i]
    _dxva2.DestroyPhysicalMonitors(wintypes.DWORD(n.value), marr)
    return supported, model, power


def list_displays():
    """所有物理连接的显示器（含已断开）。
    返回 dict 列表：key/name/model/active/available/x/y/w/h/gdi/ddc/power/tech
    """
    paths, np, modes, nm = _query()
    if paths is None:
        return []
    rects = _monitor_rects()
    saved_map = _load_state()
    seen = {}
    order = []
    for i in range(np):
        p = paths[i]
        t = p.targetInfo
        k = (t.adapterId.LowPart, t.adapterId.HighPart, t.id)
        if k not in seen:
            seen[k] = {"key": "%d:%d" % (t.adapterId.LowPart, t.id),
                       "adapter_low": t.adapterId.LowPart,
                       "adapter_high": t.adapterId.HighPart,
                       "tid": t.id, "active": False, "available": False,
                       "gdi": "", "rect": None, "tech": t.outputTechnology}
            order.append(k)
        d = seen[k]
        if t.targetAvailable:
            d["available"] = True
        if p.flags & PATH_ACTIVE:
            d["active"] = True
            d["gdi"] = _gdi_name(p) or d["gdi"]

    out = []
    for k in order:
        d = seen[k]
        if not d["available"]:
            continue
        name, mpath, tech = _target_name(LUID(d["adapter_low"], d["adapter_high"]), d["tid"])
        d["name"] = name or "显示器"
        d["device_path"] = mpath
        d["tech"] = tech
        d["ddc"] = False
        d["power"] = None
        if d["active"] and d["gdi"] in rects:
            x, y, w, h, pri, _hm = rects[d["gdi"]]
            d["rect"] = (x, y, w, h)
            d["primary"] = pri
            sup, model, power = _ddc_state(d["gdi"])
            d["ddc"], d["power"] = sup, power
            if not d["name"] or d["name"] == "显示器":
                if model:
                    d["name"] = model
        else:
            d["primary"] = False
            sv = saved_map.get(str(d["tid"]))
            if sv and len(sv) >= 4:
                d["rect"] = tuple(sv[:4])
        out.append(d)
    out.sort(key=lambda d: (0 if d["active"] else 1,
                            d["rect"][0] if d["rect"] else 99999,
                            d["rect"][1] if d["rect"] else 99999))
    return out


# ---------------- 动作 ----------------

def _apply(paths, np, modes, nm):
    rc = _u32.SetDisplayConfig(np, paths, nm, modes,
                               SDC_APPLY | SDC_USE_SUPPLIED | SDC_ALLOW_CHANGES | SDC_SAVE)
    return rc == 0, rc


def detach(key):
    """断开指定显示器（从桌面移除，鼠标不会移过去）"""
    paths, np, modes, nm = _query()
    if paths is None:
        return False, "无法读取显示配置"
    active_cnt = sum(1 for i in range(np) if paths[i].flags & PATH_ACTIVE)
    if active_cnt <= 1:
        return False, "只剩最后一台，不能断开"
    low, tid = [int(x) for x in key.split(":")]
    hit = 0
    for i in range(np):
        t = paths[i].targetInfo
        if t.adapterId.LowPart == low and t.id == tid and (paths[i].flags & PATH_ACTIVE):
            paths[i].flags &= ~PATH_ACTIVE
            hit += 1
    if not hit:
        return False, "该显示器当前未连接"
    ok, rc = _apply(paths, np, modes, nm)
    time.sleep(0.5)
    return ok, ("已断开" if ok else "断开失败 rc=%d" % rc)


def attach(key):
    """重新连接指定显示器：取首选模式重建 path 后激活"""
    paths, np, modes, nm = _query()
    if paths is None:
        return False, "无法读取显示配置"
    low, tid = [int(x) for x in key.split(":")]
    tpl = None
    active_src = set()
    used = []
    for i in range(np):
        t = paths[i].targetInfo
        if paths[i].flags & PATH_ACTIVE:
            active_src.add(paths[i].sourceInfo.id)
            used.append(paths[i])
        if t.adapterId.LowPart == low and t.id == tid and t.targetAvailable and tpl is None:
            tpl = paths[i]
    if tpl is None:
        return False, "该显示器不在线（请检查线缆）"
    lowp = str(tid)
    pref = _preferred(tpl.targetInfo.adapterId, tid)
    if pref is None:
        return False, "取不到该显示器的首选模式"

    free_src = next((s for s in range(8) if s not in active_src), None)
    if free_src is None:
        return False, "没有可用的显示源"

    saved = _load_state().get(lowp)
    w, h = pref.width, pref.height
    if saved and len(saved) >= 2:
        px, py = saved[0], saved[1]
    else:
        xs = [p[0] for p in (_rect_of(p) for p in used) if p is not None]
        px = (min(xs) - w) if xs else 0
        py = 0

    new_nm = nm + 2
    new_modes = (MODE_INFO * new_nm)()
    for i in range(nm):
        memmove(byref(new_modes[i]), byref(modes[i]), sizeof(MODE_INFO))

    sm = new_modes[nm]
    sm.infoType = 1
    sm.id = free_src
    sm.adapterId = tpl.targetInfo.adapterId
    sm.u.sourceMode.width = w
    sm.u.sourceMode.height = h
    sm.u.sourceMode.pixelFormat = 0
    sm.u.sourceMode.position.x = px
    sm.u.sourceMode.position.y = py

    tm = new_modes[nm + 1]
    tm.infoType = 2
    tm.id = tid
    tm.adapterId = tpl.targetInfo.adapterId
    memmove(byref(tm.u.targetMode), byref(pref.targetMode), sizeof(TARGET_MODE))

    new_np = len(used) + 1
    new_paths = (PATH_INFO * new_np)()
    for k, p in enumerate(used):
        memmove(byref(new_paths[k]), byref(p), sizeof(PATH_INFO))
    p = new_paths[len(used)]
    p.sourceInfo.adapterId = tpl.targetInfo.adapterId
    p.sourceInfo.id = free_src
    p.sourceInfo.modeInfoIdx = nm
    p.sourceInfo.statusFlags = 0
    p.targetInfo = tpl.targetInfo
    p.targetInfo.modeInfoIdx = nm + 1
    p.targetInfo.statusFlags = 0
    p.flags = PATH_ACTIVE

    ok, rc = _apply(new_paths, new_np, new_modes, new_nm)
    time.sleep(0.8)
    return ok, ("已连接" if ok else "连接失败 rc=%d" % rc)


def _rect_of(p):
    gdi = _gdi_name(p)
    r = _monitor_rects().get(gdi)
    return (r[0], r[1], r[2], r[3]) if r else None


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(displays):
    try:
        old = _load_state()
        for d in displays:
            if d["active"] and d["rect"]:
                old[str(d["tid"])] = list(d["rect"])
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(old, f)
    except Exception:
        pass


def set_ddc(gdi, on):
    """DDC 关/开背光（不改变桌面布局）"""
    info = _monitor_rects().get(gdi)
    if not info:
        return False
    hm = info[5]
    n = wintypes.DWORD()
    if not _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hm, byref(n)) or not n.value:
        return False
    arr = (PHYSICAL_MONITOR * n.value)()
    if not _dxva2.GetPhysicalMonitorsFromHMONITOR(hm, n.value, arr):
        return False
    ok = bool(_dxva2.SetVCPFeature(arr[0].hPhysicalMonitor, VCP_POWER,
                                   PWR_ON if on else PWR_OFF_SOFT))
    marr = (PHYSICAL_MONITOR * n.value)()
    for i in range(n.value):
        marr[i] = arr[i]
    _dxva2.DestroyPhysicalMonitors(wintypes.DWORD(n.value), marr)
    return ok


def dpms(off=True):
    res = wintypes.WPARAM()
    _u32.SendMessageTimeoutW(wintypes.HWND(0xFFFF), 0x0112, 0xF170, 2 if off else -1,
                             0x0002, 500, byref(res))
    if not off:
        keep_display_awake(1.0)


def keep_display_awake(seconds=1.0):
    ES_CONTINUOUS = 0x80000000
    ES_DISPLAY_REQUIRED = 0x00000002
    try:
        _k32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
    except Exception:
        pass
    time.sleep(max(0.2, seconds))
    try:
        _k32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass


def nudge_mouse():
    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
    p = POINT()
    _u32.GetCursorPos(byref(p))
    _u32.SetCursorPos(p.x + 1, p.y)
    time.sleep(0.05)
    _u32.SetCursorPos(p.x, p.y)


MODE_LABEL = {
    "internal": "仅内屏 INTERNAL",
    "external": "仅外屏 EXTERNAL",
    "extend": "扩展 EXTEND",
    "clone": "复制 CLONE",
}


def switch_mode(mode):
    if mode not in MODE_LABEL:
        return False, "未知模式"
    try:
        r = subprocess.run([r"C:\Windows\System32\DisplaySwitch.exe", "/" + mode],
                           timeout=15, creationflags=0x08000000)
        time.sleep(0.8)
        return (r.returncode == 0), MODE_LABEL[mode]
    except Exception as e:
        return False, "切换失败：%s" % e


# ---------------- 界面 ----------------

class DisplayWindow(tk.Toplevel):
    def __init__(self, master):
        tk.Toplevel.__init__(self, master)
        self.title("显示器 DISPLAY · 点击断开/连接")
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(780, sw - 80), min(600, sh - 100)
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, 70))
        self.minsize(600, 480)

        self.disp = []
        self.tiles = []
        self.chips = []
        self._hover = None
        self._busy = False

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(head, text="显示器 DISPLAY", bg=BG, fg=FG,
                 font=("Consolas", 13, "bold")).pack(side="left")
        tk.Label(head, text="点方块 = 断开 / 再点同一块 = 连接", bg=BG, fg=DIM,
                 font=("Consolas", 8)).pack(side="left", padx=(10, 0))
        tk.Button(head, text="刷新 REFRESH", bg=PANEL, fg=ACC,
                  activebackground="#16232c", activeforeground=ACC,
                  font=("Consolas", 8), bd=0, relief="flat",
                  highlightthickness=0, command=self.refresh).pack(side="right")

        self.cv = tk.Canvas(self, bg=PANEL, highlightthickness=0)
        self.cv.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        self.cv.bind("<Configure>", lambda e: self._draw())
        self.cv.bind("<Button-1>", self._on_click)
        self.cv.bind("<Motion>", self._on_motion)
        self.cv.bind("<Button-3>", self._on_right)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14)
        for c in range(4):
            bar.columnconfigure(c, weight=1, uniform="m")
        for i, k in enumerate(("internal", "external", "extend", "clone")):
            b = tk.Button(bar, text=MODE_LABEL[k], bg=PANEL, fg=ACC,
                          activebackground="#16232c", activeforeground=ACC,
                          font=("Consolas", 9, "bold"), bd=0, relief="flat",
                          highlightthickness=0, command=lambda kk=k: self.do_mode(kk))
            b.grid(row=0, column=i, sticky="ew", padx=2, pady=2, ipady=4)

        bar2 = tk.Frame(self, bg=BG)
        bar2.pack(fill="x", padx=14)
        for c in range(2):
            bar2.columnconfigure(c, weight=1, uniform="m")
        tk.Button(bar2, text="只关背光（布局不变）BLANK", bg=PANEL, fg=WARN,
                  activebackground="#16232c", activeforeground=WARN,
                  font=("Consolas", 9), bd=0, relief="flat", highlightthickness=0,
                  command=self.blank_all).grid(row=0, column=0, sticky="ew", padx=2, pady=2, ipady=4)
        tk.Button(bar2, text="点亮全部（背光+DPMS）ON", bg=PANEL, fg=OK,
                  activebackground="#16232c", activeforeground=OK,
                  font=("Consolas", 9), bd=0, relief="flat", highlightthickness=0,
                  command=self.wake_all).grid(row=0, column=1, sticky="ew", padx=2, pady=2, ipady=4)

        self.lbl_msg = tk.Label(self, text="", bg=BG, fg=DIM, font=("Consolas", 8),
                                anchor="w", justify="left")
        self.lbl_msg.pack(fill="x", padx=14, pady=(2, 8))

        self.bind("<Escape>", lambda e: self.destroy())
        self.after(80, self.refresh)

    # ---------- 数据 ----------
    def refresh(self):
        try:
            self.disp = list_displays()
        except Exception as e:
            self._msg("枚举失败：%s" % e, CRIT)
            return
        save_state(self.disp)
        self._draw()
        on = sum(1 for d in self.disp if d["active"])
        off = len(self.disp) - on
        self._msg("已连接 %d 台 · 已断开 %d 台" % (on, off), DIM)

    def _msg(self, text, color=DIM):
        try:
            self.lbl_msg.config(text=text, fg=color)
        except Exception:
            pass

    # ---------- 绘制 ----------
    def _draw(self):
        c = self.cv
        c.delete("all")
        self.tiles = []
        self.chips = []
        shown = [d for d in self.disp if d["rect"]]
        cw = max(240, c.winfo_width())
        ch = max(160, c.winfo_height())
        if not shown:
            c.create_text(cw // 2, ch // 2, text="未检测到显示器", fill=DIM,
                          font=("Consolas", 10))
            return

        x0 = min(d["rect"][0] for d in shown)
        y0 = min(d["rect"][1] for d in shown)
        bw = max(1, max(d["rect"][0] + d["rect"][2] for d in shown) - x0)
        bh = max(1, max(d["rect"][1] + d["rect"][3] for d in shown) - y0)
        pad = 22
        scale = min((cw - pad * 2) / bw, (ch - pad * 2) / bh)
        offx = (cw - bw * scale) / 2 - x0 * scale
        offy = (ch - bh * scale) / 2 - y0 * scale

        for idx, d in enumerate(shown, 1):
            x, y, w, h = d["rect"]
            x1, y1 = x * scale + offx, y * scale + offy
            x2, y2 = x1 + w * scale, y1 + h * scale
            active = d["active"]
            hover = (self._hover == d["key"])
            if active:
                fill = "#0f2a22"
                outline = ACC if hover else OK
                num_col, name_col = FG, DIM
            else:
                fill = "#0e1216"
                outline = ACC if hover else "#3a4652"
                num_col, name_col = "#3f4b57", "#41505c"
            rect = c.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=2)
            if not active:
                c.itemconfig(rect, dash=(5, 4))
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            c.create_text(cx, cy - 12, text=str(idx), fill=num_col,
                          font=("Consolas", 22, "bold"))
            c.create_text(cx, cy + 12, text=d["name"][:14], fill=name_col,
                          font=("Consolas", 8))
            c.create_text(x1 + 8, y1 + 10, text="%dx%d" % (w, h), anchor="w",
                          fill=name_col, font=("Consolas", 7))
            c.create_text(x2 - 8, y2 - 10, anchor="e",
                          text=("● 已连接" if active else "○ 已断开 · 点击连接"),
                          fill=(OK if active else "#6b7d8c"), font=("Consolas", 7))
            if active and d.get("primary"):
                c.create_text(x1 + 8, y2 - 10, anchor="w", text="主 PRIMARY",
                              fill=ACC, font=("Consolas", 7))
            self.tiles.append((d["key"], x1, y1, x2, y2))

        off = sum(1 for d in shown if not d["active"])
        if off:
            c.create_text(8, 8, anchor="w",
                          text="虚线框 = 已断开，点击即可重新连接（%d 台）" % off,
                          fill=DIM, font=("Consolas", 7))

    def _hit(self, x, y):
        for key, a, b, c2, d2 in self.tiles:
            if a <= x <= c2 and b <= y <= d2:
                return key, "tile"
        return None, None

    def _on_motion(self, e):
        key, _ = self._hit(e.x, e.y)
        if key != self._hover:
            self._hover = key
            self.cv.config(cursor="hand2" if key else "")
            self._draw()

    def _on_click(self, e):
        if self._busy:
            return
        key, kind = self._hit(e.x, e.y)
        if not key:
            return
        d = next((x for x in self.disp if x["key"] == key), None)
        if d is None:
            return
        if d["active"]:
            self.do_detach(key)
        else:
            self.do_attach(key)

    def _on_right(self, e):
        key, kind = self._hit(e.x, e.y)
        if not key or kind != "tile":
            return
        d = next((x for x in self.disp if x["key"] == key), None)
        if not d:
            return
        if not d["active"]:
            self._msg("该屏当前已断开，先点它连回来", WARN)
            return
        if not d["ddc"]:
            self._msg("该屏不支持 DDC 关背光", WARN)
            return
        set_ddc(d["gdi"], False)
        self._msg("%s 已关背光（布局不变）· 点「点亮全部」恢复" % d["name"], WARN)

    # ---------- 动作 ----------
    def do_detach(self, key):
        d = next((x for x in self.disp if x["key"] == key), None)
        if not d:
            return
        if sum(1 for x in self.disp if x["active"]) <= 1:
            self._msg("只剩最后一台，不能断开", CRIT)
            return
        self._busy = True
        try:
            ok, msg = detach(key)
            self.refresh()
            if ok:
                self._msg("%s %s · 鼠标不会再移到那个区域" % (d["name"], msg), OK)
                self._ensure_visible(d)
            else:
                self._msg("%s %s" % (d["name"], msg), CRIT)
        finally:
            self._busy = False

    def do_attach(self, key):
        self._busy = True
        self._msg("正在连接…", DIM)
        self.update_idletasks()
        try:
            ok, msg = attach(key)
            self.refresh()
            self._msg(msg if ok else msg, OK if ok else CRIT)
        finally:
            self._busy = False

    def blank_all(self):
        self._busy = True
        try:
            dpms(True)
            self._msg("已关背光（布局不变，鼠标仍可移动）· 动一下鼠标即恢复", WARN)
        finally:
            self._busy = False

    def wake_all(self):
        self._busy = True
        try:
            dpms(False)
            nudge_mouse()
            time.sleep(0.4)
            for d in self.disp:
                if d["active"] and d["ddc"]:
                    set_ddc(d["gdi"], True)
            self._msg("已点亮全部", OK)
        finally:
            self._busy = False

    def do_mode(self, mode):
        ok, msg = switch_mode(mode)
        self._msg(("已切换：" if ok else "切换失败：") + msg, OK if ok else CRIT)
        self.after(900, lambda: (self.refresh(),
                                 self._msg(("已切换：" if ok else "切换失败：") + msg,
                                           OK if ok else CRIT)))

    def _ensure_visible(self, gone):
        try:
            wx, wy = self.winfo_x(), self.winfo_y()
            r = gone.get("rect")
            if not r:
                return
            if not (r[0] <= wx < r[0] + r[2] and r[1] <= wy < r[1] + r[3]):
                return
            alive = [d for d in self.disp if d["active"] and d["rect"]]
            if not alive:
                return
            tgt = next((d for d in alive if d["primary"]), alive[0])
            self.geometry("+%d+%d" % (tgt["rect"][0] + 60, tgt["rect"][1] + 70))
        except Exception:
            pass


def _main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for i, d in enumerate(list_displays()):
            print("[%d] %-22s active=%-5s %s ddc=%s power=%s" % (
                i + 1, d["name"][:22], d["active"],
                ("%dx%d @(%d,%d)" % (d["rect"][2], d["rect"][3], d["rect"][0], d["rect"][1]))
                if d["rect"] else "—", d["ddc"], d["power"]))
        return
    if len(sys.argv) > 2 and sys.argv[1] in ("detach", "attach"):
        fn = detach if sys.argv[1] == "detach" else attach
        print(fn(sys.argv[2]))
        return
    root = tk.Tk()
    root.withdraw()
    DisplayWindow(root)
    root.mainloop()


if __name__ == "__main__":
    _main()
