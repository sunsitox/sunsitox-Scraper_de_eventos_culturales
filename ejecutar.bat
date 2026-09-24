@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Ejecucion - Agregador de eventos culturales
echo ============================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [AVISO] No existe el entorno virtual. Iniciando instalacion...
    call instalar.bat --no-pause
    if errorlevel 1 goto :error
)

set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright-browsers"
if not defined SOURCE_WORKERS set "SOURCE_WORKERS=4"

echo Ejecutando la extraccion completa...
echo Procesamiento paralelo: %SOURCE_WORKERS% trabajadores por dominios independientes.
echo Maipu abrira una ventana temporal; las otras fuentes trabajan en segundo plano.
echo Este proceso puede tardar varios minutos.
echo.
"venv\Scripts\python.exe" scrape_events.py
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Extraccion terminada correctamente.
echo  Resultados: data\events.json y data\events.csv
echo ============================================================
set "EXIT_CODE=0"
goto :finish

:error
set "EXIT_CODE=1"
echo.
echo ============================================================
echo  La ejecucion termino con errores.
echo  Revisa el mensaje mostrado arriba.
echo ============================================================

:finish
if /I not "%~1"=="--no-pause" pause
exit /b %EXIT_CODE%
