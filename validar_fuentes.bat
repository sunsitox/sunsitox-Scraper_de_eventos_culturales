@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ============================================================
echo  Auditoria de fuentes culturales de Chile
echo ============================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [AVISO] No existe el entorno virtual. Iniciando instalacion...
    call instalar.bat --no-pause
    if errorlevel 1 goto :error
)

if not exist "data\source_candidates.json" (
    echo [ERROR] Falta data\source_candidates.json.
    echo Importa el inventario con tools\import_source_inventory.py.
    goto :error
)

if not defined SOURCE_VALIDATION_WORKERS set "SOURCE_VALIDATION_WORKERS=6"
echo Se revisaran todas las entradas. Esto puede tardar varios minutos.
echo.
"venv\Scripts\python.exe" validate_sources.py --workers %SOURCE_VALIDATION_WORKERS%
if errorlevel 1 goto :error

echo.
echo [OK] Resultados: data\source_validation
set "EXIT_CODE=0"
goto :finish

:error
set "EXIT_CODE=1"
echo.
echo [ERROR] La auditoria no pudo completarse.

:finish
if /I not "%~1"=="--no-pause" pause
exit /b %EXIT_CODE%
