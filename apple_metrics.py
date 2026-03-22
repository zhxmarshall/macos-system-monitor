"""
Apple Silicon metrics reader - no sudo needed.

GPU usage:    ioreg AGXAccelerator PerformanceStatistics
Power data:   IOReport "Energy Model" via libIOReport.dylib
Temperature:  IOHIDEventSystemClient thermal sensors
Network:      psutil net_io_counters
"""

import ctypes
import ctypes.util
import plistlib
import subprocess
import threading
import time

import psutil


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


class GPUReader:
    """Read GPU usage in background thread to avoid blocking Qt main thread."""

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._lock = threading.Lock()
        self._data = {}
        self._running = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self):
        self._running = False

    @property
    def latest(self) -> dict:
        with self._lock:
            return dict(self._data)

    def _loop(self):
        while self._running:
            data = _poll_gpu_usage()
            with self._lock:
                self._data = data
            time.sleep(self._interval)


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
        elif cpu_temps:
            result["gpu_temp"] = result["cpu_temp"]

    except Exception:
        pass
    finally:
        if services:
            _cf.CFRelease(services)
        # client is cached globally, do not release

    return result


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


_POWER_CHANNELS = {
    "CPU Energy": "cpu_power",
    "GPU": "gpu_power",
    "DRAM": "dram_power",
    "ANE": "ane_power",
}

# Pre-cached CFString for IOReport parsing
_cfstr_ioreport_channels = _mk_cfstr("IOReportChannels") if _cf else None


class PowerReader:
    """Read power from IOReport Energy Model. Background thread, no sudo."""

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._lock = threading.Lock()
        self._data = {}
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
        while self._running:
            self._stop_event.wait(timeout=self._interval)
            if not self._running:
                break
            curr = _ioreport_lib.IOReportCreateSamples(
                self._subscription, self._channels, None,
            )
            if not curr:
                continue
            delta = _ioreport_lib.IOReportCreateSamplesDelta(prev, curr, None)
            if delta:
                with self._lock:
                    self._data = self._parse(delta)
                _cf.CFRelease(delta)
            _cf.CFRelease(prev)
            prev = curr
        if prev:
            _cf.CFRelease(prev)

    def _parse(self, delta) -> dict:
        result = {
            "cpu_power": 0.0, "gpu_power": 0.0,
            "dram_power": 0.0, "ane_power": 0.0, "total_power": 0.0,
        }
        arr = _cf.CFDictionaryGetValue(delta, _cfstr_ioreport_channels)
        if not arr:
            return result
        n = _cf.CFArrayGetCount(arr)
        for i in range(n):
            ch = _cf.CFArrayGetValueAtIndex(arr, i)
            group = _cfstr_to_py(_ioreport_lib.IOReportChannelGetGroup(ch))
            name = _cfstr_to_py(_ioreport_lib.IOReportChannelGetChannelName(ch))
            if group != "Energy Model" or name not in _POWER_CHANNELS:
                continue
            raw = _ioreport_lib.IOReportSimpleGetIntegerValue(ch, None)
            result[_POWER_CHANNELS[name]] = raw / 1000.0
        result["total_power"] = sum(
            result[k] for k in ("cpu_power", "gpu_power", "dram_power", "ane_power")
        )
        return result


# ============================================================
# Network I/O via psutil
# ============================================================

class NetworkMonitor:
    """Track network upload/download speeds."""

    def __init__(self):
        self._prev = psutil.net_io_counters()
        self._prev_time = time.monotonic()
        self._last_result = {
            "download_speed": 0.0, "upload_speed": 0.0,
            "bytes_recv_total": 0, "bytes_sent_total": 0,
        }

    def get_speeds(self) -> dict:
        """Returns {"download_speed": bytes/s, "upload_speed": bytes/s, ...}"""
        now = time.monotonic()
        curr = psutil.net_io_counters()
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
