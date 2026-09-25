# MVP · agregador de eventos culturales de Chile

El recolector tiene **21 fuentes permanentes y una fuente estacional de septiembre** configuradas mediante una arquitectura modular de conectores. Una fuente solo aparece en la aplicación cuando aporta al menos un evento vigente:

| Fuente | Región principal | Método |
|---|---|---|
| Chile Cultura | Las 16 regiones de Chile | API nacional |
| Maipú en Común | Metropolitana | Playwright |
| Centro Cultural GAM | Metropolitana | JSON-LD |
| Centro Cultural La Moneda | Metropolitana | Tarjetas HTML declarativas |
| Centro Cultural CEINA | Metropolitana | Tarjetas HTML declarativas con fechas normalizadas |
| Valpo Cultura | Valparaíso | The Events Calendar API |
| Espacio Cultural Viña del Mar | Valparaíso | The Events Calendar API |
| Teatro Municipal de Viña del Mar | Valparaíso | JSON-LD |
| Agenda Cultural UOH | O'Higgins | WordPress REST declarativo |
| Teatro Biobío | Biobío | WordPress REST + fechas visibles |
| Agenda Universidad de Talca | Maule | Tarjetas HTML declarativas |
| Corporación Cultural de Iquique | Tarapacá | Tarjetas institucionales declarativas |
| Centro de Arte Molino Machmar | Los Lagos | Tarjetas institucionales declarativas |
| Enoturismo Chile · Agenda | Cobertura interregional | WordPress + EventON + taxonomías vinícolas |
| FISA · Ferias y Exposiciones | Varias regiones | Portafolio oficial FISA |
| Espacio Riesco · Calendario | Metropolitana | Tarjetas HTML declarativas |
| Parque Cultural de Valparaíso | Valparaíso | WordPress REST declarativo |
| Fundación CorpArtes | Metropolitana | Tarjetas HTML declarativas |
| Cultura Vitacura | Metropolitana | Tarjetas HTML declarativas |
| FondasChile · Septiembre | Cobertura nacional | Tarjetas HTML declarativas, activas solo en septiembre |
| Santiago Cultura | Metropolitana | WordPress/EventON; detalles incrementales por modificación |
| Ticketplus · Chile | Las 16 regiones de Chile | Selector regional público + fichas estructuradas; sin NVIDIA |

Santiago Cultura y Ticketplus quedan activas con detalles incrementales y checkpoints.
Santiago revisa el índice completo y reutiliza detalles sin cambios; Ticketplus descubre
los enlaces de su cartelera y revalida detalles cada 24 horas. La primera extracción puede
seguir siendo larga. Ambas pasan por el filtro cultural común.

## Pipeline reanudable

Cada fuente y resultado de IA se guarda en `state/pipeline.sqlite3`. Las tareas OCR pendientes
se recuperan en futuras ejecuciones; los resultados terminados se reutilizan por versión de entrada.
El comando habitual sigue funcionando y permite además separar fases:

```powershell
python scrape_events.py --phase extract --local-only
python scrape_events.py --phase process --supabase
```

El workflow restaura y conserva este estado mediante artefactos. El detalle de checkpoints,
cola de IA, límites y recuperación está consolidado en la
[documentación técnica del scraper](docs/DOCUMENTACION_SCRAPER.pdf).

## Instalación

### Opción automática en Windows

Haz doble clic en `instalar.bat`. El archivo:

1. Crea `venv` solamente si todavía no existe.
2. Actualiza `pip` e instala `requirements.txt`.
3. Instala Chromium dentro de `.playwright-browsers` para Maipú.
4. Crea `.env` desde `.env.example` si falta, sin sobrescribir configuraciones existentes.

Después puedes hacer doble clic en `ejecutar.bat` para iniciar la extracción completa. Si el entorno virtual no existe, este segundo archivo llama automáticamente al instalador.

Desde una consola también pueden ejecutarse sin la pausa final:

```powershell
instalar.bat --no-pause
ejecutar.bat --no-pause
```

### Opción manual

Dentro del entorno virtual:

```powershell
python -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH="$PWD\.playwright-browsers"
python -m playwright install chromium
```

Las dos últimas instrucciones instalan, dentro de la carpeta del proyecto, el navegador que necesita únicamente el conector de Maipú.

Después ejecuta:

```powershell
python scrape_events.py
```

Los resultados consolidados quedan en `data/events.json` y `data/events.csv`. También se crean archivos JSON y CSV separados dentro de `data/by_region/`. `data/source_metrics.json` registra por fuente la duración, eventos obtenidos, solicitudes HTTP y aciertos de caché.

