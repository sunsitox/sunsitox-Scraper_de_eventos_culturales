# Integración con Supabase

El boceto relacional se adaptó al modelo real del recolector. No se copiaron sus columnas:
solo se conservó la idea de separar entidades y relaciones.

El significado detallado de todas las tablas, columnas, relaciones y categorías está disponible
en [`DATABASE.md`](DATABASE.md).

## Modelo resultante

| Tabla | Responsabilidad |
|---|---|
| `events` | Campos normalizados de `Event`, URLs de origen y control de vigencia |
| `categories` + `event_categories` | Relación muchos-a-muchos; conserva todas las categorías |
| `sources` | Sitio o agenda de procedencia |
| `organizers` | Entidad organizadora normalizada |
| `communes` | Comuna, región y país |
| `media_assets` | Imágenes actuales y futuros videos, audios o documentos |
| `event_provenance` | Texto original y OCR privados para trazabilidad y para evitar reprocesamiento |
| `scrape_runs` | Estado, cantidad y metadatos de cada ejecución local o de GitHub Actions |
| `catalog_staging` | Fragmentos privados previos a la publicación atómica del catálogo |
| `profiles`, `favorites`, `reports` | Base futura de la aplicación; no interviene en el scraper |

Los identificadores que genera Python son UUID v5 deterministas. Ejecutar dos veces el mismo
scraping actualiza las filas existentes mediante `upsert` y no crea duplicados. Las categorías
y la imagen de los eventos incluidos en el lote se regeneran para retirar relaciones antiguas.

## 1. Crear las tablas

Las migraciones están en `supabase/migrations/` y deben aplicarse en orden:

1. `20260831000100_initial_event_catalog.sql`: catálogo, relaciones y RLS.
2. `20260902000100_event_location_metadata.sql`: ubicación canónica, precisión y procedencia.
3. `20260911000100_content_enrichment_and_organizer_controls.sql`: OCR y texto de origen
   privados, coordenadas, deduplicación previa y bloqueo de organizadores.
4. `20260912000100_rewrite_state_and_reconciliation.sql`: estado real de redacción y soporte
   para reconciliación incremental por fuente.
5. `20260921000100_atomic_catalog_publication.sql`: staging privado y publicación transaccional
   del catálogo, sus relaciones y la limpieza.

En un proyecto nuevo, vincula Supabase CLI y aplica las migraciones:

```powershell
supabase login
supabase link --project-ref TU_PROJECT_REF
supabase db push
```

En Windows puedes automatizarlo con [`migrar_supabase.bat`](../migrar_supabase.bat). El script obtiene
el `project ref` automáticamente desde `SUPABASE_URL` en `.env`, inicializa `supabase/config.toml`
si falta, inicia sesión, comprueba los cambios con `--dry-run`, vincula el proyecto y aplica solo
las migraciones pendientes. También puedes entregarle un identificador o una URL directamente:

```powershell
migrar_supabase.bat
migrar_supabase.bat TU_PROJECT_REF
migrar_supabase.bat https://TU_PROJECT_REF.supabase.co
```

No hace falta instalar la CLI globalmente. El script usa el comando global si ya existe y, en caso
contrario, ejecuta automáticamente `npx --yes supabase@latest`. Para esta segunda opción se requiere
Node.js 20 o superior. El inicio de sesión de la CLI utiliza la cuenta de Supabase y el enlace puede
solicitar la contraseña de Postgres; `SUPABASE_SECRET_KEY` sirve para cargar filas por REST, pero no
concede permisos para ejecutar migraciones SQL.

Para una prueba puntual también puedes copiar las cinco migraciones, en ese orden, en el SQL
Editor, pero después conviene mantener todos los cambios mediante migraciones versionadas.

La migración habilita RLS. El público solamente puede leer el catálogo publicado. La escritura
del catálogo queda reservada a la clave secreta del proceso de ingesta; perfiles, favoritos y
reportes tienen políticas por usuario.

## 2. Configurar la ejecución local

`instalar.bat` instala las dependencias necesarias para esta conexión y revisa si la configuración
de Supabase está activa. La carga de eventos se realiza por la API REST con estas variables; no
requiere instalar ni autenticar la CLI de Supabase.

Completa estas variables en `.env`:

```dotenv
SUPABASE_ENABLED=true
SUPABASE_URL=https://TU_PROJECT_REF.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
```

Usa una clave **Secret** `sb_secret_*`, no la clave publicable. Para proyectos antiguos también
se admite `SUPABASE_SERVICE_ROLE_KEY`, pero no es la opción recomendada para una instalación nueva.

Ejecuta una sincronización obligatoria con:

```powershell
python scrape_events.py --supabase
```

`--local-only` evita la carga aunque `SUPABASE_ENABLED=true`. Sin banderas se respeta el valor de
`SUPABASE_ENABLED`. Los archivos JSON y CSV continúan generándose como respaldo local.

Si el scraping ya terminó y solo falló Supabase, no es necesario consultar nuevamente las fuentes:

```powershell
python scrape_events.py --sync-existing
```

Ese comando carga `data/events.json`, vuelve a sanearlo, elimina sus vencidos, regenera los archivos
locales y lo sincroniza. No extrae páginas, no llama a NVIDIA y exige las credenciales secretas de
Supabase en `.env`.

Para convertir ese dataset y sus métricas en un snapshot autoritativo y retirar registros antiguos:

```powershell
python scrape_events.py --sync-existing --reconcile-existing
```

