"""
Apple Silicon metrics reader - no sudo needed.

GPU usage:    ioreg AGXAccelerator PerformanceStatistics
Power data:   IOReport "Energy Model" via libIOReport.dylib
Temperature:  IOHIDEventSystemClient thermal sensors
Network:      psutil net_io_counters
"""

import collections
import ctypes
import ctypes.util
import plistlib
import subprocess
import threading
import time

import psutil

import smc


# ============================================================
# Shared: load frameworks once
# ============================================================

_cf_path = ctypes.util.find_library("CoreFoundation")
_iokit_path = ctypes.util.find_library("IOKit")

_cf = ctypes.cdll.LoadLibrary(_cf_path) if _cf_path else None
_iokit = ctypes.cdll.LoadLibrary(_iokit_path) if _iokit_path else None

_kCFStringEncodingUTF8 = 0x08000100
_kCFNumberSInt32Type = 3

if _cf:
    _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    _cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
    ]
    _cf.CFStringGetLength.restype = ctypes.c_long
    _cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
    _cf.CFStringGetCString.restype = ctypes.c_bool
    _cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32,
    ]
    _cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    _cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
    _cf.CFDictionaryCreateMutable.argtypes = [
        ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _cf.CFDictionarySetValue.restype = None
    _cf.CFDictionarySetValue.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _cf.CFArrayGetCount.restype = ctypes.c_long
    _cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    _cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    _cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    _cf.CFRelease.restype = None
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _cf.CFNumberCreate.restype = ctypes.c_void_p
    _cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]


def _mk_cfstr(s: str):
    return _cf.CFStringCreateWithCString(
        None, s.encode("utf-8"), _kCFStringEncodingUTF8,
    )


def _cfstr_to_py(ref) -> str:
    if not ref:
        return ""
    length = _cf.CFStringGetLength(ref)
    buf = ctypes.create_string_buffer(length * 4 + 1)
    _cf.CFStringGetCString(ref, buf, len(buf), _kCFStringEncodingUTF8)
    return buf.value.decode("utf-8")


# ============================================================
# GPU Usage via ioreg (no sudo) — background thread
# ============================================================

_IOREG_GPU_CMD = ["ioreg", "-c", "AGXAccelerator", "-r", "-d", "1", "-a"]

_GPU_UTIL_KEYS = [
    "Device Utilization %",
    "Renderer Utilization %",
    "GPU Core Utilization(%)",
    "GPU Activity(%)",
    "Tiler Utilization %",
]


def _poll_gpu_usage() -> dict:
    """Read GPU utilization from ioreg. Returns dict or empty."""
    try:
        proc = subprocess.run(_IOREG_GPU_CMD, capture_output=True, timeout=2)
        if proc.returncode != 0 or not proc.stdout:
            return {}
        entries = plistlib.loads(proc.stdout)
        result = {}
        for entry in entries:
            perf = entry.get("PerformanceStatistics", {})
            for key in _GPU_UTIL_KEYS:
                if key in perf and isinstance(perf[key], (int, float)):
                    result[key] = min(float(perf[key]), 100.0)
        return result
    except Exception:
        return {}


class PollingReader:
    """Generic background-thread poller. Subclasses just set _poll_fn and _default."""

    def __init__(self, poll_fn, default, interval: float):
        self._poll_fn = poll_fn
        self._interval = interval
        self._lock = threading.Lock()
        self._data = default
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    @property
    def latest(self):
        with self._lock:
            return dict(self._data) if isinstance(self._data, dict) else self._data

    def _loop(self):
        while self._running:
            data = self._poll_fn()
            with self._lock:
                self._data = data
            time.sleep(self._interval)


class GPUReader(PollingReader):
    def __init__(self, interval: float = 1.0):
        super().__init__(_poll_gpu_usage, {}, interval)


# Keep simple function for one-shot use
def get_gpu_usage() -> dict:
    return _poll_gpu_usage()


# ============================================================
# Temperature via IOHIDEventSystemClient (no sudo!)
# — Pre-cached CF objects to avoid leaks
# ============================================================

