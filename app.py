#!/usr/bin/env python3
"""
macOS System Monitor — Menu Bar App
点击菜单栏图标即可查看系统状态，无需打开窗口。
支持 Apple Silicon，无需 sudo。
"""

APP_VERSION = "1.3.0"
APP_NAME = "System Monitor"
APP_DEVELOPER = "Marshall Zheng"

# ── i18n ──
I18N = {
    "en": {
        "show_dashboard": "Show Dashboard",
        "line1": "Line 1",
        "line2": "Line 2",
        "none": "None",
        "start_login": "Start at Login",
        "pin_dashboard": "Dashboard on Top",
        "language": "Language",
        "about": "About",
        "quit": "Quit",
        "copyright": "Copyright © 2026 Marshall Zheng. All rights reserved.",
        "description": "A lightweight macOS menu bar system monitor for Apple Silicon.",
        "temp_label": "Temperature",
        "power_label": "Power",
    },
    "zh": {
        "show_dashboard": "打开面板",
        "line1": "第一行",
        "line2": "第二行",
        "none": "不显示",
        "start_login": "开机启动",
        "pin_dashboard": "面板置顶",
        "language": "语言",
        "about": "关于",
        "quit": "退出",
        "copyright": "Copyright © 2026 Marshall Zheng. 保留所有权利。",
        "description": "轻量级 macOS 菜单栏系统监控工具，专为 Apple Silicon 设计。",
        "temp_label": "温度",
        "power_label": "功耗",
    },
    "ja": {
        "show_dashboard": "ダッシュボード",
        "line1": "1行目",
        "line2": "2行目",
        "none": "非表示",
        "start_login": "ログイン時に起動",
        "pin_dashboard": "ダッシュボード最前面",
        "language": "言語",
        "about": "情報",
        "quit": "終了",
        "copyright": "Copyright © 2026 Marshall Zheng. All rights reserved.",
        "description": "Apple Silicon 向け軽量 macOS メニューバーシステムモニター。",
        "temp_label": "温度",
        "power_label": "電力",
    },
}
LANG_NAMES = {"en": "English", "zh": "中文", "ja": "日本語"}
DEFAULT_LANG = "en"

def _t(key):
    """Get translated string for current language."""
    return I18N.get(_current_lang, I18N["en"]).get(key, I18N["en"].get(key, key))

_current_lang = DEFAULT_LANG

# 菜单栏可选显示项（只显示数值，不显示标签前缀）
MENUBAR_ITEMS = {
    "cpu": ("CPU %", lambda s: f"{s.get('cpu', 0):.0f}%"),
    "gpu": ("GPU %", lambda s: f"{s.get('gpu_pct', 0):.0f}%"),
    "ram": ("RAM %", lambda s: f"{s.get('ram_pct', 0):.0f}%"),
    "temp": ("Temperature", lambda s: f"{s.get('cpu_temp', 0):.0f}°" if s.get('cpu_temp') else "—°"),
    "power": ("Power", lambda s: f"{s.get('total_power', 0):.1f}W"),
    "net_ul": ("Net ↑", lambda s: f"↑{_fmt_speed_short(s.get('net_ul', 0))}"),
    "net_dl": ("Net ↓", lambda s: f"↓{_fmt_speed_short(s.get('net_dl', 0))}"),
}
MENUBAR_DEFAULT_TOP = "cpu"
MENUBAR_DEFAULT_BOTTOM = ""


def _fmt_speed_short(bps):
    if bps < 1024:
        return f"{bps:.0f}B"
    if bps < 1048576:
        return f"{bps / 1024:.0f}K"
    return f"{bps / 1048576:.1f}M"

import sys

from pathlib import Path
from collections import deque

# ── 自动加载 venv (只加载与当前 Python 版本匹配的) ──
_base = Path(__file__).resolve().parent
_pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
for _venv_root in [
    _base / "venv" / "lib",
    Path.home() / "Library/Application Support/SystemMonitor/venv/lib",
]:
    _sp = _venv_root / _pyver / "site-packages"
    if _sp.exists() and str(_sp) not in sys.path:
        sys.path.insert(0, str(_sp))
        break  # 只用第一个匹配的 venv

# 隐藏 Dock 图标（必须在 QApplication 创建前设置）
import AppKit
info = AppKit.NSBundle.mainBundle().infoDictionary()
info["LSUIElement"] = "1"

import psutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QFrame, QMenu, QMainWindow,
    QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox, QGroupBox,
    QSystemTrayIcon, QWidgetAction, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QRect, QPoint, pyqtSignal, QPointF
from PyQt6.QtGui import (
    QFont, QFontMetrics, QPixmap, QPainter, QColor, QPen,
    QIcon, QPainterPath, QShortcut, QKeySequence,
)
import json

from apple_metrics import (
    PowerReader, NetworkMonitor, GPUReader, TempReader, BatteryReader,
)

HIST = 60  # 60 秒历史


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 工具函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fmt_bytes(b):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def fmt_speed(bps):
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1048576:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / 1048576:.1f} MB/s"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 自绘组件（轻量，不依赖 pyqtgraph）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 主题系统（纯跟随系统） ──

def _system_is_dark():
    try:
        import subprocess
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip().lower() == "dark"
    except Exception:
        return False

_DARK_MODE = _system_is_dark()

def _apply_theme():
    """根据当前 _DARK_MODE 刷新 Theme 类所有属性。"""
    dark = _DARK_MODE
    if dark:
        Theme.BG, Theme.BG_CARD = "#000000", "#0a0a0a"
        Theme.TRACK, Theme.BORDER = "#1a1a1a", "#222222"
        Theme.TEXT, Theme.TEXT_DIM, Theme.TEXT_BRIGHT = "#e0e0e0", "#777777", "#ffffff"
        Theme.CPU, Theme.GPU, Theme.RAM = "#5bb8f5", "#7ecf8a", "#e8a855"
        Theme.TEMP, Theme.POWER = "#e57373", "#cfb44a"
        Theme.NET_DL, Theme.NET_UL = "#5ec4b0", "#d4845a"
        Theme.BAR_LOW, Theme.BAR_MID, Theme.BAR_HIGH = "#5bb8f5", "#e8a855", "#e06060"
    else:
        Theme.BG, Theme.BG_CARD = "transparent", "#f0f0f0"
        Theme.TRACK, Theme.BORDER = "#e6e6e6", "#e0e0e0"
        Theme.TEXT, Theme.TEXT_DIM, Theme.TEXT_BRIGHT = "#1c1c1e", "#6e6e73", "#000000"
        Theme.CPU, Theme.GPU, Theme.RAM = "#2196F3", "#4CAF50", "#E67E22"
        Theme.TEMP, Theme.POWER = "#E53935", "#F9A825"
        Theme.NET_DL, Theme.NET_UL = "#00897B", "#D84315"
        Theme.BAR_LOW, Theme.BAR_MID, Theme.BAR_HIGH = "#2196F3", "#E67E22", "#E53935"

