#!/usr/bin/env python3
"""
System Monitor — 自动化测试
测试所有核心功能：主题系统、数据采集、后台线程、UI 组件等。
"""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# 使用 offscreen 平台避免弹窗
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 必须在导入任何 Qt widget 之前创建 QApplication
from PyQt6.QtWidgets import QApplication
_qapp = QApplication.instance() or QApplication([])


# ============================================================
# apple_metrics 测试
# ============================================================

class TestPollingReader(unittest.TestCase):
    """测试 PollingReader 基类。"""

    def test_start_stop(self):
        from apple_metrics import PollingReader
        call_count = {"n": 0}
        def poll_fn():
            call_count["n"] += 1
            return {"value": call_count["n"]}
        reader = PollingReader(poll_fn, {"value": 0}, interval=0.1)
        reader.start()
        time.sleep(0.35)
        reader.stop()
        self.assertGreaterEqual(call_count["n"], 2)
        data = reader.latest
        self.assertIn("value", data)
        self.assertGreater(data["value"], 0)

    def test_latest_returns_copy(self):
        from apple_metrics import PollingReader
        reader = PollingReader(lambda: {"a": 1}, {"a": 0}, interval=1.0)
        reader._data = {"a": 42}
        d = reader.latest
        d["a"] = 999
        self.assertEqual(reader.latest["a"], 42)

    def test_default_data(self):
        from apple_metrics import PollingReader
        reader = PollingReader(lambda: {}, {"default": True}, interval=1.0)
        self.assertEqual(reader.latest, {"default": True})


class TestGPUReader(unittest.TestCase):
    def test_instantiate(self):
        from apple_metrics import GPUReader
        r = GPUReader(interval=5.0)
        self.assertEqual(r._interval, 5.0)
        self.assertEqual(r.latest, {})


class TestTempReader(unittest.TestCase):
    def test_instantiate(self):
        from apple_metrics import TempReader
        r = TempReader(interval=5.0)
        self.assertEqual(r._interval, 5.0)
        d = r.latest
        self.assertIn("cpu_temp", d)
        self.assertIn("gpu_temp", d)


class TestBatteryReader(unittest.TestCase):
    def test_instantiate(self):
        from apple_metrics import BatteryReader
        r = BatteryReader(interval=30.0)
        self.assertEqual(r._interval, 30.0)
        d = r.latest
        for key in ("plugged", "charging", "percent", "charge_watts"):
            self.assertIn(key, d)


class TestGetBatteryInfo(unittest.TestCase):
    def test_returns_dict_with_required_keys(self):
        from apple_metrics import get_battery_info
        info = get_battery_info()
        self.assertIsInstance(info, dict)
        for key in ("plugged", "charging", "percent", "charge_watts"):
            self.assertIn(key, info)
        self.assertIsInstance(info["plugged"], bool)
        self.assertIsInstance(info["charging"], bool)
        self.assertIsInstance(info["percent"], int)
        self.assertIsInstance(info["charge_watts"], float)
        self.assertGreaterEqual(info["percent"], 0)
        self.assertLessEqual(info["percent"], 100)

    @patch("apple_metrics.subprocess.run", side_effect=Exception("fail"))
    def test_returns_default_on_error(self, mock_run):
        from apple_metrics import get_battery_info
        info = get_battery_info()
        self.assertEqual(info["plugged"], False)
        self.assertEqual(info["percent"], 0)


class TestPowerReader(unittest.TestCase):
    def test_instantiate(self):
        from apple_metrics import PowerReader
        r = PowerReader()
        r.latest  # 不抛异常即可