_TEMP_AVAILABLE = False
_kIOHIDEventTypeTemperature = 15

# Pre-cached CF objects for temperature matching (created once, never released)
_temp_matching = None
_temp_product_key = None

if _iokit and _cf:
    try:
        _iokit.IOHIDEventSystemClientCreate.restype = ctypes.c_void_p
        _iokit.IOHIDEventSystemClientCreate.argtypes = [ctypes.c_void_p]
        _iokit.IOHIDEventSystemClientSetMatching.restype = None
        _iokit.IOHIDEventSystemClientSetMatching.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        _iokit.IOHIDEventSystemClientCopyServices.restype = ctypes.c_void_p
        _iokit.IOHIDEventSystemClientCopyServices.argtypes = [ctypes.c_void_p]
        _iokit.IOHIDServiceClientCopyProperty.restype = ctypes.c_void_p
        _iokit.IOHIDServiceClientCopyProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        _iokit.IOHIDServiceClientCopyEvent.restype = ctypes.c_void_p
        _iokit.IOHIDServiceClientCopyEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64,
        ]
        _iokit.IOHIDEventGetFloatValue.restype = ctypes.c_double
        _iokit.IOHIDEventGetFloatValue.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
        ]

        # Pre-create the matching dictionary (reused every call)
        _temp_matching = _cf.CFDictionaryCreateMutable(None, 2, None, None)
        _page_val = ctypes.c_int32(0xFF00)
        _usage_val = ctypes.c_int32(0x0005)
        _page_num = _cf.CFNumberCreate(
            None, _kCFNumberSInt32Type, ctypes.byref(_page_val),
        )
        _usage_num = _cf.CFNumberCreate(
            None, _kCFNumberSInt32Type, ctypes.byref(_usage_val),
        )
        _cf.CFDictionarySetValue(
            _temp_matching, _mk_cfstr("PrimaryUsagePage"), _page_num,
        )
        _cf.CFDictionarySetValue(
            _temp_matching, _mk_cfstr("PrimaryUsage"), _usage_num,
        )
        _temp_product_key = _mk_cfstr("Product")

        _TEMP_AVAILABLE = True
    except Exception:
        pass


# Cached IOHIDEventSystemClient (created once, reused)
_temp_client = None


def _get_temp_client():
    global _temp_client
    if _temp_client is None and _TEMP_AVAILABLE:
        _temp_client = _iokit.IOHIDEventSystemClientCreate(None)
        if _temp_client:
            _iokit.IOHIDEventSystemClientSetMatching(_temp_client, _temp_matching)
    return _temp_client


def get_temperatures() -> dict:
    """
    Read thermal sensor data via IOHIDEventSystemClient.
    Returns {"cpu_temp": float, "gpu_temp": float} or None values.
    No sudo required. Uses cached client and pre-cached CF objects.
    """
    result = {"cpu_temp": None, "gpu_temp": None}
    if not _TEMP_AVAILABLE:
        return result

    client = _get_temp_client()
    services = None
    try:
        if not client:
            return result

        services = _iokit.IOHIDEventSystemClientCopyServices(client)
        if not services:
            return result

        n = _cf.CFArrayGetCount(services)
        cpu_temps = []
        gpu_temps = []
        temp_field = _kIOHIDEventTypeTemperature << 16

        for i in range(n):
            svc = _cf.CFArrayGetValueAtIndex(services, i)
            name_ref = _iokit.IOHIDServiceClientCopyProperty(
                svc, _temp_product_key,
            )
            name = _cfstr_to_py(name_ref) if name_ref else ""
            if name_ref:
                _cf.CFRelease(name_ref)

            event = _iokit.IOHIDServiceClientCopyEvent(
                svc, _kIOHIDEventTypeTemperature, 0, 0,
            )
            if not event:
                continue

            temp = _iokit.IOHIDEventGetFloatValue(event, temp_field)
            _cf.CFRelease(event)

            if temp < 10 or temp > 120:
                continue

            name_lower = name.lower()
            if "tdie" in name_lower or "cpu" in name_lower:
                cpu_temps.append(temp)
            elif "gpu" in name_lower:
                gpu_temps.append(temp)

        if cpu_temps:
            result["cpu_temp"] = sum(cpu_temps) / len(cpu_temps)
        if gpu_temps:
            result["gpu_temp"] = sum(gpu_temps) / len(gpu_temps)
        # Apple Silicon 只有整片 SoC 的 tdie 传感器，没有独立 GPU 探头。
        # 以前这里把 CPU 温度复制给 GPU，看着像真的其实是同一个数，现在留空。

    except Exception:
        pass
    finally:
        if services:
            _cf.CFRelease(services)
        # client is cached globally, do not release

    return result


