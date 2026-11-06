"""电源故障统计面板（可独立运行，也可被保活主程序内嵌调用）

数据源：Windows 系统事件日志
  · Event 41        未正常关机就重启（硬冻结）
  · Kernel-Power 105 电源来源切换（交流 ↔ 电池）
  · Kernel-Power 566 可用电池数量变化
  · 105/566 相邻 ≤120 秒合并为「1 次断连」

用法：
  独立：python power_fault_view.py
  内嵌：from power_fault_view import FaultWindow; FaultWindow(parent)
"""

import bisect
import csv
import datetime as dt
import glob
import os
import re
import subprocess
import time

import tkinter as tk
from tkinter import messagebox

BG = "#0b0f14"
PANEL = "#10161e"
PANEL2 = "#16232c"
LINE = "#1e2836"
FG = "#d7dee8"
DIM = "#5f6f80"
FAINT = "#3f4d5c"
ACC = "#35c2d4"
CRASH = "#e05a5a"
PWR = "#35c2d4"

MERGE_SEC = 120
_ENV_DATA = os.environ.get("POWERSHARE_DATA")
_DEFAULT_DATA = os.path.join(os.path.expanduser("~"), "PowerShare")
OUT_DIR = os.path.join(_ENV_DATA or _DEFAULT_DATA, "powerlog")
PLOG_DIR = OUT_DIR

RANGES = [("今天", 1), ("7 天", 7), ("30 天", 30), ("90 天", 90), ("180 天", 180)]

COLS = 8
ROWS = 6
CELL_H = 46
CELL_GAP = 6


# ---------------------------------------------------------------- 事件读取
def _query(query, count=20000):
    cmd = ["wevtutil", "qe", "System", "/q:" + query, "/c:%d" % count,
           "/rd:false", "/f:text"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300,
                           creationflags=0x08000000)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _parse(txt):
    out, cur = [], None
    for line in txt.splitlines():
        m = re.match(r"\s*Date:\s*(\S+)", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"\s*Event ID:\s*(\d+)", line)
        if m and cur:
            try:
                out.append((time.mktime(time.strptime(cur[:19], "%Y-%m-%dT%H:%M:%S")),
                            int(m.group(1))))
            except Exception:
                pass
            cur = None
    return out


def collect(days):
    now = time.time()
    start = time.mktime(dt.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0).timetuple())
    start -= (days - 1) * 86400
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(start)) + "Z"
    tf = "TimeCreated[@SystemTime>='%s']" % iso

    stats = _parse(_query("*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] "
                          "and (EventID=105 or EventID=566) and %s]]" % tf))
    crashes = _parse(_query("*[System[(EventID=41) and %s]]" % tf))

    stats.sort()
    groups = []
    for t, _e in stats:
        if groups and t - groups[-1][-1] <= MERGE_SEC:
            groups[-1].append(t)
        else:
            groups.append([t])
    disc = [(g[0], len(g), int(g[-1] - g[0])) for g in groups]
    return disc, [t for t, _ in crashes], start


def _powerlog_times():
    """读出 powerlog 里所有采样时刻，用于把 Event 41 校准到真实冻结时刻。"""
    ts = []
    for p in sorted(glob.glob(os.path.join(PLOG_DIR, "power_*.csv"))):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for r in csv.DictReader(f):
                    try:
                        ts.append(time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S")))
                    except Exception:
                        pass
        except Exception:
            continue
    ts.sort()
    return ts


def refine_crashes(boots, plog, max_gap=4 * 3600):
    """Event 41 记的是「开机时刻」，真实冻结在此之前。

    若 powerlog 在开机前有采样、且最后一条到开机之间无采样（典型：冻结即断档），
    就把最后一条采样时刻当作冻结时刻；否则退化为「开机前 2 分钟」的估计。
    返回 [(冻结时刻, 开机时刻, 是否估计)]。
    """
    out = []
    for bt in boots:
        i = bisect.bisect_right(plog, bt) - 1
        if i >= 0 and 60 <= bt - plog[i] <= max_gap:
            out.append((plog[i], bt, False))
        else:
            out.append((bt - 120, bt, True))
    return out


def day_key(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# ---------------------------------------------------------------- 可滚动容器
class ScrollBody:
    def __init__(self, parent):
        self.outer = tk.Frame(parent, bg=BG)
        self.outer.pack(fill="both", expand=True)

        self.cv = tk.Canvas(self.outer, bg=BG, highlightthickness=0, bd=0)
        self.vs = tk.Scrollbar(self.outer, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=self.vs.set)
        self.vs.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.cv, bg=BG)
        self.win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner)
        self.cv.bind("<Configure>", self._on_canvas)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            parent.bind_all(seq, self._on_wheel, add="+")

    def _on_inner(self, _e):
        self.cv.configure(scrollregion=self.cv.bbox("all"))

    def _on_canvas(self, e):
        self.cv.itemconfigure(self.win, width=e.width)

    def _on_wheel(self, e):
        if isinstance(e.widget, tk.Scrollbar):
            return
        try:
            if self.outer.winfo_exists() == 0:
                return
        except Exception:
            return
        delta = -1 if getattr(e, "num", 0) == 4 else (
            1 if getattr(e, "num", 0) == 5 else (-1 if e.delta > 0 else 1))
        self.cv.yview_scroll(delta * 2, "units")

    def width(self):
        w = self.cv.winfo_width()
        return w if w > 50 else 900


