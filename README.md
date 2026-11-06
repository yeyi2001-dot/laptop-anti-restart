# 笔记本防重启 1.0 · 分享版

**Laptop Anti-Restart 1.0 · Share Edition**

让一个 CPU 核心持续满载，把机器维持在工作温度，避免「冷下来就死机重启」。

> 免费、开源、免安装、不需要管理员权限。
> Free, open source, portable, no admin required. Windows 10/11 x64.

[中文](#中文) · [English](#english)

---

# 中文

## 为什么做这个

作者用的是一台**联想游戏本**，长期**合盖外接显示器**使用。

合盖之后出风口受阻，热量散不出去，CPU 长时间处在高温状态。用了一段时间，机器出现了一个**暗病**：

- 开机后**不断死机、重启**
- 严重的时候**连系统都进不去**
- 换系统、重装驱动、清灰换硅脂，都没有解决

**后来偶然发现一件事：只要让一个 CPU 核心持续满载，机器就能稳住，不再重启。**
一旦让它空闲冷却下来，又开始死机。

于是就有了这个程序——**让一个核心保持满载，把机器维持在工作温度**。

**它治不好你的电脑。** 硬件已经受损的前提下，它只是把机器维持在一个"还能用"的状态。

如果你也是类似症状，可以**死马当活马医**试一下。

## 先说清楚：这是推断，不是结论

「合盖导致 CPU 过热变形脱焊」是**作者的推断**，没有做过拆机与显微验证。

同一类症状（开机后反复死机重启、无蓝屏、无转储、只有 Event 41）在别的机器上，
最常见的成因其实是另外几个，而且**它们是可以真正修好的**：

| 可能原因 | 怎么快速验证 |
|---|---|
| **电源适配器 / DC 头接触不良** | 满载 20 分钟后摸插头，明显发烫 → 接触电阻大 |
| **电池内阻升高** | 满载时软件显示「放电 xxW」→ 适配器已到极限 |
| **散热器积灰 / 硅脂干涸** | 满载温度 95℃+ 且立刻降频 |
| **内存 / 内存插槽** | 单条轮换、换插槽测试 |
| **主板供电相老化** | 需维修店用万用表测 +12V 压降 |

**建议顺序：先做下面「四步排查」，都排除了，再来试这个程序。**
毕竟"保温"买的是时间，不是修复。

## 四步排查（不花钱，比装任何软件都准）

1. **满载 20 分钟后摸电源插头（DC 头）**：明显发烫 → 先清洁或更换 DC 头 / 电源线
2. **满载时看软件里的「电池」一行**：出现 `放电 xxW` → 适配器到极限；一直 `0W` → 适配器还有余量
3. **清洁 DC 头 + 机器端插孔后再测**：如果存活时间明显变长 → 就是接触问题
4. **看诊断页的崩溃时间分布**：全集中在高负载时段、且日志里没有「电源断连」→ 大概率供电链路

> 作者自己这台机器，就是靠第 3 步从"5 分钟必崩"变成"18 分钟不崩"的。
> **清洁一个插头，比装任何软件都有效。**

## 这个程序做什么

| 模块 | 说明 |
|---|---|
| **防重启保温** | 一个 CPU 核心持续接近满载，让机器不冷下来。**默认关闭**，手动开启 |
| **实时功耗** | 显卡（NVIDIA 实测）· 处理器（按占用率推算）· 整机（推算 / 电池放电时实测） |
| **显示器控制** | 点方块断开某台显示器 · 再点虚线框连回 · 仅内屏 / 仅外屏 / 扩展 / 复制 · 右键只关背光 |
| **故障诊断** | 读事件日志统计「电源断连」与「异常重启」，最长 180 天，48 格半小时热力图 |
| **历史曲线** | 6 小时 / 24 小时 / 3 天 / 7 天，5 秒一个采样 |

```
┌──────────────────────────────────────────────┐
│ 笔记本防重启 1.0 · 分享版            ● 采样中 │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│ │ 显卡 GPU │ │ 处理器CPU│ │ 整机TOTAL│          │
│ │    83   │ │    21   │ │   106   │          │
│ └─────────┘ └─────────┘ └─────────┘          │
│   ～～～～～～ 功耗曲线 100/130W 虚线 ～～～～～  │
│ 电池 BATT  0W · 交流 ac · 电池不出力           │
│ ──────────────────────────────────────────── │
│ 防重启保温 KEEP-WARM（默认关闭）      [ 开始 ] │
│ ──────────────────────────────────────────── │
│ [显示器]  [故障诊断]  [历史曲线]               │
└──────────────────────────────────────────────┘
```

## 下载与使用

1. 到 [Releases](../../releases) 下载 `Laptop-Anti-Restart-1.0-Share.exe`（中文说明：`USAGE-zh.txt`）
2. 双击运行（免安装，不写注册表，不建开机启动）
3. 首次会看到「Windows 已保护你的电脑」→ **更多信息 → 仍要运行**（未做代码签名）

> GitHub 会剥离 Release 附件名里的非 ASCII 字符，所以下载到的文件名是英文 `Laptop-Anti-Restart-1.0-Share.exe`；
> 程序窗口标题仍是「笔记本防重启 1.0 · 分享版」，不影响使用。

**如果你要的只是防重启**：打开后点「开始 START」，确认，然后最小化。它会一直保温到你点停止或关窗口。

**如果你只是想查清楚为什么会崩**：不用开保温，直接看「故障诊断」和「历史曲线」，
崩一次之后回来读数据，比猜有用得多。

数据目录：`%LOCALAPPDATA%\AntiRestart\`（删掉等于清空全部记录，日志保留 7 天自动清理）。

## 保温的代价

| 项 | 代价 |
|---|---|
| 功耗 | 持续约 10–20W（一年约 130 kWh） |
| 温度 | 笔记本长期偏高，**对电池寿命不利**（锂电最适 20–35℃） |
| 风扇 | 长期运转，进灰更快 |
| 效果 | 只在「冷机就崩、热起来稳定」这类机器上有意义；**满载才崩的帮不上** |

内置两条保险：**使用电池时自动暂停**、**显卡 ≥80℃ 自动暂停**。

## 实现

- 纯标准库，只用 `ctypes` 调 Win32，不依赖 `pywin32` / `wmi` / `psutil`
- 保温：单线程 15ms 忙等 + 0.8ms 让步，`THREAD_PRIORITY_BELOW_NORMAL`，实测占用 **0.90 核**，停止后立即归零
- GPU 功耗：`nvidia-smi --query-gpu=power.draw`
- 电池充放：`CallNtPowerInformation(SystemBatteryState)`
- 显示器断开/连接：`SetDisplayConfig` 操作 `DISPLAYCONFIG_PATH`
  （断开后 Windows 会清除该屏的显示模式，重连必须用
  `GET_TARGET_PREFERRED_MODE` 重建 source/target mode，只改 ACTIVE 标志连不回来）
- 崩溃统计：`wevtutil` 读 `Event 41` 与 `Kernel-Power 105/566`

## 构建

```bat
pip install pyinstaller
pyinstaller --onefile --windowed --name AntiRestart ^
  --hidden-import display_ctl --hidden-import power_fault_view ^
  share_edition.pyw
```

## 免责

本工具只做观测与系统接口调用，**不修改系统设置、不做任何"修复"**。
CPU 与整机功耗为推算值，不能当作电表读数。
保温会让机器长期处于较高温度，是否使用、何时使用，请自行判断并承担后果。

MIT License.

---

# English

## Why this exists

The author uses a **Lenovo gaming laptop**, mostly **with the lid closed** driving an external monitor.

With the lid shut, the exhaust is partly blocked, heat builds up, and the CPU sits at high temperature for long stretches. Eventually the machine developed a **latent fault**:

- it **keeps freezing and rebooting** after boot
- in bad cases it **can't even reach the desktop**
- reinstalling Windows, reinstalling drivers, cleaning dust and repasting — none of it helped

**Then one accidental discovery: as long as one CPU core stays fully loaded, the machine is stable.**
Let it idle and cool down, and the reboots come back.

So this tool exists to do exactly that — **keep one core busy and keep the machine warm**.

**It does not repair your laptop.** It only keeps a damaged machine in a usable state.

If your symptoms look similar, it costs nothing to try — *"死马当活马医"*, treat the dying horse as if it were alive.

## Honest disclaimer

"Closing the lid caused the CPU to overheat, deform and crack its solder joints" is the **author's inference**.
No teardown and no microscopic inspection was performed to confirm it.

The same symptom set — repeated freezes after boot, no BSOD, no crash dump, only `Event 41` —
is more often caused by things that **can actually be fixed**:

| Likely cause | Quick check |
|---|---|
| **Bad DC jack / adapter contact** | Touch the plug after 20 min at full load; hot means high contact resistance |
| **Aged battery (high internal resistance)** | If the app shows `放电 xxW` (discharging) under load, the adapter is maxed out |
| **Dust / dried thermal paste** | 95℃+ and immediate throttling under load |
| **RAM / RAM slot** | Test sticks one at a time, swap slots |
| **Aged VRM phases** | Needs a shop with a multimeter to measure +12V droop |

**Do the four checks below first. Try this tool only after they all come up empty.**

## Four checks (free, and more accurate than any software)

1. Touch the DC plug after 20 minutes at full load — hot means clean or replace the jack/cable
2. Watch the battery row under load — `discharging` means the adapter is at its limit
3. Clean the DC plug and the laptop-side socket, then retest — longer survival means it was contact resistance
4. Look at the crash time distribution on the diagnostics page — if everything clusters at high load and there are no power-disconnect events, suspect the power path

## What it does

| Module | Description |
|---|---|
| **Anti-restart keep-warm** | One core stays near 100%. **Off by default**, manual start |
| **Live power** | GPU (measured via nvidia-smi) · CPU (estimated) · System total (estimated / measured on battery) |
| **Display control** | Click a tile to disconnect that monitor (cursor can't enter it) · click the dashed tile to reconnect · internal / external / extend / clone · right-click to blank backlight |
| **Fault diagnostics** | Counts power-disconnect and unexpected-restart events, up to 180 days, 48-cell half-hour heatmap |
| **History** | 6h / 24h / 3d / 7d curve, sampled every 5 s |

## Usage

1. Download `Laptop-Anti-Restart-1.0-Share.exe` from [Releases](../../releases) (Chinese manual: `USAGE-zh.txt`)
2. Double-click. Portable — no installer, no registry, no startup entry
3. SmartScreen will warn ("Windows protected your PC") because the binary is unsigned → **More info → Run anyway**

Data lives in `%LOCALAPPDATA%\AntiRestart\`; delete the folder to wipe everything (logs auto-expire after 7 days).

## Cost of keep-warm

About 10–20 W continuously (~130 kWh/year), permanently elevated temperatures (bad for battery longevity),
and a fan that never stops. It only helps machines that **crash when cold and stabilize when warm** —
it does nothing for machines that crash **because of** full load.

Two built-in guards: **auto-pause on battery** and **auto-pause above 80℃ GPU**.

## Compatibility

Windows 10 / 11, 64-bit. No admin required.
GPU power needs NVIDIA's `nvidia-smi`; on AMD / integrated graphics that card shows `—` and everything else still works.

## License

MIT