class TempReader(PollingReader):
    def __init__(self, interval: float = 3.0):
        super().__init__(get_temperatures, {"cpu_temp": None, "gpu_temp": None}, interval)


# ============================================================
# Power via IOReport (no sudo)
# ============================================================

def _load_ioreport():
    try:
        return ctypes.CDLL("libIOReport.dylib")
    except OSError:
        return None


_ioreport_lib = _load_ioreport()
_IOREPORT_AVAILABLE = _ioreport_lib is not None and _cf is not None

if _IOREPORT_AVAILABLE:
    _ioreport_lib.IOReportCopyChannelsInGroup.restype = ctypes.c_void_p
    _ioreport_lib.IOReportCopyChannelsInGroup.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64,
    ]
    _ioreport_lib.IOReportCreateSubscription.restype = ctypes.c_void_p
    _ioreport_lib.IOReportCreateSubscription.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint64, ctypes.c_void_p,
    ]
    _ioreport_lib.IOReportCreateSamples.restype = ctypes.c_void_p
    _ioreport_lib.IOReportCreateSamples.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _ioreport_lib.IOReportCreateSamplesDelta.restype = ctypes.c_void_p
    _ioreport_lib.IOReportCreateSamplesDelta.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _ioreport_lib.IOReportChannelGetGroup.restype = ctypes.c_void_p
    _ioreport_lib.IOReportChannelGetGroup.argtypes = [ctypes.c_void_p]
    _ioreport_lib.IOReportChannelGetChannelName.restype = ctypes.c_void_p
    _ioreport_lib.IOReportChannelGetChannelName.argtypes = [ctypes.c_void_p]
    _ioreport_lib.IOReportSimpleGetIntegerValue.restype = ctypes.c_int64
    _ioreport_lib.IOReportSimpleGetIntegerValue.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    _ioreport_lib.IOReportChannelGetUnitLabel.restype = ctypes.c_void_p
    _ioreport_lib.IOReportChannelGetUnitLabel.argtypes = [ctypes.c_void_p]


# macOS 27 冻结了旧的 mJ 计数器，新的能耗通道用 nJ 且改了名，两套都映射，
# 谁在动就用谁。
_POWER_CHANNELS = {
    "CPU Energy": "cpu_power",
    "GPU": "gpu_power",
    "GPU Energy": "gpu_power",
    "DRAM": "dram_power",
    "ANE": "ane_power",
}

# 单位 -> 焦耳换算系数
_ENERGY_UNIT_SCALE = {"J": 1.0, "mJ": 1e-3, "uJ": 1e-6, "nJ": 1e-9}

# SMC 功率键（经负载实测确认）：PSTR = 整机总功耗，PPMC/PPSC = CPU 集群供电轨
_SMC_TOTAL_KEY = "PSTR"
_SMC_CPU_KEYS = ("PPMC", "PPSC")

_POWER_FIELDS = ("cpu_power", "gpu_power", "dram_power", "ane_power", "total_power")

# Pre-cached CFString for IOReport parsing
_cfstr_ioreport_channels = _mk_cfstr("IOReportChannels") if _cf else None