# ---------------------------------------------------------------- 面板窗口
class FaultWindow(tk.Toplevel):
    def __init__(self, parent, days=7):
        tk.Toplevel.__init__(self, parent)
        self.days = days
        self.disc = []
        self.crash = []
        self.range_start = 0
        self.sel_day = day_key(time.time())
        self._day_w = 0
        self._bar_w = 0
        self.bar_geom = []

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1000, sw - 60), min(820, sh - 90)
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                       max(0, (sh - h) // 3)))
        self.minsize(720, 480)
        self.configure(bg=BG)
        self.title("电源故障统计 · 断电 / 冻结时间轴（最长 180 天）")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=(10, 2))

        tk.Label(bar, text="电源故障统计", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        tk.Label(bar, text="POWER FAULT LOG", bg=BG, fg=DIM,
                 font=("Consolas", 8)).pack(side="left", padx=(8, 0))

        self.btns = {}
        for label, n in reversed(RANGES):
            b = tk.Button(bar, text=label, font=("Microsoft YaHei UI", 9), bd=0,
                          relief="flat", padx=9, pady=2, cursor="hand2",
                          command=lambda v=n: self.switch(v))
            b.pack(side="right", padx=(5, 0))
            self.btns[n] = b

        tk.Button(bar, text="导出 CSV", font=("Microsoft YaHei UI", 9), bd=0,
                  relief="flat", padx=9, pady=2, bg=PANEL, fg=DIM,
                  activebackground=PANEL2, activeforeground=ACC,
                  cursor="hand2", command=self.export).pack(side="right", padx=(14, 8))

        tk.Label(self, text="滚轮 / 拖动滚动条可上下浏览",
                 bg=BG, fg=FAINT, font=("Microsoft YaHei UI", 8),
                 anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        self.body = ScrollBody(self)
        f = self.body.inner

        self.lbl_sum = tk.Label(f, text="正在读取事件日志…", bg=BG, fg=DIM,
                                font=("Consolas", 9), anchor="w")
        self.lbl_sum.pack(fill="x", padx=16, pady=(6, 4))

        self.lbl_day = tk.Label(f, text="", bg=BG, fg=FG,
                                font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        self.lbl_day.pack(fill="x", padx=16)

        self.cv_day = tk.Canvas(f, height=ROWS * (CELL_H + CELL_GAP) + 4, bg=BG,
                                highlightthickness=0, bd=0)
        self.cv_day.pack(fill="x", padx=16, pady=(4, 2))
        self.cv_day.bind("<Configure>", self._day_resize)

        tk.Label(f, text="每格 30 分钟 · 蓝=断连 红=冻结 · 点击下方柱状图切换日期",
                 bg=BG, fg=FAINT, font=("Microsoft YaHei UI", 8),
                 anchor="w").pack(fill="x", padx=16)

        tk.Frame(f, bg=LINE, height=1).pack(fill="x", padx=16, pady=(8, 6))

        self.lbl_bar = tk.Label(f, text="", bg=BG, fg=DIM,
                                font=("Consolas", 9), anchor="w")
        self.lbl_bar.pack(fill="x", padx=16)

        self.cv_bar = tk.Canvas(f, height=180, bg=BG, highlightthickness=0, bd=0)
        self.cv_bar.pack(fill="x", padx=16, pady=(2, 4))
        self.cv_bar.bind("<Configure>", self._bar_resize)
        self.cv_bar.bind("<Button-1>", self.on_bar_click)

        lg = tk.Frame(f, bg=BG)
        lg.pack(fill="x", padx=16, pady=(2, 18))
        for txt, col in (("断连  105+566（≤120s 合并）", PWR), ("冻结  Event 41", CRASH)):
            tk.Label(lg, text="■ " + txt, bg=BG, fg=col,
                     font=("Consolas", 8)).pack(side="left", padx=(0, 16))
        tk.Label(lg, text="※ 冻结时刻已按功率日志断档校准；无日志的取开机前 2 分钟估计", bg=BG,
                 fg=FAINT, font=("Microsoft YaHei UI", 8)).pack(side="right")

        self.switch(days)

    # ------------------------------------------------------------ 尺寸变化
    def _day_resize(self, e):
        if abs(e.width - self._day_w) > 2:
            self._day_w = e.width
            self.draw_day()

    def _bar_resize(self, e):
        if abs(e.width - self._bar_w) > 2:
            self._bar_w = e.width
            self.draw_bar()

    # ------------------------------------------------------------ 切换
    def switch(self, days):
        self.days = days
        for n, b in self.btns.items():
            b.config(bg=PANEL2 if n == days else PANEL,
                     fg=ACC if n == days else DIM)
        self.lbl_sum.config(text="正在读取事件日志…")
        self.update_idletasks()

        t0 = time.time()
        self.disc, boots, self.range_start = collect(days)
        # Event 41 的时间戳是「开机时刻」，用 powerlog 断档校准到真实冻结时刻
        self.crash_ref = refine_crashes(boots, _powerlog_times())
        self.crash = [f for f, _b, _e in self.crash_ref]
        self.crash_est = sum(1 for _f, _b, e in self.crash_ref if e)
        self.sel_day = day_key(time.time())
        self.lbl_sum.config(
            text="近 %d 天：断连 %d 次 · 冻结 %d 次   |   读取 %.1fs   |   "
                 "冻结时刻已按功率日志校准（%d 次无日志，取开机前 2 分钟估计）"
                 % (days, len(self.disc), len(self.crash), time.time() - t0,
                    self.crash_est))
        self.draw_day()
        self.draw_bar()
        self.body.cv.yview_moveto(0)

    # ------------------------------------------------------------ 48 格
    def draw_day(self):
        c = self.cv_day
        c.delete("all")
        W = self._day_w or 900
        pad = 2
        cw = (W - 2 * pad - (COLS - 1) * CELL_GAP) / float(COLS)

        d0 = time.mktime(time.strptime(self.sel_day, "%Y-%m-%d"))
        d1 = d0 + 86400
        disc = [x for x in self.disc if d0 <= x[0] < d1]
        crash = [t for t in self.crash if d0 <= t < d1]

        buckets = [[0, 0] for _ in range(48)]
        for ts, _n, _d in disc:
            buckets[min(47, int((ts - d0) // 1800))][0] += 1
        for ts in crash:
            buckets[min(47, int((ts - d0) // 1800))][1] += 1

        self.lbl_day.config(text="%s   断连 %d 次 · 冻结 %d 次"
                                % (self.sel_day, len(disc), len(crash)))

        for k in range(48):
            r, col = divmod(k, COLS)
            x = pad + col * (cw + CELL_GAP)
            y = pad + r * (CELL_H + CELL_GAP)
            hh, mm = divmod(k * 30, 60)
            p, cz = buckets[k]
            if cz:
                fill, stroke, tcol = "#2a1414", "#7d3030", CRASH
            elif p:
                fill, stroke, tcol = "#0d2733", "#2a6d80", PWR
            else:
                fill, stroke, tcol = "#0e1319", LINE, FAINT
            c.create_rectangle(x, y, x + cw, y + CELL_H, fill=fill, outline=stroke)
            c.create_text(x + 7, y + 13, anchor="w", text="%02d:%02d" % (hh, mm),
                          fill=DIM, font=("Consolas", 8))
            if p or cz:
                parts = []
                if p:
                    parts.append("断%d" % p)
                if cz:
                    parts.append("崩%d" % cz)
                c.create_text(x + 7, y + 31, anchor="w", text="  ".join(parts),
                              fill=tcol, font=("Consolas", 10, "bold"))
            else:
                c.create_text(x + 7, y + 31, anchor="w", text="—",
                              fill="#232c36", font=("Consolas", 9))

    # ------------------------------------------------------------ 柱状
    def draw_bar(self):
        c = self.cv_bar
        c.delete("all")
        W = self._bar_w or 900
        H = 180
        L, R, T, B = 42, W - 6, 8, H - 26

        days = [day_key(self.range_start + i * 86400) for i in range(self.days)]
        dmap = {d: [0, 0] for d in days}
        for ts, _n, _d in self.disc:
            k = day_key(ts)
            if k in dmap:
                dmap[k][0] += 1
        for ts in self.crash:
            k = day_key(ts)
            if k in dmap:
                dmap[k][1] += 1

        vmax = max(4, max(v[0] for v in dmap.values()), max(v[1] for v in dmap.values()))

        def y_of(v):
            return B - v / float(vmax) * (B - T)

        for v in (0, vmax // 2, vmax):
            c.create_line(L, y_of(v), R, y_of(v), fill="#161d26")
            c.create_text(L - 5, y_of(v), anchor="e", text=str(v), fill=FAINT,
                          font=("Consolas", 8))

        n = len(days)
        slot = (R - L) / float(n)
        bw = max(1.6, min(14.0, slot * 0.32))
        self.bar_geom = []

        for i, d in enumerate(days):
            xc = L + slot * (i + 0.5)
            p, cz = dmap[d]
            if d == self.sel_day:
                c.create_rectangle(xc - slot / 2, T, xc + slot / 2, B,
                                   fill="#111a22", outline="")
            c.create_rectangle(xc - bw - 1, y_of(p), xc - 1, B, fill=PWR, outline="")
            c.create_rectangle(xc + 1, y_of(cz), xc + bw + 1, B, fill=CRASH, outline="")
            if cz and n <= 40:
                c.create_text(xc, y_of(cz) - 5, text=str(cz), fill=CRASH,
                              font=("Consolas", 7))
            self.bar_geom.append((xc, slot))

            step = 1 if n <= 16 else (3 if n <= 45 else (7 if n <= 100 else 15))
            if i % step == 0:
                c.create_text(xc, B + 11, text=d[5:], fill=FAINT,
                              font=("Consolas", 7))

        tp = sum(v[0] for v in dmap.values())
        tc = sum(v[1] for v in dmap.values())
        peak = max(dmap.items(), key=lambda kv: kv[1][0] + kv[1][1])
        self.lbl_bar.config(
            text="按天分布 · 合计 断连 %d / 冻结 %d · 最重的一天 %s（断%d 崩%d）· 点柱子看那天"
                 % (tp, tc, peak[0], peak[1][0], peak[1][1]))

    def on_bar_click(self, e):
        if not self.bar_geom:
            return
        for i, (xc, slot) in enumerate(self.bar_geom):
            if abs(e.x - xc) <= max(slot / 2, 3):
                self.sel_day = day_key(self.range_start + i * 86400)
                self.draw_day()
                self.draw_bar()
                return

    # ------------------------------------------------------------ 导出
    def export(self):
        days = [day_key(self.range_start + i * 86400) for i in range(self.days)]
        dmap = {d: [0, 0] for d in days}
        for ts, _n, _d in self.disc:
            k = day_key(ts)
            if k in dmap:
                dmap[k][0] += 1
        for ts in self.crash:
            k = day_key(ts)
            if k in dmap:
                dmap[k][1] += 1
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            p = os.path.join(OUT_DIR, "电源故障统计_%s_%d天.csv"
                             % (time.strftime("%Y%m%d"), self.days))
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["日期", "断连次数", "冻结次数"])
                for d in days:
                    w.writerow([d, dmap[d][0], dmap[d][1]])
                w.writerow([])
                w.writerow(["区间", "%s → %s" % (days[0], days[-1])])
                w.writerow(["合计断连", sum(v[0] for v in dmap.values())])
                w.writerow(["合计冻结", sum(v[1] for v in dmap.values())])
            messagebox.showinfo("已导出", p, parent=self)
        except Exception as ex:
            messagebox.showerror("导出失败", str(ex), parent=self)


def main():
    root = tk.Tk()
    root.withdraw()
    FaultWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
