@echo off
chcp 65001 >nul
title FormatFix bauen
cd /d "%~dp0"

echo.
echo  =====================================================
echo    FormatFix wird gebaut. Das dauert 2 bis 5 Minuten.
echo    Bitte dieses Fenster offen lassen.
echo  =====================================================
echo.

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo FEHLER: Die Python-Umgebung .venv wurde nicht gefunden.
    goto fehler
)

echo [1/5] Werkzeuge installieren ...
"%PY%" -m pip install --quiet --upgrade pyinstaller pyside6 pillow pypdfium2 numpy onnxruntime
if errorlevel 1 goto fehler

echo [2/5] FormatFix.exe bauen ...
"%PY%" -m PyInstaller --noconfirm --clean --log-level WARN ^
    --windowed --name FormatFix --icon icon.ico ^
    --add-data "icon.ico;." --add-data "nmp.onnx;." --hidden-import audio2midi ^
    --exclude-module tkinter --exclude-module fitz --exclude-module pymupdf ^
    --exclude-module pynput --exclude-module scipy --exclude-module pandas ^
    formatfix.py
if errorlevel 1 goto fehler

echo [3/5] ffmpeg dazulegen ...
set "FF="
for /d %%D in ("C:\ffmpeg\*") do if exist "%%~D\bin\ffmpeg.exe" set "FF=%%~D"
if not defined FF (
    echo FEHLER: ffmpeg wurde unter C:\ffmpeg nicht gefunden.
    goto fehler
)
mkdir "dist\FormatFix\ffmpeg" 2>nul
copy /y "%FF%\bin\ffmpeg.exe" "dist\FormatFix\ffmpeg\" >nul
copy /y "%FF%\LICENSE" "dist\FormatFix\ffmpeg\LICENSE.txt" >nul

echo [4/5] Anleitung, Lizenzen und Quellcode dazulegen ...
copy /y "Anleitung.txt" "dist\FormatFix\" >nul
copy /y "Lizenzen.txt" "dist\FormatFix\" >nul
copy /y "LICENSE-basic-pitch.txt" "dist\FormatFix\" >nul
mkdir "dist\FormatFix\Quellcode" 2>nul
copy /y formatfix.py "dist\FormatFix\Quellcode\" >nul
copy /y gui.py "dist\FormatFix\Quellcode\" >nul
copy /y konverter.py "dist\FormatFix\Quellcode\" >nul
copy /y audio2midi.py "dist\FormatFix\Quellcode\" >nul
copy /y nmp.onnx "dist\FormatFix\Quellcode\" >nul
copy /y icon.ico "dist\FormatFix\Quellcode\" >nul
copy /y build.bat "dist\FormatFix\Quellcode\" >nul

echo [5/5] ZIP zum Weitergeben erstellen ...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\FormatFix' -DestinationPath 'dist\FormatFix.zip' -Force"
if errorlevel 1 goto fehler

rem Aufräumen: Zwischendateien von PyInstaller entfernen
rmdir /s /q build 2>nul
del /q FormatFix.spec 2>nul

echo.
echo  FERTIG!
echo    Programm:      dist\FormatFix\FormatFix.exe
echo    Weitergeben:   dist\FormatFix.zip
echo.
explorer "dist"
pause
exit /b 0

:fehler
echo.
echo  Etwas ist schiefgelaufen. Mach einen Screenshot von diesem Fenster
echo  und schick ihn an Claude.
echo.
pause
exit /b 1
