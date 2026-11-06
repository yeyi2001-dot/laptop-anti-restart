# -*- coding: utf-8 -*-
"""功耗与显示器工具 · 分享版

设计原则（与作者自用版的区别）：
  1. **零系统侵入**：不写注册表、不建计划任务、不需要管理员、不自动开机启动
  2. **零硬编码路径**：数据全部落在 %LOCALAPPDATA%\\PowerShare
  3. **保温功能默认关闭**，必须手动开启，且有温度/电源双重保险
  4. 没有 NVIDIA 显卡也能用（功耗区降级显示）
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

APP_NAME = "功耗与显示器工具 · 分享版"

# ---------- 数据目录（必须在导入子模块之前设置） ----------
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "PowerShare")
LOG_DIR = os.path.join(DATA_DIR, "powerlog")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    pass
os.environ["POWERSHARE_DATA"] = DATA_DIR

try:
    from display_ctl import DisplayWindow
except Exception:
    DisplayWindow = None
try:
    from power_fault_view import FaultWindow
except Exception:
    FaultWindow = None

# ---------- 主题 ----------
BG = "#0b0f14"
PANEL = "#10161e"
CARD = "#0d141b"
LINE = "#1e2836"
FG = "#d7dee8"
DIM = "#5f6f80"
FAINT = "#3c4753"
ACC = "#35c2d4"
OK = "#3ddc97"
WARN = "#e0a33c"
CRIT = "#e05a5a"

PWR_WARN, PWR_CRIT = 100.0, 130.0
LOG_EVERY = 5.0
KEEP_DAYS = 7
CREATE_NO_WINDOW = 0x08000000

K32 = ctypes.WinDLL("kernel32", use_last_error=True)
POWR = ctypes.WinDLL("powrprof")


class BATTERY_STATE(ctypes.Structure):
    _fields_ = [
        ("AcOnLine", ctypes.c_ubyte), ("BatteryPresent", ctypes.c_ubyte),
        ("Charging", ctypes.c_ubyte), ("Discharging", ctypes.c_ubyte),
        ("Spare1", ctypes.c_ubyte * 3),
        ("MaxCapacity", ctypes.c_ulong), ("RemainingCapacity", ctypes.c_ulong),
        ("Rate", ctypes.c_ulong), ("EstimatedTime", ctypes.c_ulong),
        ("DefaultAlert1", ctypes.c_ulong), ("DefaultAlert2", ctypes.c_ulong),
    ]


def read_gpu_power():
    """(功耗W, 温度C, 占用%)；无 NVIDIA 时返回 None"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            p = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            return float(p[0]), float(p[1]), float(p[2])
    except Exception:
        pass
    return None


def read_batt_power():
    try:
        s = BATTERY_STATE()
        POWR.CallNtPowerInformation(5, None, 0, ctypes.byref(s), ctypes.sizeof(s))
        raw = s.Rate / 1000.0
        if s.Discharging and raw > 0:
            rate = -raw
        elif s.Charging and raw > 0:
            rate = raw
        else:
            rate = 0.0
        return {"ac": bool(s.AcOnLine), "chg": bool(s.Charging),
                "dis": bool(s.Discharging), "rate_w": rate,
                "present": bool(s.BatteryPresent)}
    except Exception:
        return None


def cpu_peak_w():
    """按 CPU 型号粗略估一个满载功耗增量（瓦）。"""
    name = ""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
    except Exception:
        pass
    u = (name or "").upper()
    peak = 45.0
    if u.rstrip().endswith("U") or " U " in u:
        peak = 25.0
    elif "HX" in u:
        peak = 55.0
    elif u.rstrip().endswith("HS"):
        peak = 35.0
    elif u.rstrip().endswith("H"):
        peak = 45.0
    return peak, (name.strip() or "CPU")


