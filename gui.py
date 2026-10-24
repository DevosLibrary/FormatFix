import sys
import math
import time
import colorsys
import queue
import threading
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

try:
    import PySide6  # noqa: F401
except ImportError:
    sys.stderr.write("PySide6 fehlt. Im PyCharm-Terminal ausführen:  pip install pyside6\n")
    sys.exit(1)

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget

from konverter import convert, targets_for

# ---------- Aussehen (hier kannst du selbst drehen) ----------
BG = QColor(9, 12, 20)              # Fensterhintergrund
FILL = QColor(20, 26, 40)           # Wolkenfläche
FILL_HOVER = QColor(34, 44, 66)     # Wolkenfläche beim Drüberfahren
TEXT = QColor(242, 244, 248)
SUBTEXT = QColor(125, 135, 153)

W, H = 700, 470                     # Fenstergröße
CX, CY = W / 2, H / 2 - 12          # Mitte
ORBIT_X, ORBIT_Y = 232, 150         # Abstand der Format-Wolken zur Mitte
BW, BH = 112, 70                    # Größe einer Format-Wolke
CW, CH = 250, 160                   # Größe der Mittel-Wolke
FLOAT = 6                           # wie weit die Wolken schweben (Pixel)
FLOAT_SPEED = 0.9                   # wie schnell sie schweben

FPS = 60                            # Bildrate
HUE_SPEED = 0.16                    # Tempo des Regenbogens (Umläufe pro Sekunde)
PULSE_SPEED = 2.6                   # Tempo der pulsierenden Aura
PULSE_MIN, PULSE_MAX = 0.35, 1.0    # schwächste / stärkste Aura
AURA_SIZE = 16                      # wie weit die Aura nach außen strahlt (Pixel)
AURA_POWER = 0.9                    # Helligkeit der Aura (0..1)
GLOW_HUES = 36                      # vorberechnete Farbstufen der Aura

BLUR = 4                            # Unschärfe der Vorschau (höher = weniger erkennbar)
PIXEL = 7                           # Verpixelung der Vorschau (höher = gröber)
LABELS = {"mid": "MIDI"}            # Anzeigenamen, wo die Endung unschön ist
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".ico"}


def rainbow(h, alpha=255):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 0.75, 1.0)
    return QColor(int(r * 255), int(g * 255), int(b * 255), int(max(0, min(255, alpha))))


def cloud_path(w, h):
    """Wolkensilhouette um den Nullpunkt: flacher Boden, drei Kuppeln."""
    x0, y0 = -w / 2, -h / 2
    p = QPainterPath()
    p.addRoundedRect(QRectF(x0, y0 + h * 0.45, w, h * 0.55), h * 0.27, h * 0.27)
    for fx, fy, fr in [(0.28, 0.58, 0.30), (0.53, 0.42, 0.42), (0.77, 0.62, 0.27)]:
        r = fr * h
        c = QPainterPath()
        c.addEllipse(QPointF(x0 + fx * w, y0 + fy * h), r, r)
        p = p.united(c)
    return p.simplified()


