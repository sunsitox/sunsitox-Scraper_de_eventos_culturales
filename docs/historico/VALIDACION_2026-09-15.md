# Validación local — 15 de septiembre de 2026

## Resultado comprobado

- 114 pruebas automatizadas aprobadas (`python -m unittest discover -s tests -q`). Los mensajes de error de fuentes ficticias son escenarios deliberados de las pruebas.
- Dependencias compatibles (`python -m pip check`) y compilación de módulos sin errores.
- SQLite: `PRAGMA integrity_check` devuelve `ok`. Se ejecutó `tools/checkpoint_state.py` con la extracción activa y nuevamente después de las pruebas de IA.
- El YAML de GitHub Actions se pudo interpretar; contiene las dos fases y ambos respaldos. Esto no equivale a haber ejecutado un runner de GitHub.
- Supabase: consulta real de deduplicación y construcción local del paquete de dos eventos. No se ejecutaron migraciones, inserciones, actualizaciones ni eliminaciones remotas.

## Pruebas reales de IA y reutilización

| Prueba | Resultado |
| --- | --- |
| Primera muestra: dos eventos | Dos OCR y dos redacciones correctas; tres solicitudes NVIDIA; 26,38 segundos incluyendo consulta remota y salida local |
| Misma muestra en un proceso nuevo | Cero solicitudes NVIDIA; resultados recuperados del estado persistente |
| Santiago Cultura: primeras 50 fichas ya guardadas | 50 reutilizadas; una solicitud al índice; 1,62 segundos |
| Ticketplus: primera extracción | 272 eventos; 273 solicitudes HTTP; 558,40 segundos |
| Ticketplus: repetición inmediata | Los mismos 272 eventos; una solicitud a la cartelera; 2,52 segundos |

Estos tiempos corresponden a esta ejecución y a cachés recientes. No predicen la duración total en GitHub Actions ni eliminan las revisiones periódicas de fichas.

## Corrección encontrada durante la validación

Rancagua Cultura enlaza descargas de calendario mediante `?ical=1` y `?outlook-ical=1`, además de enlaces `webcal://`. Las descargas no son fichas HTML. Se excluyeron las variantes HTTP y los archivos `.ics` de los candidatos del conector genérico.

Una ficha vacía ahora deja la fuente como parcial y permite conservar otras fichas procesadas, en lugar de abortar la fuente completa. Las fuentes parciales no autorizan limpieza por ausencia en Supabase.

La repetición real de Rancagua terminó correctamente: seis eventos consolidados, estado `succeeded`. Se añadieron pruebas de regresión para los enlaces de calendario y las fichas vacías.

## Estado de la extracción completa al redactar este informe

Se inició `python scrape_events.py --phase extract --local-only` sobre las 19 fuentes habilitadas. Hay 18 checkpoints completos, incluyendo la repetición corregida de Rancagua. Santiago Cultura continúa construyendo su primera caché de fichas históricas. Una fuente completada puede devolver cero eventos vigentes: no implica por sí solo un fallo.

La ejecución completa comenzó antes de corregir Rancagua, por lo que su resumen en memoria conserva el fallo inicial. Cuando finalice, debe repetirse la fase de extracción: recuperará los checkpoints completos, incluido el de Rancagua corregido, y reconstruirá el conjunto final compatible.

No se ha ejecutado el enriquecimiento del catálogo completo ni una sincronización remota en esta validación. La prueba de IA fue deliberadamente de dos eventos.

## Evidencia local

En `work/validation-checkpoints-20260915/` se conservan:

- Copia de los archivos de datos anteriores a la validación.
- `extraction.log`, `tests.log` y registros de las repeticiones de Rancagua.
- `ai-validation-first.json`, `ai-validation.json` y los dos eventos de muestra en `ai-sample/`.
- `incremental-validation.json` con las mediciones de reutilización.
- Dos respaldos consistentes de SQLite.

La carpeta de pruebas está excluida de Git. No contiene copias del archivo `.env`.
