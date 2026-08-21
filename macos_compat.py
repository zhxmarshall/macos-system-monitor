"""macOS 版本兼容补丁 / macOS version compatibility shims."""

from __future__ import annotations

import ctypes

# -[NSEvent clickCount] 只对鼠标按下/抬起/拖拽事件有效，其余类型 AppKit 会抛
# NSInternalInconsistencyException。
_MOUSE_EVENT_TYPES = frozenset({1, 2, 3, 4, 6, 7, 25, 26, 27})

# 保存 ctypes 跳板的引用，避免被 GC 回收后 AppKit 调到野指针
_guard_imp = None


def install_nsevent_clickcount_guard() -> bool:
    """让 -[NSEvent clickCount] 对非鼠标事件返回 0，而不是抛异常。

    Qt 的 QCocoaSystemTrayIcon::emitActivated() 会无条件读取
    NSApp.currentEvent.clickCount。macOS 27 的菜单栏菜单由 NSSceneStatusItem 经
    FrontBoardServices 的场景回调弹出，此时 currentEvent 不是鼠标事件，
    clickCount 抛出的 ObjC 异常没人接，直接 abort 整个进程（Qt 6.11.1 仍未修复）。

    返回 0 后 Qt 走它自己的兜底分支 cocoaButton2QtButton()，该分支只读 type 和
    buttonNumber——两者对非鼠标事件都安全——最终得到 Trigger，正是打开菜单应有的结果。
    """
    global _guard_imp
    if _guard_imp is not None:
        return True

    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    except OSError:
        return False

    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.class_getInstanceMethod.restype = ctypes.c_void_p
    objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.method_getImplementation.restype = ctypes.c_void_p
    objc.method_getImplementation.argtypes = [ctypes.c_void_p]
    objc.method_setImplementation.restype = ctypes.c_void_p
    objc.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    cls = objc.objc_getClass(b"NSEvent")
    sel_click_count = objc.sel_registerName(b"clickCount")
    sel_type = objc.sel_registerName(b"type")
    if not cls or not sel_click_count or not sel_type:
        return False

    method = objc.class_getInstanceMethod(cls, sel_click_count)
    if not method:
        return False

    imp_t = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
    send = imp_t(ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    original = imp_t(objc.method_getImplementation(method))

    def click_count(receiver, cmd):
        if send(receiver, sel_type) in _MOUSE_EVENT_TYPES:
            return original(receiver, cmd)
        return 0

    _guard_imp = imp_t(click_count)
    objc.method_setImplementation(method, ctypes.cast(_guard_imp, ctypes.c_void_p))
    return True