class Power:
    """功耗采样：GPU 实测 / CPU 推测 / 整机推测（电池放电时改实测）"""
    SAMPLE_EVERY = 2.0
    CPU_IDLE_W = 4.0
    BASE_W = 12.0

    def __init__(self):
        self.gpu = None
        self.batt = None
        self.hist = []
        self.peak = 0.0
        self.cpu_pct = 0.0
        self.cpu_w = 0.0
        self.total_w = 0.0
        self.total_measured = False
        self.cpu_peak, self.cpu_name = cpu_peak_w()
        self._prev = None
        self._last = 0.0
        self._last_log = 0.0
        self._last_clean = 0.0
        self._gpu_ok = True

    def _cpu_pct(self):
        try:
            idle = ctypes.c_ulonglong(0)
            kern = ctypes.c_ulonglong(0)
            user = ctypes.c_ulonglong(0)
            K32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            cur = (idle.value, kern.value, user.value)
            if self._prev:
                di = cur[0] - self._prev[0]
                dt = (cur[1] - self._prev[1]) + (cur[2] - self._prev[2])
                if dt > 0:
                    self.cpu_pct = max(0.0, min(100.0, 100.0 * (dt - di) / dt))
            self._prev = cur
        except Exception:
            pass

    def _estimate(self):
        frac = self.cpu_pct / 100.0
        self.cpu_w = self.CPU_IDLE_W + self.cpu_peak * (frac ** 1.2)
        g = self.gpu[0] if self.gpu else 0.0
        self.total_measured = False
        if self.batt and self.batt["dis"] and self.batt["rate_w"] < -1:
            self.total_w = -self.batt["rate_w"]
            self.total_measured = True
        else:
            self.total_w = g + self.cpu_w + self.BASE_W

    def tick(self):
        self._cpu_pct()
        now = time.time()
        if now - self._last >= self.SAMPLE_EVERY:
            self._last = now
            g = read_gpu_power()
            self._gpu_ok = g is not None
            if g is not None:
                self.gpu = g
                self.hist.append(g[0])
                if len(self.hist) > 90:
                    self.hist.pop(0)
                self.peak = max(self.peak, g[0])
            self.batt = read_batt_power()
        self._estimate()
        self._write_log(now)

    def _path(self):
        return os.path.join(LOG_DIR, "power_%s.csv" % time.strftime("%Y-%m-%d"))

    def _write_log(self, now):
        if now - self._last_log < LOG_EVERY:
            return
        self._last_log = now
        try:
            if not os.path.isdir(LOG_DIR):
                os.makedirs(LOG_DIR, exist_ok=True)
            p = self._path()
            new = not os.path.exists(p)
            g = self.gpu or (0.0, 0.0, 0.0)
            b = self.batt or {}
            with open(p, "a", encoding="utf-8", newline="") as f:
                if new:
                    f.write("time,gpu_w,gpu_c,gpu_util,cpu_pct,cpu_w,total_w,"
                            "total_src,batt_w,ac\n")
                f.write("%s,%.1f,%.0f,%.0f,%.1f,%.1f,%.1f,%s,%.1f,%d\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), g[0], g[1], g[2],
                    self.cpu_pct, self.cpu_w, self.total_w,
                    "batt" if self.total_measured else "est",
                    b.get("rate_w", 0.0), 1 if b.get("ac") else 0))
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass
        if now - self._last_clean > 3600:
            self._last_clean = now
            cut = now - KEEP_DAYS * 86400
            try:
                for n in os.listdir(LOG_DIR):
                    fp = os.path.join(LOG_DIR, n)
                    if os.path.isfile(fp) and os.path.getmtime(fp) < cut:
                        os.remove(fp)
            except Exception:
                pass


class KeepWarm:
    """单核保温：默认关闭，需手动开启；有暂停标志供温度/电源守卫使用。"""
    def __init__(self):
        self.on = False
        self.paused = False
        self.t0 = 0.0
        self.note = ""
        self._th = None

    def start(self):
        if self.on:
            return
        self.on = True
        self.paused = False
        self.t0 = time.time()
        self.note = ""
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def stop(self):
        self.on = False
        self.paused = False

    def _run(self):
        try:
            K32.SetThreadPriority(K32.GetCurrentThread(), -1)
        except Exception:
            pass
        while self.on:
            if self.paused:
                time.sleep(0.15)
                continue
            t = time.perf_counter()
            while (time.perf_counter() - t) < 0.015 and self.on and not self.paused:
                pass
            time.sleep(0.0008)


