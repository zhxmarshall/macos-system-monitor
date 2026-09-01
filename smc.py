"""AppleSMC 读取 / Minimal AppleSMC client (read-only, no sudo)."""

from __future__ import annotations

import ctypes
import struct
import threading
from ctypes import c_uint8, c_uint16, c_uint32

_KERNEL_INDEX_SMC = 2
_CMD_READ_BYTES = 5
_CMD_READ_KEYINFO = 9
_TYPE_FLT = 0x666C7420  # 'flt '


class _Vers(ctypes.Structure):
    _fields_ = [("major", c_uint8), ("minor", c_uint8), ("build", c_uint8),
                ("reserved", c_uint8), ("release", c_uint16)]


class _PLimit(ctypes.Structure):
    _fields_ = [("version", c_uint16), ("length", c_uint16), ("cpuPLimit", c_uint32),
                ("gpuPLimit", c_uint32), ("memPLimit", c_uint32)]


class _KeyInfo(ctypes.Structure):
    _fields_ = [("dataSize", c_uint32), ("dataType", c_uint32), ("dataAttributes", c_uint8)]


class _KeyData(ctypes.Structure):
    """必须精确匹配 AppleSMC 的 80 字节 SMCKeyData_t 布局。"""
    _fields_ = [("key", c_uint32), ("vers", _Vers), ("pLimitData", _PLimit),
                ("keyInfo", _KeyInfo), ("result", c_uint8), ("status", c_uint8),
                ("data8", c_uint8), ("data32", c_uint32), ("bytes", c_uint8 * 32)]


def _load():
    try:
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
    except OSError:
        return None, None
    iokit.IOServiceMatching.restype = ctypes.c_void_p
    iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
    iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    iokit.IOServiceOpen.restype = ctypes.c_int
    iokit.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.POINTER(ctypes.c_uint32)]
    iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
    iokit.IOConnectCallStructMethod.restype = ctypes.c_int
    iokit.IOConnectCallStructMethod.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    libc.mach_task_self.restype = ctypes.c_uint32
    return iokit, libc


_iokit, _libc = _load()
_conn = None
_lock = threading.Lock()

if _iokit is not None:
    try:
        _svc = _iokit.IOServiceGetMatchingService(0, _iokit.IOServiceMatching(b"AppleSMC"))
        if _svc:
            _c = ctypes.c_uint32()
            if _iokit.IOServiceOpen(_svc, _libc.mach_task_self(), 0, ctypes.byref(_c)) == 0:
                _conn = _c.value
            _iokit.IOObjectRelease(_svc)
    except Exception:
        _conn = None


def available() -> bool:
    return _conn is not None


def _call(inp: _KeyData):
    out = _KeyData()
    size = ctypes.c_size_t(ctypes.sizeof(_KeyData))
    rc = _iokit.IOConnectCallStructMethod(
        _conn, _KERNEL_INDEX_SMC, ctypes.byref(inp), ctypes.sizeof(inp),
        ctypes.byref(out), ctypes.byref(size))
    return rc, out


def read_float(key: str):
    """读取一个 'flt ' 类型的 SMC 键，失败返回 None。"""
    if _conn is None or len(key) != 4:
        return None
    try:
        key_int = struct.unpack(">I", key.encode("ascii"))[0]
    except (UnicodeEncodeError, struct.error):
        return None
    try:
        with _lock:
            probe = _KeyData()
            probe.key = key_int
            probe.data8 = _CMD_READ_KEYINFO
            rc, info = _call(probe)
            if rc != 0 or info.result != 0:
                return None
            if info.keyInfo.dataType != _TYPE_FLT or info.keyInfo.dataSize != 4:
                return None

            req = _KeyData()
            req.key = key_int
            req.data8 = _CMD_READ_BYTES
            req.keyInfo.dataSize = 4
            rc, out = _call(req)
            if rc != 0 or out.result != 0:
                return None
            return struct.unpack("<f", bytes(out.bytes[:4]))[0]
    except Exception:
        return None


def read_sum(keys) -> float | None:
    """求若干键之和；全部读取失败时返回 None。"""
    total = None
    for k in keys:
        v = read_float(k)
        if v is not None:
            total = v if total is None else total + v
    return total