class Theme:
    pass

_apply_theme()


class GaugeBar(QWidget):
    """薄圆角进度条，颜色随百分比渐变。每次绘制实时读取 Theme 值。"""

    def __init__(self, height=6, color=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._v = 0
        self._fixed_color = color  # 保存颜色字符串，不缓存 QColor

    def set_color(self, color):
        self._fixed_color = color
        self.update()

    def set_value(self, v):
        self._v = max(0, min(100, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        # 底色 — 每次实时读取 Theme.TRACK
        p.setBrush(QColor(Theme.TRACK))
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        # 填充
        fw = w * self._v / 100
        if fw > 0:
            if self._fixed_color:
                p.setBrush(QColor(self._fixed_color))
            else:
                c = Theme.BAR_LOW if self._v < 50 else (Theme.BAR_MID if self._v < 80 else Theme.BAR_HIGH)
                p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(0, 0, max(fw, h), h), r, r)
        p.end()


class SparkLine(QWidget):
    """迷你折线图，纯 QPainter 绘制。"""

    def __init__(self, color=None, auto_max=False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self._color = QColor(color) if color else QColor(Theme.CPU)
        self._data = deque([0.0] * HIST, maxlen=HIST)
        self._auto = auto_max

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def append(self, v):
        self._data.append(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        data = list(self._data)
        mx = max(max(data), 1) if self._auto else 100
        n = len(data)
        if n < 2:
            p.end()
            return
        path = QPainterPath()
        for i, v in enumerate(data):
            x = i * w / (n - 1)
            y = h - (min(v, mx) / mx * (h - 2)) - 1
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(self._color, 1.2))
        p.drawPath(path)
        fill = QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        fill.closeSubpath()
        fc = QColor(self._color)
        fc.setAlpha(25)
        p.fillPath(fill, fc)
        p.end()


class DualSparkLine(QWidget):
    """双线迷你图（下载 + 上传）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self._dl = deque([0.0] * HIST, maxlen=HIST)
        self._ul = deque([0.0] * HIST, maxlen=HIST)

    def append(self, dl, ul):
        self._dl.append(dl)
        self._ul.append(ul)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mx = max(max(self._dl), max(self._ul), 1)
        for data, color in [(self._dl, QColor(Theme.NET_DL)),
                            (self._ul, QColor(Theme.NET_UL))]:
            pts = list(data)
            n = len(pts)
            if n < 2:
                continue
            path = QPainterPath()
            for i, v in enumerate(pts):
                x = i * w / (n - 1)
                y = h - (min(v, mx) / mx * (h - 2)) - 1
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(color, 1.2))
            p.drawPath(path)
        p.end()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 监控面板（嵌入菜单栏下拉菜单）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MonitorWidget(QWidget):
    """嵌入 QMenu 的紧凑监控面板。"""
    cpu_updated = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(320)
        self.setFixedHeight(430)

        # 数据源
        self._pwr = PowerReader()
        self._gpu = GPUReader()
        self._temp = TempReader(interval=3.0)  # 温度 3 秒读一次（IOHIDEvent 很慢）
        self._bat = BatteryReader(interval=10.0)  # 电池 10 秒读一次
        self._net = NetworkMonitor()
        psutil.cpu_percent()  # 初始化

        # 最新一轮采集的数据快照（供 dashboard 复用，避免重复采集）
        self._snapshot = {}

        self._build_ui()

        self._pwr.start()
        self._gpu.start()
        self._temp.start()
        self._bat.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        QTimer.singleShot(300, self._tick)

    # ── 构建界面 ──

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 6)
        lay.setSpacing(4)

        # 标题
        self._title = QLabel("System Monitor")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title)
        self._seps = []
        s = self._sep(); self._seps.append(s); lay.addWidget(s)

        # CPU
        self._cpu_row = self._metric_row("CPU")
        self._cpu_bar, self._cpu_val, self._cpu_lbl = self._cpu_row["bar"], self._cpu_row["val"], self._cpu_row["lbl"]
        lay.addLayout(self._cpu_row["layout"])
        self._cpu_spark = SparkLine()
        lay.addWidget(self._cpu_spark)

        # GPU
        self._gpu_row = self._metric_row("GPU")
        self._gpu_bar, self._gpu_val, self._gpu_lbl = self._gpu_row["bar"], self._gpu_row["val"], self._gpu_row["lbl"]
        lay.addLayout(self._gpu_row["layout"])
        self._gpu_spark = SparkLine()
        lay.addWidget(self._gpu_spark)

        s = self._sep(); self._seps.append(s); lay.addWidget(s)

        # RAM
        self._ram_row = self._metric_row("RAM")
        self._ram_bar, self._ram_val, self._ram_lbl = self._ram_row["bar"], self._ram_row["val"], self._ram_row["lbl"]
        lay.addLayout(self._ram_row["layout"])
        self._ram_info = QLabel("")
        lay.addWidget(self._ram_info)

        s = self._sep(); self._seps.append(s); lay.addWidget(s)

        # 温度 + 功耗 — 卡片式排列
        tp_row = QHBoxLayout()
        tp_row.setSpacing(6)
        tp_row.setContentsMargins(0, 2, 0, 2)

        # 温度卡片
        self._temp_card = QFrame()
        tc_lay = QVBoxLayout(self._temp_card)
        tc_lay.setContentsMargins(6, 4, 6, 4)
        tc_lay.setSpacing(2)
        self._tc_title = QLabel("TEMP")
        tc_lay.addWidget(self._tc_title)
        self._cpu_temp = QLabel("CPU --°C")
        tc_lay.addWidget(self._cpu_temp)
        self._gpu_temp = QLabel("GPU --°C")
        tc_lay.addWidget(self._gpu_temp)
        tp_row.addWidget(self._temp_card, 1)

        # 功耗卡片
        self._pwr_card = QFrame()
        pc_lay = QVBoxLayout(self._pwr_card)
        pc_lay.setContentsMargins(6, 4, 6, 4)
        pc_lay.setSpacing(2)
        self._pc_title = QLabel("POWER")
        pc_lay.addWidget(self._pc_title)
        self._pwr_total = QLabel("-- W")
        pc_lay.addWidget(self._pwr_total)
        self._pwr_detail = QLabel("CPU -- | GPU -- | DRAM --")
        pc_lay.addWidget(self._pwr_detail)
        self._charge_label = QLabel("")
        self._charge_label.hide()
        pc_lay.addWidget(self._charge_label)
        tp_row.addWidget(self._pwr_card, 1)

        lay.addLayout(tp_row)
        s = self._sep(); self._seps.append(s); lay.addWidget(s)

        # 网络
        net_row = QHBoxLayout()
        net_row.setContentsMargins(0, 0, 0, 0)
        self._net_dl = QLabel("↓ --")
        self._net_ul = QLabel("↑ --")
        net_row.addWidget(self._net_dl)
        net_row.addStretch()
        net_row.addWidget(self._net_ul)
        lay.addLayout(net_row)

        self._net_spark = DualSparkLine()
        lay.addWidget(self._net_spark)

        # 初始应用样式
        self._refresh_styles()

    def _metric_row(self, name):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(name)
        lbl.setFixedWidth(32)
        bar = GaugeBar()
        val = QLabel("--%")
        val.setFixedWidth(42)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl)
        row.addWidget(bar, 1)
        row.addWidget(val)
        return {"layout": row, "bar": bar, "val": val, "lbl": lbl}

    @staticmethod
    def _sep():
        f = QFrame()
        f.setFrameShape(QFrame.Shape.NoFrame)
        f.setFixedHeight(1)
        f.setStyleSheet(f"background: {Theme.BORDER};")
        return f

    def _refresh_styles(self):
        """根据当前 Theme 刷新所有子 widget 的样式。"""
        # 只设自身背景，不用 catch-all 选择器（避免覆盖子控件）
        self.setStyleSheet("background: transparent;")

        # 通用 label 样式
        _lbl = f"font-size: 12px; color: {Theme.TEXT}; background: transparent;"

        # 标题
        self._title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {Theme.TEXT_BRIGHT}; background: transparent;"
        )
        # CPU / GPU / RAM 标签
        self._cpu_lbl.setStyleSheet(f"font-weight: bold; color: {Theme.CPU}; background: transparent;")
        self._gpu_lbl.setStyleSheet(f"font-weight: bold; color: {Theme.GPU}; background: transparent;")
        self._ram_lbl.setStyleSheet(f"font-weight: bold; color: {Theme.RAM}; background: transparent;")
        # 数值
        self._cpu_val.setStyleSheet(f"font-weight: bold; color: {Theme.TEXT_BRIGHT}; background: transparent;")
        self._gpu_val.setStyleSheet(f"font-weight: bold; color: {Theme.TEXT_BRIGHT}; background: transparent;")
        self._ram_val.setStyleSheet(f"font-weight: bold; color: {Theme.TEXT_BRIGHT}; background: transparent;")
        # SparkLine 颜色
        self._cpu_spark.set_color(Theme.CPU)
        self._gpu_spark.set_color(Theme.GPU)
        # GaugeBar 强制重绘（paintEvent 实时读 Theme）
        self._cpu_bar.update()
        self._gpu_bar.update()
        self._ram_bar.update()
        # RAM info
        self._ram_info.setStyleSheet(f"font-size: 10px; color: {Theme.TEXT_DIM}; background: transparent;")
        # 分割线
        for s in self._seps:
            s.setStyleSheet(f"background: {Theme.BORDER};")
        # 温度 & 功耗卡片 — 用 objectName 限定 QFrame，不影响内部 QLabel
        self._temp_card.setObjectName("card")
        self._pwr_card.setObjectName("card")
        card_style = (
            f"QFrame#card {{ background: {Theme.BG_CARD}; border-radius: 6px;"
            f" border: 1px solid {Theme.BORDER}; }}"
            f" QFrame#card QLabel {{ background: transparent; }}"
        )
        self._temp_card.setStyleSheet(card_style)
        self._pwr_card.setStyleSheet(card_style)
        self._tc_title.setStyleSheet(f"font-weight: bold; font-size: 10px; color: {Theme.TEMP};")
        self._pc_title.setStyleSheet(f"font-weight: bold; font-size: 10px; color: {Theme.POWER};")
        self._cpu_temp.setStyleSheet(f"font-size: 12px; color: {Theme.TEXT_BRIGHT};")
        self._gpu_temp.setStyleSheet(f"font-size: 12px; color: {Theme.TEXT_BRIGHT};")
        self._pwr_total.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {Theme.TEXT_BRIGHT};")
        self._pwr_detail.setStyleSheet(f"font-size: 10px; color: {Theme.TEXT_DIM};")
        self._charge_label.setStyleSheet(f"font-size: 10px; color: {Theme.POWER};")
        # 网络
        self._net_dl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {Theme.NET_DL}; background: transparent;")
        self._net_ul.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {Theme.NET_UL}; background: transparent;")

    # ── 数据刷新 ──

    def _tick(self):
        # 采集一次，存入 snapshot，供 dashboard 复用
        cpu = psutil.cpu_percent()
        gpu = self._gpu.latest  # 从背景线程读取，不阻塞
        vm = psutil.virtual_memory()
        temps = self._temp.latest  # 后台线程读取，不阻塞主线程
        pwr = self._pwr.latest
        net = self._net.get_speeds()

        used = vm.total - vm.available
        ram_pct = used / vm.total * 100

        # 提取常用值，供 MENUBAR_ITEMS lambda 使用
        gpu_pct = 0
        if gpu:
            gpu_pct = gpu.get(
                "Device Utilization %",
                gpu.get("Renderer Utilization %", next(iter(gpu.values()), 0)),
            )
        self._snapshot = {
            "cpu": cpu, "gpu": gpu, "gpu_pct": gpu_pct, "vm": vm,
            "used": used, "ram_pct": ram_pct,
            "temps": temps, "pwr": pwr, "net": net,
            "cpu_temp": temps.get("cpu_temp", 0),
            "total_power": pwr.get("total_power", 0) if pwr else 0,
            "net_dl": net.get("download_speed", 0),
            "net_ul": net.get("upload_speed", 0),
        }

        # CPU
        self._cpu_bar.set_value(cpu)
        self._cpu_val.setText(f"{cpu:.0f}%")
        self._cpu_spark.append(cpu)

        # GPU (reuse gpu_pct from snapshot)
        if gpu:
            self._gpu_bar.set_value(gpu_pct)
            self._gpu_val.setText(f"{gpu_pct:.0f}%")
            self._gpu_spark.append(gpu_pct)
        else:
            self._gpu_val.setText("N/A")

        # RAM
        self._ram_bar.set_value(ram_pct)
        self._ram_val.setText(f"{ram_pct:.0f}%")
        self._ram_info.setText(
            f"{fmt_bytes(used)} / {fmt_bytes(vm.total)}   "
            f"Avail: {fmt_bytes(vm.available)}"
        )

        # 温度
        ct = temps.get("cpu_temp")
        gt = temps.get("gpu_temp")
        self._cpu_temp.setText(f"CPU {ct:.0f}°C" if ct else "CPU --°C")
        self._gpu_temp.setText(f"GPU {gt:.0f}°C" if gt else "GPU --°C")

        # 功耗
        if pwr:
            self._pwr_total.setText(f"{pwr.get('total_power', 0):.1f} W")
            self._pwr_detail.setText(
                f"CPU {pwr.get('cpu_power', 0):.1f}  "
                f"GPU {pwr.get('gpu_power', 0):.1f}  "
                f"DRAM {pwr.get('dram_power', 0):.1f}"
            )

        # 充电状态（后台线程 10 秒轮询）
        bat = self._bat.latest
        self._snapshot["battery"] = bat
        if bat["charging"] and bat["charge_watts"] > 0:
            self._charge_label.setText(f"⚡ {bat['charge_watts']:.1f}W ({bat['percent']}%)")
            self._charge_label.show()
        elif bat["plugged"]:
            self._charge_label.setText(f"🔌 {bat['percent']}%")
            self._charge_label.show()
        else:
            self._charge_label.hide()

        # 网络
        self._net_dl.setText(f"↓ {fmt_speed(net['download_speed'])}")
        self._net_ul.setText(f"↑ {fmt_speed(net['upload_speed'])}")
        self._net_spark.append(
            net["download_speed"] / 1024,
            net["upload_speed"] / 1024,
        )

        # 通知 dashboard 更新
        self.cpu_updated.emit(cpu)

    def stop(self):
        self._pwr.stop()
        self._gpu.stop()
        self._temp.stop()
        self._bat.stop()
        self._timer.stop()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dashboard 大窗口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BigSparkLine(QWidget):
    """Dashboard 用的大号折线图。"""

    def __init__(self, color="#00bcd4", auto_max=False, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._color = QColor(color)
        self._data = deque([0.0] * HIST, maxlen=HIST)
        self._auto = auto_max

    def append(self, v):
        self._data.append(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        data = list(self._data)
        mx = max(max(data), 1) if self._auto else 100
        n = len(data)
        if n < 2:
            p.end()
            return

        # 背景网格
        p.setPen(QPen(QColor(255, 255, 255, 15), 0.5))
        for pct in (25, 50, 75):
            yy = h - pct / 100 * h
            p.drawLine(QPointF(0, yy), QPointF(w, yy))

        # Y 轴标签
        p.setPen(QColor(255, 255, 255, 60))
        p.setFont(QFont(".AppleSystemUIFont", 9))
        for pct in (0, 50, 100):
            lbl = f"{int(pct * mx / 100)}" if self._auto else f"{pct}"
            yy = h - pct / 100 * h
            p.drawText(QPointF(4, yy - 2 if pct > 0 else yy + 10), lbl)

        # 曲线
        path = QPainterPath()
        for i, v in enumerate(data):
            x = i * w / (n - 1)
            y = h - (min(v, mx) / mx * (h - 4)) - 2
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(self._color, 2))
        p.drawPath(path)

        # 填充
        fill = QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        fill.closeSubpath()
        fc = QColor(self._color)
        fc.setAlpha(40)
        p.fillPath(fill, fc)
        p.end()


class BigDualSparkLine(QWidget):
    """Dashboard 用的大号双线图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._dl = deque([0.0] * HIST, maxlen=HIST)
        self._ul = deque([0.0] * HIST, maxlen=HIST)

    def append(self, dl, ul):
        self._dl.append(dl)
        self._ul.append(ul)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mx = max(max(self._dl), max(self._ul), 1)

        # 背景网格
        p.setPen(QPen(QColor(255, 255, 255, 15), 0.5))
        for pct in (25, 50, 75):
            yy = h - pct / 100 * h
            p.drawLine(QPointF(0, yy), QPointF(w, yy))

        for data, color in [(self._dl, QColor(Theme.NET_DL)),
                            (self._ul, QColor(Theme.NET_UL))]:
            pts = list(data)
            n = len(pts)
            if n < 2:
                continue
            path = QPainterPath()
            for i, v in enumerate(pts):
                x = i * w / (n - 1)
                y = h - (min(v, mx) / mx * (h - 4)) - 2
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(color, 2))
            p.drawPath(path)
        p.end()


class DashboardWindow(QMainWindow):
    """独立的详细监控 Dashboard 窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("System Monitor Dashboard")
        self.setMinimumSize(720, 520)
        self.resize(800, 580)
        self._apply_style()
        self._build_ui()
        # Cmd+W 关闭窗口（仅隐藏，不退出应用）
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)

    def _apply_style(self):
        bg_main = Theme.BG if Theme.BG != "transparent" else ("#000000" if _DARK_MODE else "#f0f0f0")
        bg_card = Theme.BG_CARD if Theme.BG_CARD != "transparent" else ("#0a0a0a" if _DARK_MODE else "#ffffff")
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background-color: {bg_main}; color: {Theme.TEXT}; }}
            QGroupBox {{
                border: 1px solid {Theme.BORDER}; border-radius: 8px;
                margin-top: 12px; padding: 8px 8px 6px 8px;
                font-size: 12px; font-weight: bold;
                background-color: {bg_card};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
            }}
            QLabel {{ font-size: 12px; color: {Theme.TEXT}; }}
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 0, 10, 6)
        root.setSpacing(4)

        # 两列布局
        cols = QHBoxLayout()
        cols.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(6)
        right = QVBoxLayout()
        right.setSpacing(6)

        # ── 左列: CPU + GPU ──
        # CPU
        cpu_box = self._group("CPU", Theme.CPU)
        cpu_lay = QVBoxLayout(cpu_box)
        cpu_lay.setContentsMargins(8, 16, 8, 6)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.d_cpu_lbl = QLabel("0%")
        self.d_cpu_lbl.setFixedWidth(70)
        self.d_cpu_lbl.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {Theme.TEXT_BRIGHT};")
        self.d_cpu_bar = GaugeBar(10, color=Theme.CPU)
        row.addWidget(self.d_cpu_lbl, 0)
        row.addWidget(self.d_cpu_bar, 1)
        cpu_lay.addLayout(row)
        self.d_cpu_chart = BigSparkLine(Theme.CPU)
        cpu_lay.addWidget(self.d_cpu_chart, 1)
        left.addWidget(cpu_box, 1)

        # GPU
        gpu_box = self._group("GPU (Apple Silicon)", Theme.GPU)
        gpu_lay = QVBoxLayout(gpu_box)
        gpu_lay.setContentsMargins(8, 16, 8, 6)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.d_gpu_lbl = QLabel("--%")
        self.d_gpu_lbl.setFixedWidth(70)
        self.d_gpu_lbl.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {Theme.TEXT_BRIGHT};")
        self.d_gpu_bar = GaugeBar(10, color=Theme.GPU)
        row.addWidget(self.d_gpu_lbl, 0)
        row.addWidget(self.d_gpu_bar, 1)
        gpu_lay.addLayout(row)
        self.d_gpu_detail = QLabel("")
        self.d_gpu_detail.setStyleSheet(f"font-size: 10px; color: {Theme.TEXT_DIM};")
        gpu_lay.addWidget(self.d_gpu_detail)
        self.d_gpu_chart = BigSparkLine(Theme.GPU)
        gpu_lay.addWidget(self.d_gpu_chart, 1)
        left.addWidget(gpu_box, 1)

        # ── 右列: RAM + Temp/Power + Network ──
        # RAM
        ram_box = self._group("Memory / RAM", Theme.RAM)
        ram_lay = QVBoxLayout(ram_box)
        ram_lay.setContentsMargins(8, 16, 8, 6)
        row = QHBoxLayout()
        self.d_ram_lbl = QLabel("-- / --")
        self.d_ram_lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Theme.TEXT_BRIGHT};")
        self.d_ram_bar = GaugeBar(10, color=Theme.RAM)
        row.addWidget(self.d_ram_lbl, 0)
        row.addWidget(self.d_ram_bar, 1)
        ram_lay.addLayout(row)
        self.d_ram_info = QLabel("")
        self.d_ram_info.setStyleSheet(f"font-size: 11px; color: {Theme.TEXT_DIM};")
        ram_lay.addWidget(self.d_ram_info)
        right.addWidget(ram_box)

        # Temp & Power
        tp_box = self._group("Temperature & Power", Theme.TEMP)
        tp_grid = QGridLayout(tp_box)
        tp_grid.setContentsMargins(8, 18, 8, 6)
        tp_grid.setSpacing(6)

        items = [
            (0, 0, "CPU Temp:", "d_cpu_temp"),
            (0, 2, "GPU Temp:", "d_gpu_temp"),
            (1, 0, "CPU Power:", "d_cpu_pwr"),
            (1, 2, "GPU Power:", "d_gpu_pwr"),
            (2, 0, "DRAM:", "d_dram_pwr"),
            (2, 2, "Total:", "d_total_pwr"),
            (3, 0, "Charging:", "d_charge_pwr"),
        ]
        for r, c, text, attr in items:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {Theme.TEXT_DIM}; font-size: 11px;")
            val = QLabel("--")
            val.setStyleSheet(f"font-weight: bold; color: {Theme.TEXT_BRIGHT}; font-size: 12px;")
            setattr(self, attr, val)
            tp_grid.addWidget(lbl, r, c)
            tp_grid.addWidget(val, r, c + 1)
        right.addWidget(tp_box)

        # Network
        net_box = self._group("Network", Theme.NET_DL)
        net_lay = QVBoxLayout(net_box)
        net_lay.setContentsMargins(8, 18, 8, 6)
        row = QHBoxLayout()
        self.d_net_dl = QLabel("↓ --")
        self.d_net_dl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Theme.NET_DL};")
        self.d_net_ul = QLabel("↑ --")
        self.d_net_ul.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Theme.NET_UL};")
        row.addWidget(self.d_net_dl)
        row.addStretch()
        row.addWidget(self.d_net_ul)
        net_lay.addLayout(row)
        self.d_net_total = QLabel("")
        self.d_net_total.setStyleSheet(f"font-size: 10px; color: {Theme.TEXT_DIM};")
        self.d_net_total.setAlignment(Qt.AlignmentFlag.AlignCenter)
        net_lay.addWidget(self.d_net_total)
        self.d_net_chart = BigDualSparkLine()
        net_lay.addWidget(self.d_net_chart, 1)
        right.addWidget(net_box, 1)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        root.addLayout(cols, 1)

    @staticmethod
    def _group(title, color):
        g = QGroupBox(title)
        g.setStyleSheet(g.styleSheet() + f"QGroupBox {{ color: {color}; }}")
        return g

    def _toggle_pin(self, on):
        # 使用 macOS 原生 API 设置置顶，避免 setWindowFlags 导致窗口重建崩溃
        try:
            from AppKit import NSApplication, NSFloatingWindowLevel, NSNormalWindowLevel
            ns_win = None
            for w in NSApplication.sharedApplication().windows():
                if int(w.windowNumber()) == int(self.winId()):
                    ns_win = w
                    break
            if ns_win:
                ns_win.setLevel_(NSFloatingWindowLevel if on else NSNormalWindowLevel)
                return
        except Exception:
            pass
        # Fallback: 使用 Qt 方式但保持窗口位置和大小
        pos = self.pos()
        size = self.size()
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.resize(size)
        self.move(pos)
        if was_visible:
            self.show()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# About 关于窗口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AboutWindow(QWidget):
    """macOS 风格的关于窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(320, 280)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if _DARK_MODE:
            self.setStyleSheet("background-color: #000000;")
        else:
            self.setStyleSheet("background-color: #f0f0f0;")
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 24, 30, 20)
        lay.setSpacing(6)

        # App 图标
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(72, 72)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_path = Path(__file__).resolve().parent / "icon.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_lbl.setPixmap(pix)
        else:
            icon_lbl.setText("SYS")
            icon_lbl.setStyleSheet(
                "font-size: 18px; font-weight: 900; color: #fff;"
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 {Theme.CPU}, stop:1 {Theme.GPU});"
                "border-radius: 14px;"
            )

        lay.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(8)

        # App 名称
        name = QLabel(APP_NAME)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {Theme.TEXT_BRIGHT};")
        lay.addWidget(name)

        # 版本号
        ver = QLabel(f"Version {APP_VERSION}")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet(f"font-size: 12px; color: {Theme.TEXT_DIM};")
        lay.addWidget(ver)

        lay.addSpacing(10)

        # 描述
        self._desc_lbl = QLabel(_t("description"))
        self._desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setStyleSheet(f"font-size: 11px; color: {Theme.TEXT}; line-height: 1.4;")
        lay.addWidget(self._desc_lbl)

        lay.addSpacing(6)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {Theme.BORDER}; max-height: 1px;")
        lay.addWidget(sep)

        lay.addSpacing(6)

        # 开发者
        dev = QLabel(f"Developer: {APP_DEVELOPER}")
        dev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dev.setStyleSheet(f"font-size: 11px; color: {Theme.TEXT};")
        lay.addWidget(dev)

        # 技术栈
        tech = QLabel("Built with Python · PyQt6 · IOKit")
        tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tech.setStyleSheet(f"font-size: 10px; color: {Theme.TEXT_DIM};")
        lay.addWidget(tech)

        lay.addSpacing(6)

        # GitHub
        gh_lbl = QLabel('<a href="https://github.com/zhxmarshall/macos-system-monitor" style="color: #6ea8fe;">GitHub</a>')
        gh_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gh_lbl.setOpenExternalLinks(True)
        gh_lbl.setStyleSheet(f"font-size: 10px;")
        lay.addWidget(gh_lbl)

        lay.addSpacing(4)

        # 版权
        self._cr_lbl = QLabel(_t("copyright"))
        self._cr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cr_lbl.setStyleSheet(f"font-size: 9px; color: {Theme.TEXT_DIM};")
        lay.addWidget(self._cr_lbl)

        lay.addStretch()

    def update_lang(self):
        self._desc_lbl.setText(_t("description"))
        self._cr_lbl.setText(_t("copyright"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 菜单栏应用
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MonitorApp(QApplication):
    _CONFIG_DIR = Path.home() / "Library/Application Support/SystemMonitor"
    _CONFIG_FILE = _CONFIG_DIR / "config.json"

    def __init__(self, argv):
        super().__init__(argv)
        # ⚠️ 必须先加载 config 才能确定主题，然后设置 NSAppearance
        self._load_menubar_config()
        self._apply_ns_appearance()
        self.setQuitOnLastWindowClosed(False)

        self._last_icon_text = None
        self._icon_font_single = QFont(".AppleSystemUIFont", 16, QFont.Weight.Bold)
        self._icon_font_dual = QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold)
        # config 已在 __init__ 中加载，无需重复

        self._widget = MonitorWidget()
        self._widget.cpu_updated.connect(self._update_icon)

        # Dashboard 和 About 窗口（默认隐藏）
        self._dashboard = DashboardWindow()
        self._about = AboutWindow()
        self._widget.cpu_updated.connect(self._update_dashboard)

        # 系统托盘
        self._tray = QSystemTrayIcon(self)
        # 初始图标（snapshot 还没数据，手动画一个默认文字）
        self._render_initial_icon()

        # 下拉菜单
        self._menu_style = self._build_menu_style()
        self._menu = QMenu()
        self._menu.setStyleSheet(self._menu_style)

        # 强制 widget 完成首次 layout
        self._widget.ensurePolished()
        self._widget.layout().activate()

        # 嵌入监控面板
        wa = QWidgetAction(self._menu)
        wa.setDefaultWidget(self._widget)
        self._menu.addAction(wa)
        self._menu.addSeparator()

        # Dashboard
        self._dashboard_act = self._menu.addAction("", self._toggle_dashboard)
        self._menu.addSeparator()

        # 菜单栏显示配置
        self._build_menubar_submenus()
        self._menu.addSeparator()

        # 开机启动
        self._login_act = self._menu.addAction("")
        self._login_act.setCheckable(True)
        self._login_act.setChecked(self._is_login_enabled())
        self._login_act.toggled.connect(self._toggle_login)

        # Dashboard 置顶
        self._pin_act = self._menu.addAction("")
        self._pin_act.setCheckable(True)
        self._pin_act.setChecked(False)
        self._pin_act.toggled.connect(self._dashboard._toggle_pin)

        # 语言
        self._lang_menu = QMenu()
        self._lang_actions = {}
        for code, name in LANG_NAMES.items():
            act = self._lang_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(code == _current_lang)
            act.triggered.connect(lambda checked, c=code: self._set_language(c))
            self._lang_actions[code] = act
        self._menu.addMenu(self._lang_menu)

        self._about_act = self._menu.addAction("", self._show_about)
        self._menu.addSeparator()
        self._quit_act = self._menu.addAction("", self._do_quit)

        # 给所有子菜单也应用同一样式
        for sub in (self._line1_menu, self._line2_menu, self._lang_menu):
            sub.setStyleSheet(self._menu_style)

        # 设置所有文本
        self._update_menu_text()

        self._tray.setContextMenu(self._menu)
        self._tray.show()

        # 菜单创建后再次强制 NSAppearance（确保 NSMenu 也受影响）
        QTimer.singleShot(100, self._apply_ns_appearance)

        # 静默打开关闭一次菜单，完成首次 layout pass
        # 这样用户第一次真正打开时不会因为 sizeHint 不准而自动关闭
        QTimer.singleShot(500, self._warmup_menu)

    def _apply_ns_appearance(self):
        """设置 macOS 原生外观（影响 NSMenu 等原生控件）"""
        try:
            from AppKit import NSApplication, NSAppearance
            if _DARK_MODE:
                from AppKit import NSAppearanceNameDarkAqua
                NSApplication.sharedApplication().setAppearance_(
                    NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua)
                )
            else:
                from AppKit import NSAppearanceNameAqua
                NSApplication.sharedApplication().setAppearance_(
                    NSAppearance.appearanceNamed_(NSAppearanceNameAqua)
                )
        except Exception:
            pass

    def _warmup_menu(self):
        """静默完成首次 layout，防止首次打开菜单时自动关闭。"""
        self._widget.adjustSize()
        self._widget.updateGeometry()
        self._menu.adjustSize()
        # 在屏幕外弹出并立即关闭，强制 QMenu 完成首次 native layout pass
        self._menu.popup(QPoint(-9999, -9999))
        QTimer.singleShot(50, self._menu.close)

    def _update_menu_text(self):
        """更新所有菜单项文本（切换语言时调用）"""
        self._dashboard_act.setText(_t("show_dashboard"))
        self._line1_menu.setTitle(_t("line1"))
        self._line2_menu.setTitle(_t("line2"))
        self._bot_actions[""].setText(_t("none"))
        self._login_act.setText(_t("start_login"))
        self._pin_act.setText(_t("pin_dashboard"))
        self._lang_menu.setTitle(_t("language"))
        self._about_act.setText(_t("about"))
        self._quit_act.setText(_t("quit"))
        for code, act in self._lang_actions.items():
            act.setChecked(code == _current_lang)

    # ── 菜单栏图标 ──

    @staticmethod
    def _build_menu_style():
        sel_bg = "rgba(255,255,255,0.06)" if _DARK_MODE else "rgba(0,0,0,0.06)"
        return f"""
            QMenu {{
                background-color: {'#000000' if _DARK_MODE else '#f5f5f5'};
                border: 1px solid {Theme.BORDER};
                border-radius: 12px;
                padding: 4px 0;
            }}
            QMenu::item {{
                padding: 6px 16px;
                color: {Theme.TEXT};
                font-size: 12px;
            }}
            QMenu::item:selected {{
                background: {sel_bg};
                color: {Theme.TEXT_BRIGHT};
            }}
            QMenu::separator {{
                height: 1px;
                background: {Theme.BORDER};
                margin: 3px 8px;
            }}
        """

    def _render_initial_icon(self):
        text = "..."
        font = self._icon_font_single
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        dpr = 2
        pw, ph = (tw + 4) * dpr, 22 * dpr
        pix = QPixmap(pw, ph)
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(QRect(0, 0, pw // dpr, 22), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        icon = QIcon(pix)
        icon.setIsMask(True)
        self._tray.setIcon(icon)

    def _render_icon(self):
        snapshot = self._widget._snapshot
        if not snapshot:
            return
        top_key = self._menubar_top
        bot_key = self._menubar_bottom
        lines = []
        if top_key and top_key in MENUBAR_ITEMS:
            lines.append(MENUBAR_ITEMS[top_key][1](snapshot))
        if bot_key and bot_key in MENUBAR_ITEMS:
            lines.append(MENUBAR_ITEMS[bot_key][1](snapshot))
        if not lines:
            lines.append(f"{snapshot.get('cpu', 0):.0f}%")

        text_key = "\n".join(lines)
        if text_key == self._last_icon_text:
            return
        self._last_icon_text = text_key

        dpr = 2
        h = 22
        if len(lines) == 1:
            font = self._icon_font_single
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(lines[0])
            pw, ph = (tw + 4) * dpr, h * dpr
            pix = QPixmap(pw, ph)
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setFont(font)
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(QRect(0, 0, pw // dpr, h), Qt.AlignmentFlag.AlignCenter, lines[0])
            painter.end()
        else:
            font = self._icon_font_dual
            fm = QFontMetrics(font)
            tw = max(fm.horizontalAdvance(lines[0]), fm.horizontalAdvance(lines[1]))
            pw, ph = (tw + 2) * dpr, h * dpr
            pix = QPixmap(pw, ph)
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setFont(font)
            painter.setPen(QColor(0, 0, 0))
            lw = pw // dpr
            painter.drawText(QRect(0, -1, lw, h // 2 + 1), Qt.AlignmentFlag.AlignCenter, lines[0])
            painter.drawText(QRect(0, h // 2, lw, h // 2 + 1), Qt.AlignmentFlag.AlignCenter, lines[1])
            painter.end()
        icon = QIcon(pix)
        icon.setIsMask(True)
        self._tray.setIcon(icon)

    def _update_icon(self, _pct):
        self._render_icon()

    # ── Dashboard ──

    def _toggle_dashboard(self):
        if self._dashboard.isVisible():
            self._dashboard.raise_()
            self._dashboard.activateWindow()
        else:
            self._dashboard.show()
            self._dashboard.raise_()

    def _update_dashboard(self, cpu_pct):
        d = self._dashboard
        if not d.isVisible():
            return
        try:
            self._do_update_dashboard(cpu_pct)
        except Exception as e:
            print(f"[Dashboard update error] {e}")

    def _do_update_dashboard(self, cpu_pct):
        d = self._dashboard
        s = self._widget._snapshot
        if not s:
            return

        # CPU
        d.d_cpu_lbl.setText(f"{cpu_pct:.1f}%")
        d.d_cpu_bar.set_value(cpu_pct)
        d.d_cpu_chart.append(cpu_pct)

        # GPU — reuse gpu_pct from snapshot
        gpu = s.get("gpu", {})
        gpu_pct = s.get("gpu_pct", 0)
        if gpu:
            d.d_gpu_lbl.setText(f"{gpu_pct:.0f}%")
            d.d_gpu_bar.set_value(gpu_pct)
            d.d_gpu_chart.append(gpu_pct)
            details = [f"{k}: {v:.0f}%" for k, v in gpu.items()
                       if k != "Device Utilization %"]
            d.d_gpu_detail.setText("  ".join(details))
        else:
            d.d_gpu_lbl.setText("N/A")

        # RAM — reuse snapshot
        vm = s.get("vm")
        if vm:
            used = s["used"]
            ram_pct = s["ram_pct"]
            d.d_ram_lbl.setText(f"{fmt_bytes(used)} / {fmt_bytes(vm.total)}")
            d.d_ram_bar.set_value(ram_pct)
            d.d_ram_info.setText(
                f"Available: {fmt_bytes(vm.available)}    "
                f"Active: {fmt_bytes(vm.active)}  Wired: {fmt_bytes(vm.wired)}"
            )

        # Temp — reuse snapshot
        temps = s.get("temps", {})
        ct = temps.get("cpu_temp")
        gt = temps.get("gpu_temp")
        d.d_cpu_temp.setText(f"{ct:.1f} °C" if ct else "--")
        d.d_gpu_temp.setText(f"{gt:.1f} °C" if gt else "--")

        # Power — reuse snapshot
        pwr = s.get("pwr", {})
        if pwr:
            d.d_cpu_pwr.setText(f"{pwr.get('cpu_power', 0):.2f} W")
            d.d_gpu_pwr.setText(f"{pwr.get('gpu_power', 0):.2f} W")
            d.d_dram_pwr.setText(f"{pwr.get('dram_power', 0):.2f} W")
            d.d_total_pwr.setText(f"{pwr.get('total_power', 0):.2f} W")

        # Charging — only update stylesheet when state changes
        bat = s.get("battery", {})
        if bat.get("charging") and bat.get("charge_watts", 0) > 0:
            text = f"⚡ {bat['charge_watts']:.1f} W ({bat['percent']}%)"
            color = "#4CAF50"
        elif bat.get("plugged"):
            text = f"🔌 {bat.get('percent', 0)}%"
            color = Theme.TEXT_BRIGHT
        else:
            text = f"🔋 {bat.get('percent', 0)}%"
            color = Theme.TEXT_BRIGHT
        d.d_charge_pwr.setText(text)
        new_style = f"font-weight: bold; color: {color}; font-size: 12px;"
        if getattr(self, '_last_charge_style', None) != new_style:
            d.d_charge_pwr.setStyleSheet(new_style)
            self._last_charge_style = new_style

        # Network — reuse snapshot, no double get_speeds()
        net = s.get("net", {})
        if net:
            d.d_net_dl.setText(f"↓ {fmt_speed(net['download_speed'])}")
            d.d_net_ul.setText(f"↑ {fmt_speed(net['upload_speed'])}")
            d.d_net_total.setText(
                f"Total received: {NetworkMonitor.format_total(net['bytes_recv_total'])}    "
                f"Total sent: {NetworkMonitor.format_total(net['bytes_sent_total'])}"
            )
            d.d_net_chart.append(
                net["download_speed"] / 1024,
                net["upload_speed"] / 1024,
            )

    # ── 菜单栏显示配置 ──

    def _load_menubar_config(self):
        global _current_lang, _DARK_MODE
        self._menubar_top = MENUBAR_DEFAULT_TOP
        self._menubar_bottom = MENUBAR_DEFAULT_BOTTOM
        _current_lang = DEFAULT_LANG
        try:
            if self._CONFIG_FILE.exists():
                cfg = json.loads(self._CONFIG_FILE.read_text())
                self._menubar_top = cfg.get("menubar_top", MENUBAR_DEFAULT_TOP)
                self._menubar_bottom = cfg.get("menubar_bottom", MENUBAR_DEFAULT_BOTTOM)
                lang = cfg.get("lang", DEFAULT_LANG)
                if lang in I18N:
                    _current_lang = lang
        except Exception:
            pass
        _DARK_MODE = _system_is_dark()
        _apply_theme()

    def _save_config(self):
        self._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._CONFIG_FILE.write_text(json.dumps({
            "menubar_top": self._menubar_top,
            "menubar_bottom": self._menubar_bottom,
            "lang": _current_lang,
        }))

    def _build_menubar_submenus(self):
        """两个悬浮子菜单：鼠标悬停展开选择"""
        self._line1_menu = QMenu(_t("line1"))
        self._top_actions = {}
        for key, (label, _) in MENUBAR_ITEMS.items():
            act = self._line1_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == self._menubar_top)
            act.triggered.connect(lambda checked, k=key: self._set_menubar_top(k))
            self._top_actions[key] = act
        self._menu.addMenu(self._line1_menu)

        self._line2_menu = QMenu(_t("line2"))
        self._bot_actions = {}
        none_act = self._line2_menu.addAction(_t("none"))
        none_act.setCheckable(True)
        none_act.setChecked(self._menubar_bottom == "")
        none_act.triggered.connect(lambda checked: self._set_menubar_bottom(""))
        self._bot_actions[""] = none_act
        for key, (label, _) in MENUBAR_ITEMS.items():
            act = self._line2_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == self._menubar_bottom)
            act.triggered.connect(lambda checked, k=key: self._set_menubar_bottom(k))
            self._bot_actions[key] = act
        self._menu.addMenu(self._line2_menu)

    def _set_language(self, code):
        global _current_lang
        _current_lang = code
        self._save_config()
        self._update_menu_text()
        self._about.update_lang()

    def _set_menubar_top(self, key):
        self._menubar_top = key
        for k, act in self._top_actions.items():
            act.setChecked(k == key)
        self._last_icon_text = None  # force re-render
        self._save_config()

    def _set_menubar_bottom(self, key):
        self._menubar_bottom = key
        for k, act in self._bot_actions.items():
            act.setChecked(k == key)
        self._last_icon_text = None
        self._save_config()

    # ── 开机启动 ──

    @staticmethod
    def _launch_agent_path():
        return Path.home() / "Library/LaunchAgents/com.local.systemmonitor.plist"

    def _is_login_enabled(self):
        return self._launch_agent_path().exists()

    def _toggle_login(self, on):
        plist_path = self._launch_agent_path()
        if on:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            # 检测是否从 .app 运行
            app_bundle = None
            for parent in Path(__file__).resolve().parents:
                if parent.suffix == ".app":
                    app_bundle = str(parent)
                    break
            if app_bundle:
                prog_args = (
                    '  <array>\n'
                    '    <string>open</string>\n'
                    f'    <string>{app_bundle}</string>\n'
                    '  </array>\n'
                )
            else:
                prog_args = (
                    '  <array>\n'
                    f'    <string>{sys.executable}</string>\n'
                    f'    <string>{Path(__file__).resolve()}</string>\n'
                    '  </array>\n'
                )
            content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
                '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n'
                '  <key>Label</key>\n'
                '  <string>com.local.systemmonitor</string>\n'
                '  <key>ProgramArguments</key>\n'
                + prog_args +
                '  <key>RunAtLoad</key>\n'
                '  <true/>\n'
                '</dict>\n</plist>\n'
            )
            plist_path.write_text(content)
        else:
            plist_path.unlink(missing_ok=True)

    # ── 关于 ──

    def _show_about(self):
        self._about.show()
        self._about.raise_()
        self._about.activateWindow()

    # ── 退出 ──

    def _do_quit(self):
        self._widget.stop()
        self.quit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    import signal, fcntl
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # 允许 Ctrl+C 退出

    # 单实例锁：防止多开
    lock_path = Path.home() / "Library/Application Support/SystemMonitor/app.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("[System Monitor] Already running, exiting.", flush=True)
        sys.exit(0)

    app = MonitorApp(sys.argv)

    # 全局异常处理：防止未捕获异常导致静默退出
    def _exception_hook(exc_type, exc_value, exc_tb):
        import traceback
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(f"[System Monitor] Unhandled exception:\n{msg}", file=sys.stderr, flush=True)
        # 不调用 sys.exit，让 app 继续运行
    sys.excepthook = _exception_hook

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