class TestNetworkMonitor(unittest.TestCase):
    def test_get_speeds(self):
        from apple_metrics import NetworkMonitor
        mon = NetworkMonitor()
        s = mon.get_speeds()
        for key in ("download_speed", "upload_speed", "bytes_recv_total", "bytes_sent_total"):
            self.assertIn(key, s)

    def test_format_total(self):
        from apple_metrics import NetworkMonitor
        self.assertIn("B", NetworkMonitor.format_total(500))
        self.assertIn("KB", NetworkMonitor.format_total(2000))
        self.assertIn("MB", NetworkMonitor.format_total(2_000_000))
        self.assertIn("GB", NetworkMonitor.format_total(2_000_000_000))

    def test_format_speed(self):
        from apple_metrics import NetworkMonitor
        self.assertIn("B/s", NetworkMonitor.format_speed(500))
        self.assertIn("KB/s", NetworkMonitor.format_speed(2000))
        self.assertIn("MB/s", NetworkMonitor.format_speed(2_000_000))


# ============================================================
# app.py 主题系统测试
# ============================================================

class TestThemeSystem(unittest.TestCase):
    def test_system_is_dark_returns_bool(self):
        import app
        result = app._system_is_dark()
        self.assertIsInstance(result, bool)

    def test_apply_theme_dark(self):
        import app
        app._DARK_MODE = True
        app._apply_theme()
        self.assertEqual(app.Theme.BG, "#000000")
        self.assertEqual(app.Theme.TEXT_BRIGHT, "#ffffff")
        self.assertEqual(app.Theme.TRACK, "#1a1a1a")

    def test_apply_theme_light(self):
        import app
        app._DARK_MODE = False
        app._apply_theme()
        self.assertEqual(app.Theme.BG, "transparent")
        self.assertEqual(app.Theme.TEXT_BRIGHT, "#000000")
        self.assertEqual(app.Theme.TRACK, "#e6e6e6")

    def test_theme_colors_are_valid_qcolors(self):
        """确保所有 Theme 颜色值都是有效的 QColor。"""
        import app
        from PyQt6.QtGui import QColor
        for dark in (True, False):
            app._DARK_MODE = dark
            app._apply_theme()
            for attr in ("TEXT", "TEXT_DIM", "TEXT_BRIGHT", "CPU", "GPU", "RAM",
                         "TEMP", "POWER", "NET_DL", "NET_UL", "TRACK",
                         "BAR_LOW", "BAR_MID", "BAR_HIGH"):
                val = getattr(app.Theme, attr)
                c = QColor(val)
                self.assertTrue(c.isValid(), f"Theme.{attr}={val!r} invalid (dark={dark})")

    def test_no_theme_selection_globals(self):
        """确认已移除手动主题选择相关的全局变量。"""
        import app
        self.assertFalse(hasattr(app, '_theme_setting'))
        self.assertFalse(hasattr(app, '_is_dark_mode'))


# ============================================================
# UI 组件测试
# ============================================================

class TestGaugeBar(unittest.TestCase):
    def test_set_value_clamp(self):
        import app
        bar = app.GaugeBar()
        bar.set_value(150)
        self.assertEqual(bar._v, 100)
        bar.set_value(-10)
        self.assertEqual(bar._v, 0)
        bar.set_value(42)
        self.assertEqual(bar._v, 42)

    def test_with_fixed_color(self):
        import app
        bar = app.GaugeBar(color="#ff0000")
        self.assertEqual(bar._fixed_color, "#ff0000")

    def test_set_color(self):
        import app
        bar = app.GaugeBar()
        bar.set_color("#00ff00")
        self.assertEqual(bar._fixed_color, "#00ff00")

    def test_no_cached_track_color(self):
        """GaugeBar 不应缓存 track 颜色，每次 paint 实时读 Theme。"""
        import app
        bar = app.GaugeBar()
        self.assertFalse(hasattr(bar, '_track_color'))


class TestSparkLine(unittest.TestCase):
    def test_append_data(self):
        import app
        spark = app.SparkLine()
        for i in range(10):
            spark.append(float(i))
        self.assertEqual(len(spark._data), app.HIST)
        self.assertEqual(spark._data[-1], 9.0)

    def test_set_color(self):
        from PyQt6.QtGui import QColor
        import app
        spark = app.SparkLine()
        spark.set_color("#ff0000")
        self.assertEqual(spark._color, QColor("#ff0000"))


