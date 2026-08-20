import configparser
import os
import subprocess
import sys
import winreg

from helper import get_executable_path, get_window_info, is_topmost_window, get_resource_path, get_exe_path, \
    disable_high_dpi, set_dpi_awareness

disable_high_dpi()

from PySide6.QtWidgets import QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu
from PySide6.QtGui import QPixmap, QIcon, QAction
from PySide6.QtCore import Qt, QTimer

import win32gui
import win32con

set_dpi_awareness()

ICON_PATH = get_resource_path('icon.ico')
IMAGE_PATH = get_executable_path('image.png')
SETTINGS_PATH = get_executable_path('settings.ini')
TASK_NAME = "HideMyExpAutoStart"


class Overlay(QWidget):
    def __init__(self, target_name):
        super().__init__()
        self.target_name = target_name
        self.target_hwnd = None

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

        self.config = configparser.ConfigParser()
        if os.path.exists(SETTINGS_PATH):
            try:
                self.config.read(SETTINGS_PATH)
            except Exception:
                pass

        if self.config.has_section('General'):
            self.locked = self.config.getboolean('General', 'locked', fallback=False)

        self.init_ui()
        self.init_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_tracking)
        self.timer.start(1)

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
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


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    WINDOW_NAME = '新楓之谷：經典版'

    overlay = Overlay(WINDOW_NAME)

    sys.exit(app.exec())
