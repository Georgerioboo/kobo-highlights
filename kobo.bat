@echo off
rem Kobo Highlights: doble clic para abrir el menu de exportacion.
rem Tambien acepta opciones, por ejemplo: kobo.bat -f txt --no-notion

rem Trabajar siempre desde la carpeta donde esta este archivo
cd /d "%~dp0"

rem Mostrar bien tildes y emojis en la consola
chcp 65001 >nul
set PYTHONUTF8=1

rem Usar el Python del entorno virtual si existe; si no, el del sistema
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" kobo_highlights.py %*

echo.
pause
