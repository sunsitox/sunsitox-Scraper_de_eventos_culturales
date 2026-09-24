@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Instalacion - Agregador de eventos culturales
echo ============================================================
echo.

if exist "venv\Scripts\python.exe" (
    echo [OK] El entorno virtual ya existe.
    goto :install_dependencies
)

echo [1/5] Creando el entorno virtual...
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv venv
    goto :check_venv
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No se encontro Python.
    echo Instala Python 3 desde https://www.python.org/downloads/
    goto :error
)

python -m venv venv

:check_venv
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] No se pudo crear el entorno virtual.
    goto :error
)
echo [OK] Entorno virtual creado.

:install_dependencies
echo [2/5] Actualizando pip...
"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/5] Instalando las dependencias de Python y del cliente API de Supabase...
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/5] Instalando Chromium para el conector de Maipu...
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright-browsers"
"venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo [AVISO] Se creo .env desde .env.example.
        echo         Agrega tu NVIDIA_API_KEY antes de usar el respaldo IA.
        echo         Para sincronizar, agrega tambien SUPABASE_URL y SUPABASE_SECRET_KEY.
    )
)

echo [5/5] Verificando configuracion de Supabase...
if not exist ".env" (
    echo [AVISO] No se encontro .env; la sincronizacion con Supabase queda desactivada.
    goto :installation_complete
)

REM Se usa el mismo lector y los mismos valores que utiliza el pipeline.
REM override=True hace que esta verificacion examine el archivo .env y no una
REM variable antigua heredada desde Windows o desde la consola.
"venv\Scripts\python.exe" -c "from dotenv import load_dotenv; load_dotenv('.env', override=True); from eventos.services.supabase import supabase_enabled; raise SystemExit(0 if supabase_enabled() else 2)"
if errorlevel 1 (
    echo [INFO] Supabase esta desactivado en .env.
    echo        Para cargar eventos por API, completa SUPABASE_URL y SUPABASE_SECRET_KEY
    echo        y cambia SUPABASE_ENABLED=true. Tambien se aceptan enable, enabled, 1, yes y on.
    goto :installation_complete
)

"venv\Scripts\python.exe" -c "import os; from dotenv import load_dotenv; load_dotenv('.env', override=True); url=os.getenv('SUPABASE_URL','').strip(); key=(os.getenv('SUPABASE_SECRET_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY') or '').strip(); invalid=(not url.startswith('https://') or 'tu-proyecto' in url or not key or 'reemplaza' in key); raise SystemExit(2 if invalid else 0)"
if errorlevel 1 (
    echo [AVISO] Supabase esta activado, pero .env aun tiene valores de ejemplo.
    echo         Reemplaza SUPABASE_URL y SUPABASE_SECRET_KEY antes de ejecutar el scraper.
    goto :installation_complete
)

echo [OK] Supabase esta habilitado para la carga por API.

:installation_complete

echo.
echo ============================================================
echo  Instalacion completada correctamente.
echo  Ahora puedes ejecutar: ejecutar.bat
echo  La carga de eventos usa las credenciales de .env; no requiere Supabase CLI.
echo ============================================================
set "EXIT_CODE=0"
goto :finish

:error
set "EXIT_CODE=1"
echo.
echo ============================================================
echo  La instalacion no pudo completarse.
echo  Revisa el mensaje de error mostrado arriba.
echo ============================================================

:finish
if /I not "%~1"=="--no-pause" pause
exit /b %EXIT_CODE%