Al terminar, la consola muestra un único resumen operativo con el total consolidado y la duración
completa, por ejemplo: `RESUMEN FINAL | TOTAL EVENTOS: 399 | TIEMPO TOTAL: 00:12:34`. El reloj
incluye extracción, normalización, exportación local y, si corresponde, sincronización con
Supabase. La última medición también queda en `data/run_summary.json`.

Opcionalmente, el mismo resultado puede sincronizarse directamente con Supabase mediante una
carga idempotente. El esquema, la migración, las variables y GitHub Actions se explican en la
[documentación técnica del scraper](docs/DOCUMENTACION_SCRAPER.pdf). El diccionario completo de tablas, columnas y categorías se
encuentra en [`docs/DATABASE.md`](docs/DATABASE.md).

El inventario y la justificación de las agendas consultadas están documentados en
[`docs/SOURCES.md`](docs/SOURCES.md).
Las reglas comunes de formato para documentos y diagramas están en
[`docs/ESTANDAR_DOCUMENTACION.md`](docs/ESTANDAR_DOCUMENTACION.md).

Para crear o actualizar las tablas desde Windows, ejecuta `migrar_supabase.bat`. El identificador
del proyecto se obtiene automáticamente desde `SUPABASE_URL` en `.env`.

`instalar.bat` instala las dependencias del cliente que carga eventos mediante la API y verifica
la configuración de Supabase en `.env`. La carga normal no requiere la CLI: completa
`SUPABASE_URL`, `SUPABASE_SECRET_KEY` y `SUPABASE_ENABLED=true`. La CLI se necesita solamente
para aplicar las migraciones con `migrar_supabase.bat`; este archivo usa `npx` automáticamente si
no encuentra una instalación global.

El campo `categories` siempre es una lista y admite múltiples valores por evento. En JSON se guarda como un arreglo real:

```json
"categories": ["Música", "Teatro", "Familiar"]
```

En CSV se conserva como un arreglo JSON dentro de la celda: `["Música", "Teatro", "Familiar"]`.
En Supabase se normaliza mediante `categories` y `event_categories`, conservando la relación
muchos-a-muchos sin perder ninguna categoría.

Enoturismo Chile agrega automáticamente la taxonomía publicada por la fuente, por ejemplo
`["Vino", "Enoturismo", "Fiestas de la Vendimia"]`. FISA y Espacio Riesco añaden categorías
temáticas como salud, minería, construcción, gastronomía, tecnología o industria naval.

El alcance predeterminado incluye las 16 regiones de Chile. Chile Cultura entrega la cobertura
nacional y las demás fuentes activas agregan profundidad local o temática. Ticketplus se mantiene
como fuente complementaria y sus resultados pasan por el mismo filtro cultural global.

La configuración predeterminada recorre todas las páginas. Hasta cuatro dominios independientes se procesan en paralelo, pero las fuentes de un mismo dominio mantienen su orden y pausa para no multiplicar la presión sobre el sitio. Maipú consulta las páginas de la API que usa su propia aplicación dentro de una única ventana de Playwright; no abre una ventana por ficha. Una capa final común sanea HTML, CSS, scripts y shortcodes y descarta eventos vencidos aunque un conector nuevo omita hacerlo.

## Arquitectura

`scrape_events.py` es ahora un punto de entrada pequeño. El código se distribuye dentro de `eventos/` por responsabilidad: conectores, extractores, mapeo declarativo, saneamiento, servicio NVIDIA, cliente HTTP, configuración, consolidación y exportadores.

La raíz conserva solamente los comandos cotidianos. La documentación vive en `docs/`, las utilidades de mantenimiento en `tools/`, las fuentes declarativas en `sources/`, las migraciones en `supabase/` y las pruebas en `tests/`.

Se aplican Strategy para los conectores, Registry/Factory para seleccionarlos desde los JSON, Repository para cargar fuentes e inyección de dependencias para probar el pipeline. La explicación completa está en la [documentación técnica del scraper](docs/DOCUMENTACION_SCRAPER.pdf).

## NVIDIA NIM

La IA no se usa cuando la fuente ofrece API, microdatos, tarjetas configuradas o JSON-LD. Por eso GAM, Teatro Biobío y Teatro Municipal de Viña se procesan sin consumir NVIDIA. NIM queda como respaldo de las fuentes genéricas que no publican una estructura estándar. Para utilizarlo, copia `.env.example` como `.env` e incorpora tu clave.

El modelo predeterminado es `meta/llama-3.2-11b-vision-instruct`, verificado con la cuenta tanto para texto como para afiches. La solicitud exige un objeto JSON, desactiva el razonamiento interno para que no consuma el límite antes de entregar los eventos y valida la respuesta antes de importarla. `NVIDIA_MAX_TOKENS` y `NVIDIA_INPUT_CHARS` permiten ajustar los límites. Si NVIDIA responde `401`, `403`, `404` o `410`, revisa que el modelo continúe habilitado para la cuenta. El programa desactiva el respaldo IA después de un error permanente para no repetir llamadas fallidas; las fuentes deterministas continúan normalmente.

