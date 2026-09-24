# Contenido del repositorio

Esta carpeta contiene una copia autocontenida del scraper de eventos culturales, preparada para publicarse en GitHub. La copia no modifica el proyecto de trabajo original.

## Estructura

```text
.github/workflows/   Automatización y validación en GitHub Actions
data/                Inventario JSON canónico de fuentes candidatas
docs/                Arquitectura, Supabase, fuentes, auditoría e inventario Excel
eventos/             Paquete Python y conectores de extracción
sources/             Configuración declarativa de las fuentes
supabase/            Configuración y migraciones SQL
tests/               Pruebas automatizadas
tools/               Herramientas de checkpoints y documentación
```

En la raíz están los comandos de instalación, ejecución, migración y validación, además de `scrape_events.py`, `requirements.txt`, `settings.json`, `.env.example` y `.gitignore`.

## Archivos excluidos intencionalmente

- `.env` y credenciales reales.
- `venv/`, `.venv/` y paquetes instalados localmente.
- Navegadores descargados por Playwright.
- Cachés, archivos `__pycache__` y bytecode.
- `state/`, checkpoints SQLite y respaldos temporales.
- Eventos JSON/CSV generados, métricas, logs y resultados de ejecución.
- Directorios de trabajo usados en validaciones anteriores.

Estos archivos se recrean durante la instalación o ejecución y no deben versionarse. Las credenciales para GitHub Actions deben configurarse como secretos del repositorio.

## Inicio rápido en Windows

1. Copiar `.env.example` como `.env` y completar las credenciales localmente.
2. Ejecutar `instalar.bat` para crear el entorno e instalar dependencias.
3. Ejecutar `migrar_supabase.bat` cuando sea necesario aplicar las migraciones.
4. Ejecutar `ejecutar.bat` para iniciar el pipeline completo.

La periodicidad del workflow todavía no está definida. La ejecución automática permanece disponible mediante `workflow_dispatch` y el bloque `schedule` continúa comentado.
