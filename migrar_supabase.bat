@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"

set "SUPABASE_RUNNER="
where supabase >nul 2>nul
if not errorlevel 1 set "SUPABASE_RUNNER=supabase"

if not defined SUPABASE_RUNNER (
    where npx >nul 2>nul
    if not errorlevel 1 set "SUPABASE_RUNNER=npx --yes supabase@latest"
)

if not defined SUPABASE_RUNNER (
    echo.
    echo [ERROR] No se encontro Supabase CLI ni npx.
    echo Instala Node.js 20 o superior desde https://nodejs.org/
    echo Despues vuelve a ejecutar este archivo; no necesitas una instalacion global.
    echo.
    set "EXIT_CODE=1"
    goto :finish
)

echo Comprobando Supabase CLI...
call %SUPABASE_RUNNER% --version
if errorlevel 1 goto :error

if not exist "supabase\config.toml" (
    echo.
    echo Inicializando la configuracion local de migraciones...
    call %SUPABASE_RUNNER% init
    if errorlevel 1 goto :error
)

set "SUPABASE_PROJECT_INPUT=%~1"
set "PROJECT_REF="
for /f "delims=" %%R in ('"venv\Scripts\python.exe" -m eventos.supabase_project') do set "PROJECT_REF=%%R"
set "SUPABASE_PROJECT_INPUT="

if "%PROJECT_REF%"=="" (
    echo.
    set "EXIT_CODE=1"
    goto :finish
)

echo [OK] Project ref detectado: %PROJECT_REF%

echo.
echo Iniciando sesion en Supabase si es necesario...
echo Esta sesion usa un token de cuenta o el navegador, no SUPABASE_SECRET_KEY.
call %SUPABASE_RUNNER% login
if errorlevel 1 goto :error

echo.
echo Vinculando el proyecto %PROJECT_REF%...
echo La CLI puede solicitar la contrasena de la base de datos.
call %SUPABASE_RUNNER% link --project-ref "%PROJECT_REF%"
if errorlevel 1 goto :error

echo.
echo Revisando las migraciones pendientes...
call %SUPABASE_RUNNER% db push --dry-run
if errorlevel 1 goto :error

echo.
echo Aplicando migraciones pendientes...
call %SUPABASE_RUNNER% db push
if errorlevel 1 goto :error

echo.
echo Migraciones aplicadas correctamente.
set "EXIT_CODE=0"
goto :finish

:error
set "EXIT_CODE=1"
echo.
echo No se pudieron aplicar las migraciones. Revisa el mensaje anterior.

:finish
echo.
if /I not "%~2"=="--no-pause" pause
endlocal & exit /b %EXIT_CODE%