La reconciliación incluye fuentes con un snapshot `succeeded` y también `complete_empty`. Este
último estado solo se emite después de completar el conector sin errores estructurales. Las fuentes
`partial` o `failed` nunca autorizan un borrado masivo.

Para reintentar además el OCR y parafraseo de las filas pendientes sin repetir el scraping:

```powershell
python scrape_events.py --sync-existing --enrich-existing
```

Puede combinarse con `--reconcile-existing`. Supabase decide cuáles siguen pendientes; las filas
correctamente redactadas no vuelven a consumir NVIDIA.

## Vigencia y eliminación automática

Con `SUPABASE_DELETE_EXPIRED=true` (valor predeterminado), cada carga elimina físicamente:

- eventos cuyo `end_at` sea anterior a la hora actual en Chile;
- eventos sin `end_at` cuyo `start_at` sea anterior al día actual.

Además, `SUPABASE_RECONCILE_STALE=true` retira los eventos que ya no aparecen en el snapshot de una
fuente completa, aunque el nuevo snapshot sea legítimamente vacío, incluidos enlaces eliminados o
contenidos que ahora quedan fuera de los filtros. Las exclusiones manuales (`is_suppressed=true`) se
conservan para impedir que el scraper las publique nuevamente. Las fuentes fallidas o parciales
quedan fuera de esta limpieza.

Las filas relacionadas de `event_categories`, `media_assets`, favoritos y reportes se eliminan por
`ON DELETE CASCADE`. Los eventos sin fecha inicial válida no se publican. La carga sube primero
fragmentos privados a `catalog_staging`; una función RPC aplica entidades, relaciones, reconciliación
y vencimiento dentro de una sola transacción PostgreSQL.

## Bloquear un organizador y sus eventos

El nombre del organizador queda en `organizers` y cada evento lo referencia mediante
`events.organizer_id`. Para atender una solicitud de exclusión sin perder trazabilidad:

```sql
update public.organizers
set is_blocked = true,
    blocked_reason = 'Solicitud del organizador',
    blocked_at = now()
where id = 'UUID_DEL_ORGANIZADOR';
```

Las políticas RLS ocultan inmediatamente el organizador, sus eventos, categorías relacionadas e
imágenes. El scraper consulta esta lista antes de OCR y redacción, por lo que tampoco vuelve a gastar
cuota de IA en sus eventos. Para revertirlo, cambia `is_blocked` a `false` y limpia `blocked_reason`
y `blocked_at`. Si se requiere eliminación física, puede borrarse el evento desde el panel o SQL;
el bloqueo es la opción recomendada porque es reversible y evita que la siguiente corrida lo recree.

Si la solicitud afecta solamente a un evento y no a toda la organización, se puede excluir esa
fila sin que el siguiente scraping la vuelva a publicar:

```sql
update public.events
set is_suppressed = true,
    suppression_reason = 'Solicitud del organizador',
    suppressed_at = now()
where id = 'UUID_DEL_EVENTO';
```

La deduplicación previa reconoce la fila excluida y la retira antes de OCR, redacción y carga. La
vista `public_event_catalog` expone directamente `organizer_name` y `source_name` junto con las
columnas del evento, respetando estas reglas de visibilidad.

## Reconciliación y enlaces retirados

Una ejecución completa actualiza `last_seen_at` y elimina las filas no vistas de cada fuente que
haya terminado como `succeeded` o `complete_empty`. Esto elimina fichas antiguas aunque su fecha
almacenada sea futura. No se reconcilian fuentes fallidas o parciales y se conservan las exclusiones
manuales.

Las fuentes con `validate_event_urls=true` comprueban sus fichas individuales antes de sincronizar.
Solo un HTTP 404/410 o una página 404 inequívoca elimina el evento; timeouts, errores 5xx y bloqueos
temporales se consideran resultados inciertos y conservan la fila.

## 3. Preparar GitHub Actions

El workflow `.github/workflows/scrape-events.yml` instala Python, Playwright y Chromium,
ejecuta las pruebas, abre Maipú dentro de una pantalla virtual y finalmente sincroniza Supabase.
Al finalizar, el propio programa añade el total de eventos y el tiempo total al resumen visual de
la ejecución en GitHub Actions. No hace falta seguir el log en vivo ni agregar otro paso al workflow.

Crea estos Repository secrets en GitHub:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `NVIDIA_API_KEY` (opcional; las fuentes deterministas siguen funcionando sin ella)

Mientras no exista una periodicidad acordada, el workflow se inicia manualmente desde la pestaña
Actions gracias a `workflow_dispatch`. Cuando se defina el período, habilita el bloque `schedule`
comentado y reemplaza `...` por la expresión cron elegida; el bloque preparado conserva la zona
horaria `America/Santiago`. La configuración evita dos sincronizaciones simultáneas.

## Seguridad y operación

- Nunca agregues la clave secreta al repositorio, al frontend ni a los logs.
- `scrape_runs` permite revisar ejecuciones fallidas o incompletas.
- Una carga se divide en lotes configurables mediante `SUPABASE_BATCH_SIZE`.
- Fechas como `"por confirmar"` hacen que el evento se descarte antes de publicar.
- Los lotes se guardan primero en staging. Una falla al subirlos no altera el catálogo público y una
  falla dentro de `publish_staged_catalog` revierte toda la publicación.
- La migración debe aplicarse una vez antes de la primera sincronización de datos.
