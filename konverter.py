import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from PIL import Image

# ---------- Hilfsprogramme automatisch finden ----------
# Funktioniert auf jedem PC: zuerst neben der FormatFix.exe, dann im System.

def app_dir():
    """Ordner, in dem FormatFix liegt (bei der .exe: der Programmordner)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def find_ffmpeg():
    for c in (app_dir() / "ffmpeg" / "ffmpeg.exe", app_dir() / "ffmpeg.exe"):
        if c.exists():
            return str(c)
    found = shutil.which("ffmpeg")
    if found:
        return found
    # Entwicklungs-PC: entpackter Ordner unter C:\ffmpeg
    for c in Path("C:/ffmpeg").glob("*/bin/ffmpeg.exe"):
        return str(c)
    return None


def find_soffice():
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for r in roots:
        c = Path(r) / "LibreOffice" / "program" / "soffice.exe"
        if c.exists():
            return str(c)
    return shutil.which("soffice")


# Unter Windows kein schwarzes Konsolenfenster aufblitzen lassen
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ---------- Welche Dateien erkannt werden (Eingang) ----------
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".tif", ".ico"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".3gp",
             ".mpg", ".mpeg", ".ts", ".mts", ".m2ts"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
PDF_EXT = {".pdf"}
WORD_EXT = {".docx", ".doc", ".odt", ".rtf", ".txt"}
SHEET_EXT = {".xlsx", ".xls", ".ods", ".csv"}
SLIDE_EXT = {".pptx", ".ppt", ".odp"}

# ---------- Welche Formate angeboten werden (Ziel) ----------
IMAGE_TARGETS = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "pdf"]
VIDEO_TARGETS = ["mp4", "mkv", "mov", "avi", "wmv", "webm", "gif", "mp3", "wav"]
AUDIO_TARGETS = ["mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "mid"]
PDF_TARGETS = ["png", "jpg", "txt"]
WORD_TARGETS = ["pdf", "docx", "odt", "rtf", "txt", "html"]
SHEET_TARGETS = ["pdf", "xlsx", "ods", "csv", "html"]
SLIDE_TARGETS = ["pdf", "pptx", "odp"]

OFFICE_EXT = WORD_EXT | SHEET_EXT | SLIDE_EXT


def targets_for(ext):
    table = [
        (IMAGE_EXT, IMAGE_TARGETS),
        (VIDEO_EXT, VIDEO_TARGETS),
        (AUDIO_EXT, AUDIO_TARGETS),
        (PDF_EXT, PDF_TARGETS),
        (WORD_EXT, WORD_TARGETS),
        (SHEET_EXT, SHEET_TARGETS),
        (SLIDE_EXT, SLIDE_TARGETS),
    ]
    same = {".jpeg": "jpg", ".jpg": "jpg", ".tif": "tiff"}
    own = same.get(ext, ext.lstrip("."))
    for exts, targets in table:
        if ext in exts:
            return [t for t in targets if t != own]
    return []


def unique_path(src, target):
    dst = src.with_suffix("." + target)
    n = 1
    while dst.exists():
        suffix = "" if n == 1 else str(n)
        dst = src.with_name(f"{src.stem}_konvertiert{suffix}.{target}")
        n += 1
    return dst


def convert_image(src, dst):
    img = Image.open(src)
    target = dst.suffix.lower()
    if target in (".jpg", ".jpeg", ".pdf", ".bmp"):
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(dst)
    elif target == ".ico":
        img = img.convert("RGBA")
        img.thumbnail((256, 256))
        img.save(dst, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        img.save(dst)


def convert_pdf(src, dst, target):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(src))
    try:
        if target == "txt":
            parts = []
            for i in range(len(pdf)):
                page = pdf[i]
                tp = page.get_textpage()
                parts.append(tp.get_text_range())
                tp.close()
                page.close()
            dst.write_text("\n\n".join(parts), encoding="utf-8")
        else:
            # Bild-Export: erste Seite in guter Qualität (ca. 150 dpi)
            page = pdf[0]
            img = page.render(scale=150 / 72).to_pil().convert("RGB")
            page.close()
            img.save(dst)
    finally:
        pdf.close()


def convert_office(src, dst, target):
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("Für Word/Excel/PowerPoint wird das kostenlose "
                           "LibreOffice benötigt (libreoffice.org)")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [soffice, "--headless", "--convert-to", target, "--outdir", tmp, str(src)],
            check=True, capture_output=True, timeout=180, creationflags=NO_WINDOW,
        )
        produced = Path(tmp) / (src.stem + "." + target)
        if not produced.exists():
            raise RuntimeError("LibreOffice konnte die Datei nicht umwandeln.")
        shutil.move(str(produced), str(dst))


def convert_media(src, dst):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg fehlt – der Ordner 'ffmpeg' muss neben FormatFix.exe liegen.")
    if dst.suffix.lower() == ".mid":
        # Audio → MIDI: Noten erkennen (offline, siehe audio2midi.py)
        import audio2midi
        audio2midi.convert_to_midi(src, dst, ffmpeg, NO_WINDOW)
        return
    try:
        subprocess.run([ffmpeg, "-y", "-i", str(src), str(dst)],
                       check=True, capture_output=True, creationflags=NO_WINDOW)
    except subprocess.CalledProcessError:
        if dst.exists():
            dst.unlink()        # keine kaputte, halbe Datei liegen lassen
        raise RuntimeError("Umwandlung fehlgeschlagen (Datei beschädigt oder Format nicht möglich).")


def convert(src_str, target):
    src = Path(src_str)
    ext = src.suffix.lower()
    dst = unique_path(src, target)

    if ext in IMAGE_EXT:
        convert_image(src, dst)
    elif ext in PDF_EXT:
        convert_pdf(src, dst, target)
    elif ext in OFFICE_EXT:
        convert_office(src, dst, target)
    else:
        convert_media(src, dst)
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Aufruf: konverter.py <Datei> <Zielformat>")
        sys.exit(1)
    try:
        print(f"Fertig: {convert(sys.argv[1], sys.argv[2].lower())}")
    except Exception as e:
        print(f"Fehler: {e}")
