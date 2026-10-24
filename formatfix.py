"""FormatFix – läuft unauffällig unten rechts in der Taskleiste.

Bedienung: Datei im Explorer markieren, Strg+C, dann Strg+Alt+K.

Sicherheit:
- Kein Netzwerk: FormatFix öffnet keine Ports und sendet nichts ins Internet.
- Kein Tastatur-Mitlesen: Der Hotkey wird bei Windows angemeldet (RegisterHotKey).
  FormatFix bekommt nur Strg+Alt+K gemeldet, keine anderen Tasten.
- Autostart ohne Admin-Rechte, nur für den eigenen Windows-Benutzer.
"""
import getpass
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from gui import FormatFix

APP_NAME = "FormatFix"
HOTKEY_TEXT = "Strg+Alt+K"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def resource(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


# ---------- Globaler Hotkey über Windows (kein Keyboard-Hook) ----------
class HotkeyThread(threading.Thread):
    def __init__(self, on_press, on_fail):
        super().__init__(daemon=True)
        self.on_press, self.on_fail = on_press, on_fail
        self.thread_id = None

    def run(self):
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
        VK_K, WM_HOTKEY = 0x4B, 0x0312
        self.thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_K):
            self.on_fail()
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.on_press()
        user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT


# ---------- Autostart (nur aktueller Benutzer, keine Admin-Rechte) ----------
def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return value == autostart_command()
    except (OSError, ImportError):
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


# ---------- Hauptprogramm ----------
class Tray(QObject):
    hotkey_pressed = Signal()
    hotkey_failed = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.windows = []
        icon = QIcon(str(resource("icon.ico")))
        app.setWindowIcon(icon)

        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(f"{APP_NAME} – Datei kopieren, dann {HOTKEY_TEXT}")
        menu = QMenu()
        act_help = QAction("So funktioniert's", menu)
        act_help.triggered.connect(self.show_help)
        self.act_auto = QAction("Mit Windows starten", menu, checkable=True)
        self.act_auto.setChecked(autostart_enabled())
        self.act_auto.toggled.connect(self.toggle_autostart)
        act_quit = QAction("Beenden", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_help)
        menu.addAction(self.act_auto)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_click)
        self.tray.show()

        self.hotkey_pressed.connect(self.open_from_clipboard)
        self.hotkey_failed.connect(self.on_hotkey_failed)
        self.hotkey = HotkeyThread(self.hotkey_pressed.emit, self.hotkey_failed.emit)
        self.hotkey.start()

        self.tray.showMessage(APP_NAME, f"Läuft im Hintergrund. Datei kopieren (Strg+C), "
                                        f"dann {HOTKEY_TEXT} drücken.",
                              QSystemTrayIcon.MessageIcon.Information, 5000)

    def clipboard_file(self):
        mime = self.app.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.is_file():
                    return p
        return None

    def open_from_clipboard(self):
        path = self.clipboard_file()
        if path is None:
            self.tray.showMessage(APP_NAME, "Keine Datei kopiert. Datei im Explorer markieren, "
                                            f"Strg+C drücken, dann {HOTKEY_TEXT}.",
                                  QSystemTrayIcon.MessageIcon.Warning, 4000)
            return
        self.open_window(path)

    def open_window(self, path):
        win = FormatFix(path)
        self.windows = [w for w in self.windows if w.isVisible()]
        self.windows.append(win)
        win.show()
        win.raise_()
        win.activateWindow()

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_from_clipboard()

    def show_help(self):
        self.tray.showMessage(APP_NAME,
                              f"1. Datei im Explorer anklicken\n2. Strg+C drücken\n"
                              f"3. {HOTKEY_TEXT} drücken\n4. Gewünschtes Format anklicken",
                              QSystemTrayIcon.MessageIcon.Information, 8000)

    def toggle_autostart(self, on):
        try:
            set_autostart(on)
        except (OSError, ImportError) as e:
            self.tray.showMessage(APP_NAME, f"Autostart konnte nicht geändert werden: {e}",
                                  QSystemTrayIcon.MessageIcon.Warning, 4000)

    def on_hotkey_failed(self):
        self.tray.showMessage(APP_NAME, f"{HOTKEY_TEXT} ist schon von einem anderen Programm "
                                        "belegt. Klick stattdessen auf das Wolken-Symbol.",
                              QSystemTrayIcon.MessageIcon.Warning, 8000)

    def quit(self):
        self.hotkey.stop()
        self.tray.hide()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    # Direkt mit Datei gestartet (z. B. "Öffnen mit") → nur das Fenster zeigen
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if files:
        app.setQuitOnLastWindowClosed(True)
        win = FormatFix(files[0])
        win.show()
        sys.exit(app.exec())

    # Nur einmal gleichzeitig laufen lassen
    server_name = f"{APP_NAME}-{getpass.getuser()}"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(300):
        probe.write(b"hello")
        probe.flush()
        probe.waitForBytesWritten(300)
        sys.exit(0)
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # nur eigener Benutzer
    server.listen(server_name)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.exit(1)

    tray = Tray(app)
    server.newConnection.connect(lambda: (server.nextPendingConnection(),
                                          tray.tray.showMessage(APP_NAME, "FormatFix läuft bereits.",
                                                                QSystemTrayIcon.MessageIcon.Information,
                                                                3000)))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