Todas las llamadas comparten un limitador global configurado en `NVIDIA_RPM_LIMIT=40`. El intervalo
mínimo efectivo es de 1,55 segundos, por lo que extracción y normalización no pueden superar en
conjunto las 40 solicitudes por minuto. Las ubicaciones incompletas se envían en lotes de 20 y con
un máximo predeterminado de cinco lotes por ejecución; estos valores se controlan con
`NVIDIA_LOCATION_BATCH_SIZE` y `NVIDIA_LOCATION_MAX_BATCHES`.

Después de deduplicar, el pipeline consulta Supabase por URL original y por la clave combinada de
título, fecha y lugar. Los eventos ya conocidos reutilizan su OCR y descripción editorial; los
nuevos, los que tienen una redacción pendiente y aquellos cuyo texto de origen cambió vuelven a
procesarse. El subconjunto resuelto por Supabase se conserva hasta OCR, redacción y ubicación IA;
los eventos sin cambios no vuelven a entrar a esas etapas. La existencia de una fila de procedencia no se considera por sí sola una redacción
exitosa. El texto original y el OCR se
guardan en `event_provenance`, que no tiene lectura pública. La redacción automática mantiene hechos
y atribución, pero no garantiza por sí sola la aprobación de Google AdSense ni reemplaza la revisión
de derechos de autor y calidad editorial.

Los errores transitorios de NVIDIA tienen reintentos acotados. Se aplica espera global cuando
el proveedor publica `Retry-After`, responde 429 o se acumulan varios timeouts consecutivos.
No hay pausas fijas entre lotes. Los pendientes se guardan para siguientes ejecuciones.
`NVIDIA_RETRY_MAX_ELAPSED_SECONDS` limita cada operación y sus reintentos.

La concurrencia de fuentes no aumenta la frecuencia de NVIDIA: el inicio de sus solicitudes pasa
por un único limitador global. `SOURCE_WORKERS=4` controla cuántos dominios pueden trabajar simultáneamente y
`HTTP_CACHE_ENABLED=true` activa la revalidación con ETag/Last-Modified. La caché no evita consultar
la fuente; reutiliza el cuerpo anterior únicamente cuando el propio servidor confirma `304 Not
Modified`.

El límite se coordina dentro de una ejecución. GitHub Actions ya impide dos workflows simultáneos,
pero una ejecución local y otra en GitHub no comparten memoria; si ambas usan la misma clave al
mismo tiempo, el límite de la cuenta podría alcanzarse y NVIDIA respondería `429`. El cliente
respeta `Retry-After` y realiza un único reintento predeterminado.

## Normalización de ubicaciones

Cada evento termina con `location`, `location_precision` y `location_source`. La ubicación se
resuelve en este orden: dato publicado, valor asumido explícitamente por la fuente, reglas
deterministas, enriquecimiento NVIDIA por lotes y fallback geográfico. Nunca se inventa una
dirección para cumplir el requisito: si solo se conoce la comuna o la región, se conserva ese nivel
y queda señalado en `location_precision`.

Los JSON de fuente admiten además `postal_code`, `latitude` y `longitude` dentro de
`location_defaults`. APIs, JSON-LD y tarjetas declarativas también pueden mapear esos campos.
Para fuentes con varios recintos, `location_metadata` relaciona nombres conocidos con su dirección
y coordenadas verificadas; también puede activarse cuando el nombre aparece en el OCR del afiche.

El organizador se conserva en cada evento y se normaliza en `organizers`. Marcar
`organizers.is_blocked=true` oculta de inmediato todos sus eventos mediante RLS y evita que futuras
corridas gasten OCR o redacción en ellos. El procedimiento está en la [documentación técnica del scraper](docs/DOCUMENTACION_SCRAPER.pdf).

Una fuente que siempre usa el mismo recinto puede declararlo sin modificar Python:

```json
"location_defaults": {
  "venue": "Teatro Municipal",
  "address": "Dirección opcional"
}
```

## Agregar una nueva fuente

La guía completa de incorporación, pruebas y recuperación ante cambios o caídas
se encuentra junto a las configuraciones en
[`sources/GUIA_AGREGAR_Y_MANTENER_FUENTES.md`](sources/GUIA_AGREGAR_Y_MANTENER_FUENTES.md).

La forma más sencilla es ejecutar:

```powershell
python add_source.py
```

El asistente pregunta nombre, URL, región, comuna, organizador y, opcionalmente, un recinto o
dirección asumidos para toda la fuente. Después crea automáticamente un archivo dentro de
`sources/`. No hace falta modificar `scrape_events.py`.