class PowerReader:
    """Read power from IOReport Energy Model. Background thread, no sudo."""

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._lock = threading.Lock()
        self._data = {k: None for k in _POWER_FIELDS}
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._ok = False

        if not _IOREPORT_AVAILABLE:
            return
        try:
            energy_key = _mk_cfstr("Energy Model")
            ch = _ioreport_lib.IOReportCopyChannelsInGroup(
                energy_key, None, 0, 0, 0,
            )
            _cf.CFRelease(energy_key)
            if not ch:
                return
            sub_ref = ctypes.c_void_p()
            sub = _ioreport_lib.IOReportCreateSubscription(
                None, ch, ctypes.byref(sub_ref), 0, None,
            )
            if not sub:
                _cf.CFRelease(ch)
                return
            self._channels = ch
            self._subscription = sub
            self._ok = True
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._ok

    def start(self):
        if not self._ok:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()

    @property
    def latest(self) -> dict:
        with self._lock:
            return dict(self._data)

    def _loop(self):
        prev = _ioreport_lib.IOReportCreateSamples(
            self._subscription, self._channels, None,
        )
        if not prev:
            return
        prev_t = time.monotonic()
        while self._running:
            self._stop_event.wait(timeout=self._interval)
            if not self._running:
                break
            curr = _ioreport_lib.IOReportCreateSamples(
                self._subscription, self._channels, None,
            )
            if not curr:
                continue
            now = time.monotonic()
            delta = _ioreport_lib.IOReportCreateSamplesDelta(prev, curr, None)
            if delta:
                data = self._parse(delta, max(now - prev_t, 1e-3))
                with self._lock:
                    self._data = data
                _cf.CFRelease(delta)
            _cf.CFRelease(prev)
            prev = curr
            prev_t = now
        if prev:
            _cf.CFRelease(prev)

    def _parse(self, delta, elapsed: float) -> dict:
        """把能耗增量换算成瓦特。拿不到的分项返回 None（显示为 --），不返回 0。"""
        result = {k: None for k in _POWER_FIELDS}
        arr = _cf.CFDictionaryGetValue(delta, _cfstr_ioreport_channels)
        if arr:
            for i in range(_cf.CFArrayGetCount(arr)):
                ch = _cf.CFArrayGetValueAtIndex(arr, i)
                if _cfstr_to_py(_ioreport_lib.IOReportChannelGetGroup(ch)) != "Energy Model":
                    continue
                name = _cfstr_to_py(_ioreport_lib.IOReportChannelGetChannelName(ch))
                field = _POWER_CHANNELS.get(name)
                if field is None:
                    continue
                unit = (_cfstr_to_py(
                    _ioreport_lib.IOReportChannelGetUnitLabel(ch)) or "").strip()
                scale = _ENERGY_UNIT_SCALE.get(unit)
                if scale is None:
                    continue
                raw = _ioreport_lib.IOReportSimpleGetIntegerValue(ch, None)
                if raw <= 0:
                    continue  # 冻结或空的计数器，别把 0 当成真实读数
                watts = raw * scale / elapsed
                if result[field] is None or watts > result[field]:
                    result[field] = watts

        # macOS 27 上 CPU/DRAM/ANE 的 IOReport 计数器已停更，改用 SMC 供电轨
        if result["cpu_power"] is None:
            result["cpu_power"] = smc.read_sum(_SMC_CPU_KEYS)

        total = smc.read_float(_SMC_TOTAL_KEY)
        if total is None:
            parts = [result[k] for k in ("cpu_power", "gpu_power",
                                         "dram_power", "ane_power")]
            known = [v for v in parts if v is not None]
            total = sum(known) if known else None
        result["total_power"] = total
        return result


# ============================================================
# Network I/O via psutil
# ============================================================

# 这些接口的流量会和物理网卡重复：VPN 隧道 (utun) 把同一批字节再记一遍，
# 环回/网桥/AirDrop 也不该算进上网流量。
_VIRTUAL_NIC_PREFIXES = ("lo", "utun", "ipsec", "ppp", "gif", "stf",
                         "bridge", "awdl", "llw", "anpi", "ap")


_NetCounters = collections.namedtuple("_NetCounters", "bytes_recv bytes_sent")