def make_preview(path):
    """Datei auf die Wolke zuschneiden, verpixeln, weichzeichnen. None bei Dokumenten."""
    ext = path.suffix.lower()
    try:
        if ext in IMG_EXT:
            im = Image.open(path)
            im.seek(0)
            im = im.convert("RGB")
        elif ext == ".pdf":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(path))
            page = pdf[0]
            im = page.render(scale=0.6).to_pil().convert("RGB")
            page.close()
            pdf.close()
        else:
            return None
    except Exception:
        return None
    im = ImageOps.fit(im, (CW, CH), Image.Resampling.LANCZOS)
    im = im.resize((max(1, CW // PIXEL), max(1, CH // PIXEL)), Image.Resampling.BILINEAR)
    im = im.resize((CW, CH), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(BLUR))
    shade = Image.linear_gradient("L").resize((CW, CH)).point(lambda p: int(p * 0.55))
    im = Image.composite(Image.new("RGB", (CW, CH)), im, shade)
    data = im.tobytes("raw", "RGB")
    qimg = QImage(data, CW, CH, CW * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class FormatFix(QWidget):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        self.targets = targets_for(self.path.suffix.lower())
        self.start = time.perf_counter()
        self.t = 0.0
        self.mouse = QPointF(-1000, -1000)
        self.jobs = queue.Queue()          # Warteschlange: Umwandlungen laufen nacheinander
        self.messages = queue.Queue()      # Rückmeldungen vom Hintergrund an die Oberfläche
        self.show_fps = False
        self.frames, self.fps, self.fps_t = 0, 0, time.perf_counter()
        self.status = "Ein oder mehrere Formate wählen  ·  Esc schließt" if self.targets else "Dieser Dateityp wird nicht unterstützt"

        self.small_path = cloud_path(BW, BH)
        self.big_path = cloud_path(CW, CH)
        self.preview = make_preview(self.path)
        self.small_glows = self.build_glows(self.small_path, BW, BH)
        self.big_glows = self.build_glows(self.big_path, CW, CH)
        self.center_fill = self.build_center_fill()

        n = max(len(self.targets), 1)
        self.bubbles = []
        for i, fmt in enumerate(self.targets):
            a = -math.pi / 2 + i * 2 * math.pi / n
            self.bubbles.append({
                "bx": CX + ORBIT_X * math.cos(a), "by": CY + ORBIT_Y * math.sin(a),
                "fmt": fmt, "phase": i * 1.3, "speed": 0.8 + (i % 3) * 0.15,
                "hue": i / n, "hover": 0.0, "x": 0.0, "y": 0.0, "state": None,
            })

        self.setWindowTitle("FormatFix")
        icon = Path(__file__).with_name("icon.ico")
        if getattr(sys, "frozen", False):
            icon = Path(getattr(sys, "_MEIPASS", "")) / "icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setFixedSize(W, H)
        self.setMouseTracking(True)
        self.font_fmt = QFont("Segoe UI Semibold", 10)
        self.font_name = QFont("Segoe UI Semibold", 8)
        self.font_status = QFont("Segoe UI", 8)
        self.font_big = QFont("Segoe UI Semibold", 20)
        self.font_state = QFont("Segoe UI", 7)

        threading.Thread(target=self.worker, daemon=True).start()

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / FPS))

    # ---------- Animation ----------
    def tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self.start - self.t)
        self.t = now - self.start
        hovered = self.bubble_at(self.mouse)
        for i, b in enumerate(self.bubbles):
            s = b["speed"] * FLOAT_SPEED
            b["x"] = b["bx"] + math.sin(self.t * s + b["phase"]) * FLOAT
            b["y"] = b["by"] + math.cos(self.t * s * 0.8 + b["phase"]) * FLOAT
            target = 1.0 if i == hovered else 0.0
            b["hover"] += (target - b["hover"]) * min(1.0, dt * 12)  # weiches Ein-/Ausblenden
        self.setCursor(Qt.CursorShape.PointingHandCursor if hovered is not None
                       else Qt.CursorShape.ArrowCursor)
        self.check_result()
        self.frames += 1
        if now - self.fps_t >= 0.5:
            self.fps = self.frames / (now - self.fps_t)
            self.frames, self.fps_t = 0, now
        self.update()

    def bubble_at(self, p):
        for i, b in enumerate(self.bubbles):
            if self.small_path.contains(QPointF(p.x() - b["x"], p.y() - b["y"])):
                return i
        return None

    # ---------- Aura einmal vorberechnen (hält die Animation flüssig) ----------
    def build_glows(self, path, w, h):
        m = AURA_SIZE + 6
        size = (int(w + 2 * m), int(h + 2 * m))
        glows = []
        for i in range(GLOW_HUES):
            img = QImage(size[0], size[1], QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            qp = QPainter(img)
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            qp.translate(size[0] / 2, size[1] / 2)
            layers = 10
            for k in range(layers, 0, -1):
                f = k / layers
                pen = QPen(rainbow(i / GLOW_HUES, 255 * (1 - f) ** 1.6 * 0.6), AURA_SIZE * 2 * f)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                qp.setPen(pen)
                qp.drawPath(path)
            qp.end()
            glows.append(QPixmap.fromImage(img))
        return glows

    def build_center_fill(self):
        if self.preview is None:
            return None
        img = QImage(CW + 4, CH + 4, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        qp = QPainter(img)
        qp.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        qp.translate((CW + 4) / 2, (CH + 4) / 2)
        qp.setClipPath(self.big_path)
        qp.drawPixmap(QRectF(-CW / 2, -CH / 2, CW, CH), self.preview, QRectF(self.preview.rect()))
        qp.end()
        return QPixmap.fromImage(img)

    # ---------- Zeichnen ----------
    def draw_cloud(self, qp, path, glows, x, y, hue, pulse, hover, scale=1.0, fill_pix=None):
        qp.save()
        qp.translate(x, y)
        qp.scale(scale, scale)
        strength = min(1.0, (PULSE_MIN + (PULSE_MAX - PULSE_MIN) * pulse) * AURA_POWER + hover * 0.35)
        # Aura: zwei benachbarte Farbstufen fließend überblenden
        hf = (hue % 1.0) * GLOW_HUES
        i0 = int(hf) % GLOW_HUES
        i1 = (i0 + 1) % GLOW_HUES
        frac = hf - int(hf)
        g = glows[i0]
        rect = QRectF(-g.width() / 2, -g.height() / 2, g.width(), g.height())
        qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        qp.setOpacity(strength * (1 - frac))
        qp.drawPixmap(rect, glows[i0], QRectF(g.rect()))
        qp.setOpacity(strength * frac)
        qp.drawPixmap(rect, glows[i1], QRectF(g.rect()))
        qp.setOpacity(1.0)
        qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # Fläche
        qp.setPen(Qt.PenStyle.NoPen)
        if fill_pix is not None:
            qp.drawPixmap(QRectF(-fill_pix.width() / 2, -fill_pix.height() / 2,
                                 fill_pix.width(), fill_pix.height()), fill_pix, QRectF(fill_pix.rect()))
        else:
            fill = QColor(
                int(FILL.red() + (FILL_HOVER.red() - FILL.red()) * hover),
                int(FILL.green() + (FILL_HOVER.green() - FILL.green()) * hover),
                int(FILL.blue() + (FILL_HOVER.blue() - FILL.blue()) * hover))
            qp.setBrush(fill)
            qp.drawPath(path)
        # scharfe Kontur
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.setPen(QPen(rainbow(hue), 1.6))
        qp.drawPath(path)
        qp.restore()

    def paintEvent(self, event):
        qp = QPainter(self)
        qp.setRenderHints(QPainter.RenderHint.Antialiasing |
                          QPainter.RenderHint.SmoothPixmapTransform |
                          QPainter.RenderHint.TextAntialiasing)
        qp.fillRect(self.rect(), BG)
        base = self.t * HUE_SPEED

        # Mittel-Wolke
        pulse = (math.sin(self.t * PULSE_SPEED) + 1) / 2
        self.draw_cloud(qp, self.big_path, self.big_glows, CX, CY, base + 0.55, pulse, 0.0,
                        fill_pix=self.center_fill)
        if self.preview is None:
            qp.setPen(SUBTEXT)
            qp.setFont(self.font_big)
            label = self.path.suffix.upper().lstrip(".") or "DATEI"
            qp.drawText(QRectF(CX - CW / 2, CY - 20, CW, 50), Qt.AlignmentFlag.AlignCenter, label)
        name = self.path.name if len(self.path.name) <= 32 else self.path.name[:29] + "…"
        qp.setFont(self.font_name)
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):     # dezenter Schatten für helle Dateien
            qp.setPen(QColor(0, 0, 0, 150))
            qp.drawText(QRectF(CX - CW / 2 + dx, CY + 48 + dy, CW, 22), Qt.AlignmentFlag.AlignCenter, name)
        qp.setPen(TEXT)
        qp.drawText(QRectF(CX - CW / 2, CY + 48, CW, 22), Qt.AlignmentFlag.AlignCenter, name)

        # Format-Wolken
        qp.setFont(self.font_fmt)
        for b in self.bubbles:
            pulse = (math.sin(self.t * PULSE_SPEED + b["phase"]) + 1) / 2
            scale = 1.0 + 0.06 * b["hover"]
            self.draw_cloud(qp, self.small_path, self.small_glows, b["x"], b["y"], base + b["hue"],
                            pulse, b["hover"], scale)
            sub, color = self.state_label(b["state"])
            dy = -7 if sub else 0
            qp.setPen(TEXT)
            qp.setFont(self.font_fmt)
            qp.drawText(QRectF(b["x"] - BW / 2, b["y"] - 2 + dy, BW, 24),
                        Qt.AlignmentFlag.AlignCenter, LABELS.get(b["fmt"], b["fmt"].upper()))
            if sub:
                qp.setPen(color)
                qp.setFont(self.font_state)
                qp.drawText(QRectF(b["x"] - BW / 2, b["y"] + 13, BW, 16),
                            Qt.AlignmentFlag.AlignCenter, sub)

        # Statuszeile
        status = self.status + (f"   ·   {self.fps:.0f} FPS" if self.show_fps else "")
        qp.setPen(SUBTEXT)
        qp.setFont(self.font_status)
        qp.drawText(QRectF(0, H - 34, W, 24), Qt.AlignmentFlag.AlignCenter, status)
        qp.end()

    # ---------- Interaktion ----------
    def mouseMoveEvent(self, e):
        self.mouse = e.position()

    def leaveEvent(self, e):
        self.mouse = QPointF(-1000, -1000)

    def mousePressEvent(self, e):
        i = self.bubble_at(e.position())
        if i is None:
            return
        b = self.bubbles[i]
        if b["state"] in ("wait", "work"):
            return                          # läuft schon – kein Doppelklick-Chaos
        b["state"] = "wait"
        self.jobs.put(i)

    def state_label(self, state):
        if state == "wait":
            return "wartet", SUBTEXT
        if state == "work":
            return "läuft" + "." * (int(self.t * 3) % 4), QColor(140, 200, 255)
        if state == "done":
            return "\u2713 erstellt", QColor(110, 220, 150)
        if state == "error":
            return "\u2717 Fehler", QColor(255, 120, 120)
        return "", TEXT

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif e.key() == Qt.Key.Key_F:
            self.show_fps = not self.show_fps   # F zeigt die Bildrate an

    # ---------- Hintergrund-Arbeit ----------
    def worker(self):
        """Arbeitet die angeklickten Formate nacheinander ab. Das Fenster bleibt dabei offen."""
        while True:
            i = self.jobs.get()
            b = self.bubbles[i]
            b["state"] = "work"
            try:
                dst = convert(str(self.path), b["fmt"])
                b["state"] = "done"
                self.messages.put(f"Erstellt: {dst.name}")
            except Exception as e:
                b["state"] = "error"
                self.messages.put(f"Fehler bei .{b['fmt']}: {e}")

    def check_result(self):
        while not self.messages.empty():
            self.status = self.messages.get() + "   ·   weitere Formate möglich"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Aufruf: gui.py <Datei>")
        sys.exit(1)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    win = FormatFix(sys.argv[1])
    win.show()
    win.raise_()
    win.activateWindow()
    sys.exit(app.exec())