También permite indicar una o más categorías predeterminadas separadas por punto y coma, por ejemplo `Cultura;Familiar;Gratuito`.

También puedes copiar `sources/_template.json`, cambiarle el nombre y editar sus valores. Los archivos cuyo nombre comienza con `_` se consideran plantillas y no se ejecutan.

El asistente permite elegir el tipo de fuente y solicita solamente las rutas o selectores necesarios. También hay ejemplos copiables en `sources/templates/`.

El conector `generic` intenta primero extraer JSON-LD/Schema.org y, si no encuentra eventos estructurados y existe una clave NVIDIA, usa NIM como respaldo. `json_api` permite incorporar una API paginada configurando `results_path`, parámetros de paginación y `field_map`, sin crear una clase Python. También existen `html_cards`, `tribe_events_api`, `wordpress_rest`, `eventon_wordpress`, `fisa_portfolio`, `chilecultura_api` y `maipu_browser`.

Para una API WordPress, `wordpress_rest` permite mapear campos desde el propio JSON de la fuente, sin editar Python. Consulta la [documentación técnica del scraper](docs/DOCUMENTACION_SCRAPER.pdf) para ver la arquitectura y los ejemplos.

`html_cards` permite hacer lo mismo con páginas HTML estáticas: `card_selector` identifica cada tarjeta y `selectors` mapea título, fechas, lugar, imagen y enlace mediante CSS.

## Ajustes rápidos

Cada fuente se controla desde su propio archivo en `sources/`:

- `max_results`: `0` significa sin límite para Chile Cultura y Santiago Cultura.
- `max_pages`: limita cuántas fichas descubre una fuente genérica; el valor predeterminado del asistente es `30`.
- `max_pages`: `0` hace que Maipú detecte y recorra automáticamente todas sus páginas.
- `category_filter`: está vacío para incluir todas las actividades municipales de Maipú. Puedes escribir `Cultura` si después quieres restringirlas.
- `headless`: Maipú usa `false`, por lo que abre temporalmente una ventana de navegador; su protección bloquea navegadores invisibles.
- `browser_request_interval`: pausa mínima entre páginas de la API interna de Maipú.
- `description_max_chars`: límite opcional de descripción para una fuente concreta.
- `enabled`: permite activar o desactivar una fuente.

Los ajustes globales de rendimiento están en `.env`: `SOURCE_WORKERS` admite de 1 a 8 (se
recomienda 4) y `HTTP_CACHE_ENABLED` permite apagar la caché condicional. Aumentar trabajadores no
elimina las pausas por dominio ni modifica el límite de 40 RPM de NVIDIA.

`EVENT_DESCRIPTION_MAX_CHARS=8000` evita conservar cuerpos HTML desproporcionados. En Supabase,
`SUPABASE_DELETE_EXPIRED=true` elimina en cada sincronización los eventos cuyo término ya pasó; si
no existe `end_at`, utiliza `start_at`. Las relaciones de categorías e imágenes se eliminan en
cascada. Los registros sin una fecha inicial válida se descartan antes de exportar o publicar. Las
fechas sin offset publicado se interpretan en `America/Santiago`.

Las regiones se controlan en `settings.json`. Puedes agregar otra región copiando exactamente el nombre que entrega Chile Cultura. `include_unknown_region` determina si se conservan fichas que no informan región.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Las pruebas validan el catálogo mínimo de diez fuentes activas, el registro de conectores, el
mapeo WordPress, JSON-LD, la deduplicación, el filtro regional y la transformación idempotente
del conjunto al esquema de Supabase. También validan la caché condicional, la lectura directa de
Maipú, el contador, el temporizador y los resúmenes operativos.

## Auditar el inventario de fuentes

El proyecto incluye un segundo pipeline independiente que no extrae eventos ni modifica
Supabase. `validar_fuentes.bat` revisa las 390 entradas del inventario: las fuentes digitales se
comprueban mediante HTTP, `robots.txt`, estructura de eventos, fechas, enlaces, relevancia cultural
y cobertura chilena; las fichas territoriales sin agenda quedan como `no_aplica`.

```powershell
validar_fuentes.bat
```

La auditoría completa, el catálogo aprobado y la cola de revisión se generan en
`data/source_validation/`. Las reglas y parámetros están resumidos en la
[documentación técnica del scraper](docs/DOCUMENTACION_SCRAPER.pdf).

## Consideraciones

El programa aplica una pausa entre solicitudes y no intenta acceder a información privada. Antes de convertirlo en un servicio comercial o programarlo para ejecución frecuente, revisa los términos de uso, `robots.txt` y políticas de reutilización de cada fuente.
