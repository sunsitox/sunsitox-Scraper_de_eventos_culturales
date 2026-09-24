# Extracción reanudable y enriquecimiento pendiente

El comando habitual conserva compatibilidad: `python scrape_events.py --supabase`.
También hay dos fases independientes:

```powershell
python scrape_events.py --phase extract --local-only
python scrape_events.py --phase process --supabase
```

La primera guarda los resultados de la recolección. La segunda carga ese snapshot,
sanea y deduplica, consulta las exclusiones y coincidencias de Supabase, procesa la IA
pendiente y publica. Para procesar sin publicar se usa `--phase process --local-only`.
No se ha programado ninguna periodicidad: el workflow conserva `workflow_dispatch`.

## Qué se conserva

El estado reside en `state/pipeline.sqlite3`. SQLite forma parte de Python y no requiere
un servidor ni una migración de Supabase. No se deben versionar `state/` ni `state-export/`.
Las escrituras se confirman mediante transacciones independientes; una excepción posterior
no revierte las fichas ni las respuestas de IA ya guardadas.

| Punto | Estado guardado | Recuperación |
|---|---|---|
| Fuente recolectada | Lista cruda y configuración | Puede retomarse la validación pendiente |
| Fuente terminada | Eventos saneados y validados, ID de ciclo | Se reutiliza dentro de una corrida interrumpida |
| Detalle incremental | Eventos parseados, URL, revisión y fecha de consulta | Se reutiliza incluso si la fuente no terminó |
| Afiches | Entrada, estado y texto OCR | Se reintentan únicamente tareas pendientes |
| Redacción | Entradas factuales y respuesta por evento del lote | No se repiten resultados ya guardados |
| Ubicación IA | Entrada y campos respaldados por la respuesta | Se recuperan las ubicaciones ya procesadas |

Los estados de IA son `pending`, `processing`, `completed` y `retryable_error`.
Una tarea `processing` dejada por una interrupción vuelve a estar disponible en el
siguiente proceso. Una respuesta OCR válida con texto vacío se guarda como completada;
no se reintenta indefinidamente un afiche sin texto. El límite de 100 imágenes deja el
resto en `pending`. Los fallos de credenciales siguen suspendiendo llamadas durante
esa ejecución; hay que corregirlas para que puedan completarse los pendientes.

La cola se cruza con los eventos de la extracción elegida y las exclusiones remotas.
No vuelve a publicar automáticamente eventos vencidos, desaparecidos o suprimidos solo
porque una tarea antigua permanezca en la cola. Su historial se depura tras 90 días sin
actualizaciones. Las fichas terminadas pueden retomarse durante 24 horas por defecto;
tras ese plazo empieza una nueva recolección, conservando las cachés de detalles e IA.

`--phase process` permite volver a procesar el snapshot más reciente durante ese plazo,
también si su publicación anterior terminó. Cambiar la configuración de fuentes o el
mes exige una nueva extracción. Una corrida terminada seguida del comando habitual
inicia una nueva revisión; una corrida con fuentes fallidas retoma las que faltan.

## Deduplicación y cambios

La deduplicación global precede al OCR y al parafraseo. Compara la clave de título,
fecha y lugar, y la URL cuando también coinciden título y fecha/hora. Una URL de cartelera
puede representar varias actividades: compartir URL por sí solo no las convierte en duplicadas.
Si lugar y comuna están vacíos, la fuente y la URL forman parte de la identidad para no fusionar
eventos homónimos de procedencias distintas.
Luego se resuelven los identificadores existentes en Supabase y se vuelven a consolidar.

Cada operación de IA tiene una versión de entrada. La redacción y ubicación incluyen
los datos factuales y el texto fuente; cambiar fecha, lugar, precio, organizador o
descripción invalida el resultado correspondiente. Los resultados terminados se reutilizan
desde el estado local o desde Supabase. OCR usa la URL de imagen y el modelo como identidad.
Cambiar la URL invalida el OCR. **No es un hash de los bytes de la imagen**: si el proveedor
reemplaza un afiche manteniendo exactamente la URL, la relectura se difiere hasta
`OCR_CACHE_DAYS` (30 días). La caché se identifica además por versión del formato de entrada.

No se cobran llamadas nuevas para reutilizar un checkpoint. Puede repetirse una llamada
si el proceso muere después de que NVIDIA responde pero antes de confirmar el resultado
en SQLite; no hay una transacción compartida con el proveedor que elimine esa ventana.

## Pausas NVIDIA

