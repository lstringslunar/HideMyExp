import configparser
import os
import subprocess
import sys

from helper import get_executable_path, get_window_info, is_topmost_window, get_resource_path, get_exe_path, \
    disable_high_dpi, set_dpi_awareness

disable_high_dpi()

from PySide6.QtWidgets import QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu
from PySide6.QtGui import QPixmap, QIcon, QAction, QPainter, QColor
from PySide6.QtCore import Qt, QTimer

import win32gui
import win32con

set_dpi_awareness()

ICON_PATH = get_resource_path('icon.ico')
IMAGE_PATH = get_executable_path('image.png')
SETTINGS_PATH = get_executable_path('settings.ini')
TASK_NAME = "HideMyExpAutoStart"
ARROW_PATH = get_resource_path('resources/arrow3.png')


class Overlay(QWidget):
    def __init__(self, target_name):
        super().__init__()
        self.target_name = target_name
        self.target_hwnd = None

        self.is_fullscreen_borderless = False
        self.saved_window_info = None

        if not os.path.exists(IMAGE_PATH):
            print(f"Error: '{IMAGE_PATH}' not found!")
            sys.exit(1)

        self.original_pixmap = QPixmap(IMAGE_PATH)

        self.x = 0.0
        self.y = 0.0

        self.height_px = None

        self.last_target_rect = None
        self.last_target_dim = None
        self.drag_pos = None

        self.locked = False

        self.calibration_enabled = False
        self.calibration_valid = False
        self.calibration_state_ok = True
        self.base_blue = None

        self.current_pixmap = None
        self.last_darken_ratio = 1.0

        self.config = configparser.ConfigParser()
        if os.path.exists(SETTINGS_PATH):
            try:
                self.config.read(SETTINGS_PATH)
            except Exception:
                pass

        if self.config.has_section('General'):
            self.locked = self.config.getboolean('General', 'locked', fallback=False)
            self.calibration_enabled = self.config.getboolean('General', 'calibration_enabled', fallback=False)

        self.calibration_overlay = CalibrationOverlay(self)

        self.init_ui()
        self.init_tray()

        if self.calibration_enabled and not self.locked:
            self.calibration_overlay.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_tracking)
        self.timer.start(1)

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.label = QLabel(self)
        self.label.move(0, 0)

    def init_tray(self):
        icon_pixmap = QPixmap(ICON_PATH)
        icon = QIcon(icon_pixmap) if not icon_pixmap.isNull() else QIcon()

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip(f"目標視窗 - {self.target_name}")

        tray_menu = QMenu()

        # Lock Action
        self.lock_action = QAction('鎖定', self)
        self.lock_action.setCheckable(True)
        self.lock_action.setChecked(self.locked)
        font = self.lock_action.font()
        font.setPointSize(12)
        font.setBold(True)
        self.lock_action.setFont(font)
        self.lock_action.triggered.connect(self.toggle_lock)
        tray_menu.addAction(self.lock_action)

        # Borderless Fullscreen Action
        self.fullscreen_action = QAction('全螢幕無邊框', self)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setChecked(False)
        font_fs = self.fullscreen_action.font()
        font_fs.setPointSize(12)
        font_fs.setBold(True)
        self.fullscreen_action.setFont(font_fs)
        self.fullscreen_action.triggered.connect(self.toggle_borderless_fullscreen)
        tray_menu.addAction(self.fullscreen_action)

        # Auto Start Action
        self.autostart_action = QAction('開機自啟', self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self.check_autostart())
        font_auto = self.autostart_action.font()
        font_auto.setPointSize(12)
        font_auto.setBold(True)
        self.autostart_action.setFont(font_auto)
        self.autostart_action.triggered.connect(self.toggle_autostart)
        tray_menu.addAction(self.autostart_action)

        # Calibration Action
        self.calibration_action = QAction('使用橘點顏色校準', self)
        self.calibration_action.setCheckable(True)
        self.calibration_action.setChecked(self.calibration_enabled)
        font_calib = self.calibration_action.font()
        font_calib.setPointSize(12)
        font_calib.setBold(True)
        self.calibration_action.setFont(font_calib)
        self.calibration_action.triggered.connect(self.toggle_calibration)
        tray_menu.addAction(self.calibration_action)

        # Exit Action
        exit_action = QAction('關閉', self)
        font_exit = exit_action.font()
        font_exit.setPointSize(12)
        font_exit.setBold(True)
        exit_action.setFont(font_exit)
        exit_action.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def toggle_borderless_fullscreen(self, checked):
        hwnd, _, _, _, _ = get_window_info(self.target_name)
        if not hwnd:
            self.fullscreen_action.setChecked(self.is_fullscreen_borderless)
            return

        self.is_fullscreen_borderless = checked

        if checked:
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            rect = win32gui.GetWindowRect(hwnd)
            self.saved_window_info = {
                'style': style,
                'ex_style': ex_style,
                'rect': rect
            }

            new_style = style & ~win32con.WS_OVERLAPPEDWINDOW & ~win32con.WS_POPUP
            new_style |= win32con.WS_POPUP
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, new_style)

            from win32api import MonitorFromWindow, GetMonitorInfo
            monitor = MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
            monitor_info = GetMonitorInfo(monitor)
            monitor_rect = monitor_info['Monitor']

            x, y, w, h = (monitor_rect[0],
                          monitor_rect[1],
                          monitor_rect[2] - monitor_rect[0],
                          monitor_rect[3] - monitor_rect[1])

            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                x, y, w, h,
                win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW
            )
        else:
            if self.saved_window_info:
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, self.saved_window_info['style'])
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, self.saved_window_info['ex_style'])

                l, t, r, b = self.saved_window_info['rect']
                w = r - l
                h = b - t

                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_NOTOPMOST,
                    l, t, w, h,
                    win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW
                )
                self.saved_window_info = None
            else:
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style | win32con.WS_OVERLAPPEDWINDOW)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_NOTOPMOST,
                    100, 100, 800, 600,
                    win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW
                )

    @staticmethod
    def check_autostart():
        result = subprocess.run(
            ['schtasks', '/query', '/tn', TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.returncode == 0

    @staticmethod
    def toggle_autostart(checked):
        try:
            if checked:
                exe_path = get_exe_path()

                result = subprocess.run(
                    [
                        'schtasks',
                        '/create',
                        '/tn', TASK_NAME,
                        '/tr', f'"{exe_path}"',
                        '/sc', 'onlogon',
                        '/rl', 'highest',
                        '/f'
                    ],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )

                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip())

            else:
                subprocess.run(
                    ['schtasks', '/delete', '/tn', TASK_NAME, '/f'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )

        except Exception as e:
            print(f"Failed to toggle autostart: {e}")

    def toggle_lock(self, checked):
        self.locked = checked
        self.drag_pos = None
        self.save_general_settings()

        if self.calibration_enabled:
            if checked:
                self.calibration_overlay.hide()
                self.examine_calibration_point()
            else:
                self.calibration_overlay.show()

    def toggle_calibration(self, checked):
        self.calibration_enabled = checked
        self.save_general_settings()

        if checked:
            if not self.locked:
                self.calibration_overlay.show()
        else:
            self.calibration_overlay.hide()
            self.calibration_valid = False
            # self.base_gray = None
            self.base_blue = None
            self.calibration_state_ok = True
            self.apply_darkening(1.0)

    def examine_calibration_point(self):
        point = self.calibration_overlay.pos()
        color = self.get_pixel_color(point.x(), point.y())

        if color is None or not self.is_orange(color):
            self.calibration_valid = False
            # self.base_gray = None
            self.base_blue = None
            self.calibration_state_ok = True
            self.apply_darkening(1.0)
            return

        # self.base_gray = sum(color) / 3
        self.base_blue = color[2]
        self.calibration_valid = True
        self.calibration_state_ok = True
        self.save_calibration_settings()

    def load_calibration_settings(self, tw, th):
        key = self.calibration_overlay.get_dimension_key(tw, th)
        if self.config.has_section(key) and self.config.has_option(key, 'base_blue'):
            self.base_blue = self.config.getfloat(key, 'base_blue')
            self.calibration_valid = True
        else:
            self.base_blue = None
            self.calibration_valid = False

    def save_calibration_settings(self):
        if self.last_target_dim is None:
            return

        tw, th = self.last_target_dim
        key = self.calibration_overlay.get_dimension_key(tw, th)
        if not self.config.has_section(key):
            self.config.add_section(key)
        self.config.set(key, 'base_blue', str(self.base_blue))
        self.write_config()

    def apply_darkening(self, ratio):
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self.last_darken_ratio) < 1e-3:
            return
        self.last_darken_ratio = ratio

        if self.current_pixmap is None or self.current_pixmap.isNull():
            return

        if ratio >= 1.0:
            self.label.setPixmap(self.current_pixmap)
            return

        darkened = QPixmap(self.current_pixmap)
        painter = QPainter(darkened)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        alpha = max(0, min(255, int(round((1.0 - ratio) * 255))))
        painter.fillRect(darkened.rect(), QColor(0, 0, 0, alpha))
        painter.end()

        self.label.setPixmap(darkened)

    @staticmethod
    def get_pixel_color(x, y):
        hdc = win32gui.GetDC(0)
        try:
            colorref = win32gui.GetPixel(hdc, x, y)
        finally:
            win32gui.ReleaseDC(0, hdc)

        if colorref == -1:
            return None

        r = colorref & 0xff
        g = (colorref >> 8) & 0xff
        b = (colorref >> 16) & 0xff
        return r, g, b

    @staticmethod
    def is_orange(color):
        r, g, b = color
        return r > g > b

    @staticmethod
    def get_dimension_key(tw, th):
        return f"{tw}x{th}"

    def load_dimension_settings(self, tw, th):
        key = self.get_dimension_key(tw, th)
        if self.config.has_section(key):
            self.x = self.config.getfloat(key, 'x', fallback=0.0)
            self.y = self.config.getfloat(key, 'y', fallback=0.0)
            self.height_px = self.config.getint(key, 'height_px', fallback=256)
        else:
            self.x = 0.0
            self.y = 0.0
            self.height_px = 256
            self.save_dimension_settings(tw, th)

    def save_dimension_settings(self, tw, th):
        key = self.get_dimension_key(tw, th)
        if not self.config.has_section(key):
            self.config.add_section(key)
        self.config.set(key, 'x', str(self.x))
        self.config.set(key, 'y', str(self.y))
        self.config.set(key, 'height_px', str(self.height_px))
        self.write_config()

    def save_general_settings(self):
        if not self.config.has_section('General'):
            self.config.add_section('General')
        self.config.set('General', 'locked', str(self.locked))
        self.config.set('General', 'calibration_enabled', str(self.calibration_enabled))
        self.write_config()

    def write_config(self):
        try:
            with open(SETTINGS_PATH, 'w') as f:
                self.config.write(f)
        except Exception as e:
            print(f"Warning: failed to save settings: {e}")

    def update_tracking(self):
        hwnd, tx, ty, tw, th = get_window_info(self.target_name)
        self.target_hwnd = hwnd

        # Target window missing or minimized -> hide overlay
        if not hwnd or win32gui.IsIconic(hwnd):
            if not self.isHidden():
                self.hide()
            if self.calibration_enabled and not self.calibration_overlay.isHidden():
                self.calibration_overlay.hide()
            return

        current_rect = (tx, ty, tw, th)
        current_dim = (tw, th)

        if current_dim != self.last_target_dim:
            self.last_target_dim = current_dim
            self.load_dimension_settings(tw, th)
            self.last_target_rect = current_rect
            self.update_transform()
        elif current_rect != self.last_target_rect:
            self.last_target_rect = current_rect
            self.update_transform()

        if self.calibration_enabled:
            if current_dim != self.calibration_overlay.last_target_dim:
                self.calibration_overlay.last_target_dim = current_dim
                self.calibration_overlay.load_dimension_settings(tw, th)
                self.load_calibration_settings(tw, th)
            self.calibration_overlay.last_target_rect = current_rect
            self.calibration_overlay.update_transform()

            if self.locked:
                if not self.calibration_overlay.isHidden():
                    self.calibration_overlay.hide()
            else:
                if self.calibration_overlay.isHidden():
                    self.calibration_overlay.show()

        if self.calibration_enabled and self.calibration_valid and self.locked:
            point = self.calibration_overlay.pos()
            color = self.get_pixel_color(point.x(), point.y())

            if color is not None and self.is_orange(color):
                blue = color[2]
                ratio = 0.0 if self.base_blue <= 0 else max(0.0, min(1.0, blue / self.base_blue))

                self.apply_darkening(ratio)
                self.calibration_state_ok = True
            else:
                self.calibration_state_ok = False
        else:
            self.calibration_state_ok = True
            self.apply_darkening(1.0)

        if self.calibration_enabled and self.calibration_valid and self.locked and not self.calibration_state_ok:
            if not self.isHidden():
                self.hide()
        else:
            if self.isHidden():
                self.show()

        self.sync_z_order()

    def sync_z_order(self):
        overlay_hwnd = int(self.winId())
        target_hwnd = self.target_hwnd

        if not overlay_hwnd or not target_hwnd:
            return

        # Check if overlay is already directly above target_hwnd
        try:
            next_hwnd = win32gui.GetWindow(overlay_hwnd, win32con.GW_HWNDNEXT)
            if next_hwnd == target_hwnd:
                return
        except win32gui.error:
            pass

        target_is_topmost = is_topmost_window(target_hwnd)

        # Find the window immediately preceding target_hwnd in Z-order
        try:
            prev_hwnd = win32gui.GetWindow(target_hwnd, win32con.GW_HWNDPREV)
            while prev_hwnd and prev_hwnd == overlay_hwnd:
                prev_hwnd = win32gui.GetWindow(prev_hwnd, win32con.GW_HWNDPREV)
        except win32gui.error:
            prev_hwnd = None

        flags = win32con.SWP_NOSIZE | win32con.SWP_NOMOVE | win32con.SWP_NOACTIVATE

        if prev_hwnd:
            prev_is_topmost = is_topmost_window(prev_hwnd)
            # If target is normal but prev is topmost, place overlay at top of normal stack
            if not target_is_topmost and prev_is_topmost:
                win32gui.SetWindowPos(overlay_hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            else:
                win32gui.SetWindowPos(overlay_hwnd, prev_hwnd, 0, 0, 0, 0, flags)
        else:
            # Target is at the top of its Z-order layer
            insert_after = win32con.HWND_TOPMOST if target_is_topmost else win32con.HWND_TOP
            win32gui.SetWindowPos(overlay_hwnd, insert_after, 0, 0, 0, 0, flags)

    def update_transform(self):
        if not self.last_target_rect or self.height_px is None:
            return

        tx, ty, tw, th = self.last_target_rect
        target_bottom = ty + th

        img_w = self.original_pixmap.width()
        img_h = self.original_pixmap.height()

        new_h = max(1, int(self.height_px))
        if img_h <= 0:
            new_w = new_h
        else:
            new_w = max(1, int(round(new_h * (img_w / img_h))))

        new_x = int(tx + self.x * tw)
        new_bottom = int(target_bottom - self.y * tw)
        new_y = int(new_bottom - new_h)

        if new_w != self.width() or new_h != self.height():
            scaled_pixmap = self.original_pixmap.scaled(
                new_w, new_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.resize(new_w, new_h)
            self.label.resize(new_w, new_h)
            self.label.setPixmap(scaled_pixmap)

            self.setMask(scaled_pixmap.mask())

            self.current_pixmap = scaled_pixmap
            self.last_darken_ratio = -1.0

        self.move(new_x, new_y)

    def mousePressEvent(self, event):
        if self.locked:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.locked:
            return
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_pos is not None:
            new_global_pos = event.globalPosition().toPoint() - self.drag_pos
            self.move(new_global_pos)

            if self.last_target_rect:
                tx, ty, tw, th = self.last_target_rect
                target_bottom = ty + th
                overlay_bottom = new_global_pos.y() + self.height()

                self.x = (new_global_pos.x() - tx) / tw
                self.y = (target_bottom - overlay_bottom) / tw

                # Overriding save: position changed for the *current* dimension.
                if self.last_target_dim:
                    self.save_dimension_settings(*self.last_target_dim)

            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None

    def wheelEvent(self, event):
        if self.locked:
            return

        angle = event.angleDelta().y()
        if angle == 0:
            return

        # Ctrl + Scroll : 1px | Scroll : 10px
        step = 1 if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) else 10

        if self.height_px is None:
            self.height_px = 256

        if angle > 0:
            self.height_px += step
        else:
            self.height_px -= step

        self.height_px = max(64, min(self.height_px, 8192))

        self.update_transform()

        if self.last_target_dim:
            self.save_dimension_settings(*self.last_target_dim)

        event.accept()


class CalibrationOverlay(QWidget):
    def __init__(self, parent_overlay):
        super().__init__()
        self.parent_overlay = parent_overlay

        self.x = 0.0
        self.y = 0.0

        self.last_target_rect = None
        self.last_target_dim = None
        self.drag_pos = None

        if not os.path.exists(ARROW_PATH):
            print(f"Error: '{ARROW_PATH}' not found!")
            self.original_pixmap = QPixmap()
        else:
            self.original_pixmap = QPixmap(ARROW_PATH)

        self.init_ui()

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.label = QLabel(self)
        self.label.move(0, 0)
        self.label.setPixmap(self.original_pixmap)
        self.resize(self.original_pixmap.size())
        self.label.resize(self.original_pixmap.size())

    @staticmethod
    def get_dimension_key(tw, th):
        return f"{tw}x{th}_calib"

    def load_dimension_settings(self, tw, th):
        key = self.get_dimension_key(tw, th)
        config = self.parent_overlay.config
        if config.has_section(key):
            self.x = config.getfloat(key, 'x', fallback=0.0)
            self.y = config.getfloat(key, 'y', fallback=0.0)
        else:
            self.x = 0.0
            self.y = 0.0
            self.save_dimension_settings(tw, th)

    def save_dimension_settings(self, tw, th):
        key = self.get_dimension_key(tw, th)
        config = self.parent_overlay.config
        if not config.has_section(key):
            config.add_section(key)
        config.set(key, 'x', str(self.x))
        config.set(key, 'y', str(self.y))
        self.parent_overlay.write_config()

    def update_transform(self):
        if not self.last_target_rect:
            return

        tx, ty, tw, th = self.last_target_rect

        new_x = int(tx + self.x * tw)
        new_y = int(ty + self.y * th)

        self.move(new_x, new_y)

    def mousePressEvent(self, event):
        if self.parent_overlay.locked:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.parent_overlay.locked:
            return
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_pos is not None:
            new_global_pos = event.globalPosition().toPoint() - self.drag_pos
            self.move(new_global_pos)

            if self.last_target_rect:
                tx, ty, tw, th = self.last_target_rect

                self.x = (new_global_pos.x() - tx) / tw
                self.y = (new_global_pos.y() - ty) / th

                # A new target pixel invalidates any previously examined calibration.
                self.parent_overlay.calibration_valid = False
                # self.parent_overlay.base_gray = None
                self.parent_overlay.base_blue = None

                if self.last_target_dim:
                    self.save_dimension_settings(*self.last_target_dim)

            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    WINDOW_NAME = '新楓之谷：經典版'

    overlay = Overlay(WINDOW_NAME)

    sys.exit(app.exec())
