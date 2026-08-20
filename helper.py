import ctypes
import os
import sys
from pathlib import Path

import win32con
import win32gui


def disable_high_dpi():
    os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '0'
    os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '0'


def set_dpi_awareness():
    # Set Windows DPI Awareness Context to Per-Monitor V2
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# noinspection protected-member
def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path.cwd()

    resource_path = base_dir / relative_path
    return resource_path.resolve()


def get_exe_path():
    if getattr(sys, 'frozen', False):
        return os.fspath(Path(sys.executable).resolve())
    return str(Path(__file__).resolve())


def get_executable_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent

    resource_path = base_dir / relative_path
    return resource_path.resolve()


def get_window_info(window_name: str):
    hwnd = win32gui.FindWindow(None, window_name)
    if hwnd:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top
        if width > 0 and height > 0:
            return hwnd, left, top, width, height
    return None, 0, 0, 0, 0


def is_topmost_window(hwnd):
    if not hwnd:
        return False

    try:
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    except win32gui.error:
        return False

    return bool(ex_style & win32con.WS_EX_TOPMOST)