class TestDualSparkLine(unittest.TestCase):
    def test_append(self):
        import app
        ds = app.DualSparkLine()
        ds.append(100, 50)
        self.assertEqual(ds._dl[-1], 100)
        self.assertEqual(ds._ul[-1], 50)


# ============================================================
# 格式化函数测试
# ============================================================

class TestFormatters(unittest.TestCase):
    def test_fmt_bytes(self):
        import app
        self.assertIn("B", app.fmt_bytes(500))
        self.assertIn("KB", app.fmt_bytes(1500))
        self.assertIn("MB", app.fmt_bytes(1_500_000))
        self.assertIn("GB", app.fmt_bytes(1_500_000_000))

    def test_fmt_speed(self):
        import app
        self.assertIn("B/s", app.fmt_speed(500))
        self.assertIn("KB/s", app.fmt_speed(1500))
        self.assertIn("MB/s", app.fmt_speed(1_500_000))

    def test_fmt_speed_short(self):
        import app
        self.assertIn("B", app._fmt_speed_short(500))
        self.assertIn("K", app._fmt_speed_short(1500))
        self.assertIn("M", app._fmt_speed_short(1_500_000))


# ============================================================
# i18n 测试
# ============================================================

class TestI18N(unittest.TestCase):
    def test_all_languages_have_same_keys(self):
        import app
        en_keys = set(app.I18N["en"].keys())
        for lang, strings in app.I18N.items():
            self.assertEqual(set(strings.keys()), en_keys,
                             f"Language '{lang}' has different keys than 'en'")

    def test_t_function(self):
        import app
        app._current_lang = "en"
        self.assertEqual(app._t("quit"), "Quit")
        app._current_lang = "zh"
        self.assertEqual(app._t("quit"), "退出")
        app._current_lang = "ja"
        self.assertEqual(app._t("quit"), "終了")

    def test_t_fallback_to_en(self):
        import app
        app._current_lang = "xx"
        self.assertEqual(app._t("quit"), "Quit")


# ============================================================
# 配置测试
# ============================================================

class TestMenubarItems(unittest.TestCase):
    def test_lambdas_return_strings(self):
        import app
        snapshot = {
            "cpu": 25.0, "gpu_pct": 30.0, "ram_pct": 65.0,
            "cpu_temp": 42, "total_power": 8.5,
            "net_ul": 1024, "net_dl": 2048,
        }
        for key, (label, fn) in app.MENUBAR_ITEMS.items():
            result = fn(snapshot)
            self.assertIsInstance(result, str, f"MENUBAR_ITEMS[{key}]")
            self.assertTrue(len(result) > 0, f"MENUBAR_ITEMS[{key}] empty")


# ============================================================
# MonitorWidget 集成测试
# ============================================================

class TestMonitorWidget(unittest.TestCase):
    def test_widget_creates_without_crash(self):
        import app
        w = app.MonitorWidget()
        self.assertEqual(w.width(), 320)
        w.stop()

    def test_refresh_styles_both_themes(self):
        import app
        w = app.MonitorWidget()
        # 深色
        app._DARK_MODE = True
        app._apply_theme()
        w._refresh_styles()
        # 浅色
        app._DARK_MODE = False
        app._apply_theme()
        w._refresh_styles()
        w.stop()

    def test_metric_row_returns_expected_keys(self):
        import app
        w = app.MonitorWidget()
        row = w._metric_row("TEST")
        for key in ("layout", "bar", "val", "lbl"):
            self.assertIn(key, row)
        w.stop()

    def test_separator_height(self):
        import app
        w = app.MonitorWidget()
        sep = w._sep()
        self.assertEqual(sep.maximumHeight(), 1)
        w.stop()


# ============================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