def _physical_net_counters() -> "_NetCounters":
    """只累加物理网卡，避免 VPN 隧道把流量算两遍。"""
    recv = sent = 0
    matched = False
    for nic, c in psutil.net_io_counters(pernic=True).items():
        if nic.startswith(_VIRTUAL_NIC_PREFIXES):
            continue
        matched = True
        recv += c.bytes_recv
        sent += c.bytes_sent
    if not matched:
        total = psutil.net_io_counters()
        return _NetCounters(total.bytes_recv, total.bytes_sent)
    return _NetCounters(recv, sent)


class NetworkMonitor:
    """Track network upload/download speeds."""

    def __init__(self):
        self._prev = _physical_net_counters()
        self._prev_time = time.monotonic()
        self._last_result = {
            "download_speed": 0.0, "upload_speed": 0.0,
            "bytes_recv_total": 0, "bytes_sent_total": 0,
        }

    def get_speeds(self) -> dict:
        """Returns {"download_speed": bytes/s, "upload_speed": bytes/s, ...}"""
        now = time.monotonic()
        curr = _physical_net_counters()
        dt = now - self._prev_time
        if dt <= 0:
            dt = 1.0

        dl_speed = (curr.bytes_recv - self._prev.bytes_recv) / dt
        ul_speed = (curr.bytes_sent - self._prev.bytes_sent) / dt

        self._prev = curr
        self._prev_time = now

        self._last_result = {
            "download_speed": dl_speed,
            "upload_speed": ul_speed,
            "bytes_recv_total": curr.bytes_recv,
            "bytes_sent_total": curr.bytes_sent,
        }
        return self._last_result

    @property
    def last(self) -> dict:
        """Return last computed speeds without re-sampling."""
        return dict(self._last_result)

    @staticmethod
    def format_speed(bps: float) -> str:
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        elif bps < 1024 * 1024 * 1024:
            return f"{bps / (1024 * 1024):.2f} MB/s"
        else:
            return f"{bps / (1024 * 1024 * 1024):.2f} GB/s"

    @staticmethod
    def format_total(b: int) -> str:
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"


# ============================================================
# Battery / Charging Info
# ============================================================

def get_battery_info() -> dict:
    """Read battery state via ioreg AppleSmartBattery.
    Returns dict with battery status and charging details.
    """
    result = {
        "plugged": False, "charging": False, "percent": 0,
        "charge_watts": 0.0, "time_to_full": 0, "cycle_count": 0,
        "bat_temp": 0.0, "amperage": 0, "voltage": 0,
    }
    try:
        proc = subprocess.run(
            ["ioreg", "-rn", "AppleSmartBattery", "-a"],
            capture_output=True, timeout=2,
        )
        if proc.returncode != 0 or not proc.stdout:
            return result
        entries = plistlib.loads(proc.stdout)
        if not entries:
            return result
        e = entries[0]
        result["plugged"] = bool(e.get("ExternalConnected", False))
        result["charging"] = bool(e.get("IsCharging", False))
        result["percent"] = int(e.get("CurrentCapacity", 0))
        result["cycle_count"] = int(e.get("CycleCount", 0))
        # 电池温度（单位：℃ × 100）
        raw_temp = e.get("Temperature", 0)
        if raw_temp:
            result["bat_temp"] = raw_temp / 100.0
        amp = e.get("Amperage", 0)   # mA (positive = charging)
        volt = e.get("Voltage", 0)   # mV
        result["amperage"] = amp
        result["voltage"] = volt
        if result["charging"] and amp > 0 and volt > 0:
            result["charge_watts"] = abs(amp) * volt / 1_000_000
        # 预计充满时间（分钟），65535 表示无效
        ttf = e.get("AvgTimeToFull", 65535)
        if ttf and ttf < 65535:
            result["time_to_full"] = int(ttf)
    except Exception:
        pass
    return result


_BATTERY_DEFAULT = {
    "plugged": False, "charging": False, "percent": 0,
    "charge_watts": 0.0, "time_to_full": 0, "cycle_count": 0,
    "bat_temp": 0.0, "amperage": 0, "voltage": 0,
}


class BatteryReader(PollingReader):
    """Poll battery info every 10s in background (ioreg subprocess is slow)."""
    def __init__(self, interval: float = 10.0):
        super().__init__(get_battery_info, dict(_BATTERY_DEFAULT), interval)