def _fmt_span(sec):
    sec = int(sec)
    if sec < 60:
        return "%d 秒" % sec
    if sec < 3600:
        return "%d 分 %d 秒" % (sec // 60, sec % 60)
    return "%d 小时 %d 分" % (sec // 3600, (sec % 3600) // 60)


class HistoryWindow(tk.Toplevel):
    """内置历史曲线（不依赖任何外部脚本）"""
    RANGES = [("6 小时", 6 * 3600), ("24 小时", 24 * 3600),
              ("3 天", 3 * 86400), ("7 天", 7 * 86400)]

    def __init__(self, master):
        tk.Toplevel.__init__(self, master)
        self.title("功耗历史 POWER HISTORY")
        self.configure(bg=BG)
        self.geometry("720x460")
        self.minsize(560, 380)
        self.rows = []
        self.span = 24 * 3600

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="功耗历史 POWER HISTORY", bg=BG, fg=FG,
                 font=("Consolas", 12, "bold")).pack(side="left")
        for txt, sp in self.RANGES:
            tk.Button(head, text=txt, bg=PANEL, fg=ACC, activebackground="#16232c",
                      activeforeground=ACC, font=("Consolas", 8), bd=0, relief="flat",
                      highlightthickness=0,
                      command=lambda t=txt, s=sp: self.load(s, t)).pack(side="right", padx=2)

        self.lbl = tk.Label(self, text="", bg=BG, fg=DIM, font=("Consolas", 8), anchor="w")
        self.lbl.pack(fill="x", padx=14)

        self.cv = tk.Canvas(self, bg=PANEL, highlightthickness=0)
        self.cv.pack(fill="both", expand=True, padx=14, pady=8)
        self.cv.bind("<Configure>", lambda e: self.draw())

        tk.Label(self, text="GPU 实测 · CPU/整机 为推测值 · 曲线取每段峰值，不掉尖峰",
                 bg=BG, fg=FAINT, font=("Consolas", 8)).pack(pady=(0, 10))
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(60, lambda: self.load(24 * 3600, "24 小时"))

    def load(self, span, label):
        self.span = span
        self.rows = _read_logs(span)
        if self.rows:
            self.lbl.config(text="区间 %s · %d 个采样点 · 来自 %s" %
                                 (label, len(self.rows), LOG_DIR))
        else:
            self.lbl.config(text="区间 %s · 暂无记录（采集 5 秒一行，稍等片刻即有）" % label)
        self.draw()

    def draw(self):
        c = self.cv
        c.delete("all")
        w = max(300, c.winfo_width())
        h = max(160, c.winfo_height())
        if not self.rows:
            c.create_text(w // 2, h // 2, text="暂无数据", fill=DIM, font=("Consolas", 10))
            return
        top, bot = 14, h - 26
        gmax = max(max(r[1], r[3]) for r in self.rows) or 1.0
        ymax = max(50.0, gmax * 1.1)
        n = len(self.rows)
        bucket = max(1, n // max(1, w - 40))
        pts_g, pts_c, pts_t = [], [], []
        for i in range(0, n, bucket):
            seg = self.rows[i:i + bucket]
            x = 40 + (i / max(1, n - 1)) * (w - 60)
            pts_g.append((x, seg and max(r[1] for r in seg)))
            pts_c.append((x, seg and max(r[2] for r in seg)))
            pts_t.append((x, seg and max(r[3] for r in seg)))
        for lv in (PWR_WARN, PWR_CRIT):
            if lv < ymax:
                y = bot - lv / ymax * (bot - top)
                c.create_line(40, y, w - 20, y, fill="#243040", dash=(3, 3))
                c.create_text(w - 18, y - 6, text="%d" % lv, anchor="e",
                              fill=FAINT, font=("Consolas", 7))
        for pts, col in ((pts_t, "#8f7dff"), (pts_c, ACC), (pts_g, CRIT)):
            flat = []
            for x, v in pts:
                if v is None:
                    continue
                flat.extend([x, bot - min(v, ymax) / ymax * (bot - top)])
            if len(flat) >= 4:
                c.create_line(*flat, fill=col, width=1.4)
        c.create_text(38, top, text="%.0fW" % ymax, anchor="w", fill=FAINT,
                      font=("Consolas", 7))
        c.create_text(38, bot, text="0", anchor="w", fill=FAINT, font=("Consolas", 7))


def _read_logs(span):
    """返回 [(ts, gpu, cpu, total)]，按时间升序"""
    import glob
    out = []
    cut = time.time() - span
    for p in sorted(glob.glob(os.path.join(LOG_DIR, "power_*.csv"))):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                next(f, None)
                for line in f:
                    c = line.rstrip("\n").split(",")
                    if len(c) < 7:
                        continue
                    try:
                        ts = time.mktime(time.strptime(c[0], "%Y-%m-%d %H:%M:%S"))
                        if ts < cut:
                            continue
                        out.append((ts, float(c[1]), float(c[5]), float(c[6])))
                    except Exception:
                        continue
        except Exception:
            continue
    out.sort(key=lambda r: r[0])
    return out


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.configure(bg=BG)
        root.geometry("460x604")
        root.minsize(440, 580)
        root.resizable(False, False)

        self.pow = Power()
        self.keep = KeepWarm()
        self.gpu_ok = True
        self._hist_win = None
        self._disp_win = None
        self._fault_win = None

        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(head, text=APP_NAME, bg=BG, fg=FG,
                 font=("Consolas", 12, "bold")).pack(side="left")
        self.lbl_live = tk.Label(head, text="● 采样中", bg=BG, fg=OK, font=("Consolas", 8))
        self.lbl_live.pack(side="right")

        cards = tk.Frame(root, bg=BG)
        cards.pack(fill="x", padx=12)
        for i in range(3):
            cards.columnconfigure(i, weight=1, uniform="c")
        self.v_gpu, self.s_gpu = self._card(cards, 0, "显卡 GPU · W")
        self.v_cpu, self.s_cpu = self._card(cards, 1, "处理器 CPU · W")
        self.v_tot, self.s_tot = self._card(cards, 2, "整机 TOTAL · W")

        self.cv = tk.Canvas(root, width=428, height=54, bg=PANEL, highlightthickness=0)
        self.cv.pack(pady=(10, 2))

        self.lbl_batt = tk.Label(root, text="电池 BATT  --", bg=BG, fg=DIM,
                                 font=("Consolas", 8), anchor="w")
        self.lbl_batt.pack(fill="x", padx=16, pady=(2, 0))

        tk.Frame(root, bg=LINE, height=1).pack(fill="x", padx=16, pady=(10, 8))

        box = tk.Frame(root, bg=CARD)
        box.pack(fill="x", padx=16)
        inner = tk.Frame(box, bg=CARD)
        inner.pack(fill="x", padx=12, pady=10)
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="保温 KEEP-WARM（可选·默认关闭）", bg=CARD, fg=FG,
                 font=("Consolas", 9, "bold")).pack(side="left")
        self.btn_keep = tk.Button(row, text="开始 START", bg=PANEL, fg=OK,
                                  activebackground="#16232c", activeforeground=OK,
                                  font=("Consolas", 9, "bold"), bd=0, relief="flat",
                                  highlightthickness=0, command=self.toggle_keep)
        self.btn_keep.pack(side="right")
        self.lbl_keep = tk.Label(inner, text="未开启 · 不会占用 CPU", bg=CARD, fg=DIM,
                                 font=("Consolas", 8), anchor="w", justify="left")
        self.lbl_keep.pack(fill="x", pady=(6, 2))
        tk.Label(inner, text="仅对「冷机启动就死机」的机器可能有帮助；"
                             "热机满载崩溃请先查供电与散热。",
                 bg=CARD, fg=FAINT, font=("Microsoft YaHei UI", 8),
                 anchor="w", justify="left", wraplength=400).pack(fill="x")

        btns = tk.Frame(root, bg=BG)
        btns.pack(fill="x", padx=16, pady=(10, 0))
        for i in range(3):
            btns.columnconfigure(i, weight=1, uniform="b")
        for i, (txt, cmd) in enumerate((("显示器 DISPLAY", self.open_display),
                                        ("故障诊断 DIAG", self.open_fault),
                                        ("历史曲线 HISTORY", self.open_hist))):
            tk.Button(btns, text=txt, bg=PANEL, fg=ACC, activebackground="#16232c",
                      activeforeground=ACC, font=("Consolas", 9), bd=0, relief="flat",
                      highlightthickness=0, command=cmd).grid(
                row=0, column=i, sticky="ew", padx=2, ipady=5)

        self.lbl_foot = tk.Label(root, text="数据目录 %s" % DATA_DIR, bg=BG, fg=FAINT,
                                 font=("Consolas", 7), anchor="w")
        self.lbl_foot.pack(fill="x", padx=16, pady=(8, 10))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(500, self.loop)
        root.after(1000, self.guard_loop)

    def _card(self, parent, col, title):
        f = tk.Frame(parent, bg=CARD)
        f.grid(row=0, column=col, sticky="nsew", padx=3, pady=2)
        tk.Label(f, text=title, bg=CARD, fg=DIM, font=("Consolas", 7)).pack(pady=(8, 0))
        v = tk.Label(f, text="—", bg=CARD, fg=FG, font=("Consolas", 20, "bold"))
        v.pack()
        s = tk.Label(f, text=" ", bg=CARD, fg=FAINT, font=("Consolas", 7))
        s.pack(pady=(0, 8))
        return v, s

    # ---------- 主循环 ----------
    def loop(self):
        try:
            self.pow.tick()
            self._draw()
        except Exception:
            pass
        self.root.after(1000, self.loop)

    def _draw(self):
        p = self.pow
        if p.gpu:
            w, c, u = p.gpu
            col = OK if w < PWR_WARN else (WARN if w < PWR_CRIT else CRIT)
            self.v_gpu.config(text="%.0f" % w, fg=col)
            self.s_gpu.config(text="%.0f℃ %.0f%% 峰%.0f" % (c, u, p.peak))
        else:
            self.v_gpu.config(text="—", fg=FAINT)
            self.s_gpu.config(text="无 NVIDIA 显卡")
        self.v_cpu.config(text="%.0f" % p.cpu_w)
        self.s_cpu.config(text="推测 %.0f%%" % p.cpu_pct)
        self.v_tot.config(text="%.0f" % p.total_w,
                          fg=(FG if p.total_w < 170 else (WARN if p.total_w < 190 else CRIT)))
        self.s_tot.config(text="实测 batt" if p.total_measured else "推测 est")

        if p.batt:
            if not p.batt.get("present"):
                self.lbl_batt.config(text="电池 BATT  无电池", fg=FAINT)
            elif abs(p.batt["rate_w"]) < 0.5:
                self.lbl_batt.config(text="电池 BATT   0W · 交流 ac · 电池不出力", fg=DIM)
            elif p.batt["rate_w"] < 0:
                self.lbl_batt.config(text="电池 BATT  %.0fW 放电 discharging" %
                                          (-p.batt["rate_w"]), fg=ACC)
            else:
                self.lbl_batt.config(text="电池 BATT  +%.0fW 充电 charging" %
                                          p.batt["rate_w"], fg="#8f7dff")

        c = self.cv
        c.delete("all")
        cw, chh = 428, 54
        top, bot = 8, chh - 8
        ymax = max(50.0, p.peak * 1.1)
        for lv in (PWR_WARN, PWR_CRIT):
            if lv < ymax:
                y = bot - lv / ymax * (bot - top)
                c.create_line(0, y, cw, y, fill="#1c2733", dash=(3, 3))
        if len(p.hist) >= 2:
            step = cw / 89.0
            col = OK if p.hist[-1] < PWR_WARN else (WARN if p.hist[-1] < PWR_CRIT else CRIT)
            pts = []
            for i, v in enumerate(p.hist):
                pts.extend([i * step, bot - min(v, ymax) / ymax * (bot - top)])
            c.create_line(*pts, fill=col, width=1.6)

    # ---------- 守卫 ----------
    def guard_loop(self):
        """温度 / 电源守卫：只影响保温，不影响采样。"""
        try:
            if self.keep.on:
                g = self.pow.gpu
                batt = self.pow.batt
                if batt and not batt.get("ac"):
                    self.keep.paused = True
                    self.keep.note = "已暂停：正在使用电池"
                elif g and g[1] >= 80:
                    self.keep.paused = True
                    self.keep.note = "已暂停：显卡 %.0f℃ 过高" % g[1]
                else:
                    if self.keep.paused:
                        self.keep.note = ""
                    self.keep.paused = False
                self._draw_keep()
        except Exception:
            pass
        self.root.after(2000, self.guard_loop)

    def _draw_keep(self):
        k = self.keep
        if not k.on:
            self.btn_keep.config(text="开始 START", fg=OK)
            self.lbl_keep.config(text="未开启 · 不会占用 CPU", fg=DIM)
        elif k.paused:
            self.btn_keep.config(text="停止 STOP", fg=CRIT)
            self.lbl_keep.config(text="%s（自动恢复）· 已运行 %s" %
                                       (k.note or "已暂停", _fmt_span(time.time() - k.t0)),
                                 fg=WARN)
        else:
            self.btn_keep.config(text="停止 STOP", fg=CRIT)
            self.lbl_keep.config(text="运行中 · 单核低优先级 · 已 %s" %
                                       _fmt_span(time.time() - k.t0), fg=OK)

    def toggle_keep(self):
        if self.keep.on:
            self.keep.stop()
            self._draw_keep()
            return
        if not messagebox.askokcancel(
                "开启保温",
                "保温会让一个 CPU 核心持续接近满载，用来让机器保持温热。\n\n"
                "· 只对「冷机启动就死机」这种形态可能有帮助\n"
                "· 会持续耗电约 10–20W，笔记本温度长期偏高\n"
                "· 使用电池或显卡超过 80℃ 时会自动暂停\n\n"
                "确定开启吗？",
                parent=self.root):
            return
        self.keep.start()
        self._draw_keep()

    # ---------- 子窗口 ----------
    def open_display(self):
        if DisplayWindow is None:
            messagebox.showwarning("不可用", "缺少 display_ctl.py", parent=self.root)
            return
        try:
            if self._disp_win is not None and self._disp_win.winfo_exists():
                self._disp_win.deiconify()
                self._disp_win.lift()
                self._disp_win.refresh()
                return
        except Exception:
            pass
        self._disp_win = DisplayWindow(self.root)

    def open_fault(self):
        if FaultWindow is None:
            messagebox.showwarning("不可用", "缺少 power_fault_view.py", parent=self.root)
            return
        try:
            if self._fault_win is not None and self._fault_win.winfo_exists():
                self._fault_win.deiconify()
                self._fault_win.lift()
                return
        except Exception:
            pass
        self._fault_win = FaultWindow(self.root)

    def open_hist(self):
        try:
            if self._hist_win is not None and self._hist_win.winfo_exists():
                self._hist_win.deiconify()
                self._hist_win.lift()
                return
        except Exception:
            pass
        self._hist_win = HistoryWindow(self.root)

    def on_close(self):
        try:
            self.keep.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