- Todas las operaciones comparten el límite de hasta 40 inicios por minuto.
- No hay descansos fijos entre fases ni entre lotes.
- `Retry-After` admite segundos y fecha HTTP; se respeta completo, aun cuando exceda
  las antiguas variables `NVIDIA_*_RETRY_MAX_WAIT_SECONDS`, mantenidas por compatibilidad.
- Un 429 sin cabecera aplica una espera conservadora de 60 segundos.
- Tras tres timeouts/conexiones fallidas consecutivos se aplica una pausa de 60 segundos;
  una respuesta HTTP reinicia ese contador. Ambos valores son configurables.
- El último intento también registra la pausa. Si esta excede el presupuesto total de
  una operación, se deja pendiente; nunca se reintenta antes de lo pedido por el servidor.
- La fecha mínima de nueva llamada se guarda en SQLite para respetarla al reanudar.

## Santiago Cultura

Se consulta el índice WordPress completo, paginado, solicitando `id`, `link`, `title`
y `modified_gmt`. Se procesan los registros a medida que llegan y se guarda cada ficha.
Una ficha con la misma revisión se reutiliza durante siete días. Al cambiar `modified_gmt`
se abre de inmediato; sin revisión utilizable se aplica el TTL de 24 horas.
Cada ficha se vuelve a descargar al alcanzar `full_audit_days`, aunque WordPress no indique
cambios. Esto detecta cambios de plantillas o metadatos que no actualicen `modified_gmt`.

Se mantiene el recorrido del índice para descubrir publicaciones antiguas modificadas y
evitar depender de un orden o filtro que el servidor pudiera ignorar. **La optimización
ahorra principalmente las descargas de detalle**, no elimina las solicitudes del índice.
La primera ejecución aún necesita recorrer las fichas; las posteriores reutilizan ese trabajo.
Los eventos vencidos se descartan también al leer detalles desde caché.

## Ticketplus

El selector público de Ticketplus aporta las 16 carteleras regionales válidas en cada corrida.
Se recorren secuencialmente y se descubren todos sus enlaces `/events/` (`max_pages: 0`). Las
URLs nuevas se abren inmediatamente; los detalles conocidos se reutilizan durante
`detail_refresh_hours` (24 horas). Una región sin eventos es válida. Una interrupción conserva
las fichas ya procesadas en SQLite y la próxima ejecución vuelve a consultar los índices, pero
no vuelve a descargar sus fichas vigentes sin cambios.

Un error transitorio o ficha sin la estructura esperada marca la fuente como `partial`;
los resultados válidos se conservan y sus detalles ya están guardados. Un 404/410 confirmado
se registra como ficha sin eventos. Una fuente parcial o fallida **no autoriza eliminación
de filas ausentes** en Supabase. Una fuente completa sin programación se marca `complete_empty`
y sí puede retirar el snapshot anterior. La limpieza por vencimiento conserva sus reglas existentes.

## GitHub Actions y durabilidad

El workflow separa extracción y procesamiento en pasos dentro de un job, conserva la
exclusión de ejecuciones simultáneas y sigue sin cron. Al comenzar restaura el artefacto
de estado más reciente disponible de la misma rama y workflow (hasta 100 corridas recientes).
Después de la extracción y del procesamiento sube una copia SQLite consistente, incluso
si un paso falla. Los artefactos se conservan 30 días y requieren `actions: read` para
recuperarse. El estado incluye contenido fuente y editorial; sus permisos de acceso son
los del repositorio/artefacto. La caché HTTP es una optimización separada, no el checkpoint.

La extracción tiene un límite de paso de 240 minutos y el procesamiento 80 minutos;
el job dispone de 350 minutos para dejar margen a instalación y copias. Esto permite
que un timeout de **paso** aún dé oportunidad de guardar el estado. Una cancelación forzada,
la pérdida de la VM o alcanzar el límite del job puede impedir subir la última copia:
en ese caso se recupera el último artefacto ya subido, no cada commit local posterior.
Si se requiere sobrevivir sin esa ventana, hace falta almacenamiento remoto durante la
ejecución. La implementación actual no promete esa durabilidad ni escribe checkpoints en Supabase.

No ejecutar simultáneamente dos procesos sobre el mismo estado. La concurrencia del
workflow protege Actions; una ejecución local separada debe evitar solaparse con él si
comparte credenciales. Para copiar estado local hacia otro equipo con consistencia:

```powershell
python tools/checkpoint_state.py
```

La copia queda en `state-export/pipeline.sqlite3`. Este comando no ejecuta scraping ni IA.
La documentación de GitHub describe la persistencia y recuperación entre corridas en
[artefactos de workflow](https://docs.github.com/en/actions/tutorials/store-and-share-data).
