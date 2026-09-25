# Auditoría técnica del scraper de eventos

**Fecha:** 20 de septiembre de 2026  
**Alcance:** extracción, checkpoints, enriquecimiento con IA/OCR, publicación en Supabase, tolerancia a fallos y GitHub Actions.

> **Actualización del 21 de septiembre de 2026:** ya se implementaron la publicación transaccional
> mediante staging, el estado `complete_empty`, el contrato obligatorio de fecha, la zona horaria
> chilena, la deduplicación reforzada y la selección estricta de candidatos para IA. Los hallazgos
> originales se conservan abajo como registro histórico de la auditoría.

## Resumen ejecutivo

El proyecto ya tiene una base adecuada para un Capstone y para crecer hacia 20–30 fuentes: conectores separados, fuentes declarativas en JSON, límites por dominio, checkpoints SQLite, deduplicación previa a IA, reintentos de NVIDIA y conciliación selectiva con Supabase. La incorporación de una fuente nueva no exige modificar el pipeline central cuando la página calza con un conector existente.

La auditoría no detectó un impedimento para seguir operando, pero sí cuatro mejoras prioritarias antes de automatizarlo de forma desatendida: publicación transaccional en Supabase, tratamiento seguro de fuentes que retornan cero eventos, persistencia de checkpoints durante una ejecución larga de Actions y controles centrales para desactivar fuentes o contenidos.

## Resultado de esta revisión

- Se incorporaron **Corporación Cultural de Iquique** y **Centro de Arte Molino Machmar**, fuentes institucionales de Tarapacá y Los Lagos.
- El conector detectó dos actividades vigentes en la comprobación del 19 de septiembre de 2026.
- Se amplió el analizador de fechas para comprender intervalos como `24 y 25 de septiembre`.
- Se agregó limpieza declarativa del prefijo de fecha en títulos, sin código exclusivo para Iquique.
- Se aisló cada región de Ticketplus: si una cartelera regional falla, las demás continúan y la fuente queda marcada como incompleta para impedir una conciliación destructiva.
- La suite automatizada quedó en **121 pruebas superadas**.

## Fortalezas actuales

1. **Arquitectura extensible.** El registro de conectores y los archivos JSON separan las reglas de cada fuente del pipeline.
2. **Tolerancia a fallos por fuente.** Un fallo no detiene la recolección completa y las ejecuciones incompletas no deben borrar datos válidos.
3. **Ahorro de IA.** La deduplicación, el hash de contenido y las colas persistentes evitan repetir OCR o parafraseo sin necesidad.
4. **Control de carga.** Hay concurrencia limitada por dominio y respeto de pausas/reintentos para no presionar las páginas ni NVIDIA.
5. **Trazabilidad.** Supabase conserva procedencia, ejecución y estado editorial de los eventos.
6. **Cobertura de pruebas.** Existen pruebas de conectores, fechas, filtros culturales, ubicaciones, disponibilidad y sincronización.

## Hallazgos prioritarios

### P1 — Publicación de Supabase sin una transacción única

La exportación hace varias solicitudes REST consecutivas: fuentes, organizadores, comunas, categorías, eventos, procedencia, relaciones y medios. Si una llamada intermedia falla, una parte del catálogo puede quedar actualizada y otra no, aunque la ejecución termine marcada como fallida.

**Riesgo:** inconsistencia temporal entre tablas y relaciones faltantes.  
**Recomendación:** publicar mediante una función PostgreSQL RPC transaccional o usar tablas de staging asociadas a `run_id` y activar el lote completo al final.

### P1 — Una fuente válida con cero eventos no elimina sus registros antiguos

Actualmente solo se concilian en Supabase las fuentes exitosas que retornan al menos un evento. Esto protege contra selectores rotos, pero también impide limpiar los eventos de una fuente que legítimamente quedó sin programación vigente.

**Riesgo:** eventos cancelados o retirados pueden permanecer publicados hasta que venza su fecha.  
**Recomendación:** distinguir explícitamente tres estados: `complete`, `complete_empty` y `partial/error`. Un `complete_empty` validado debe poder conciliar la fuente a cero; un resultado sospechoso debe quedar en cuarentena sin borrar.

### P1 — El checkpoint de GitHub Actions no es durable durante la extracción

El estado se sube como artefacto después del paso de extracción. Si el runner se cancela, supera el tiempo máximo o se pierde antes de llegar a ese paso, los commits intermedios del SQLite solo existen en el runner temporal.

**Riesgo:** repetir horas de trabajo en una ejecución posterior.  
**Recomendación:** dividir la recolección por grupos de fuentes/regiones y subir un artefacto tras cada grupo, o guardar el estado incremental en un bucket privado de Supabase. No se recomienda escribir checkpoints de ejecución al repositorio.

### P1 — Desactivación de fuentes todavía dependiente de archivos locales

`enabled` en los JSON permite apagar una fuente antes del despliegue, pero la base no es aún la autoridad central para solicitudes de exclusión. Tampoco se reconcilian como inactivas las fuentes retiradas o renombradas que ya existen en Supabase.

**Riesgo:** una fuente eliminada del repositorio puede seguir figurando activa; atender una solicitud de exclusión exige editar y desplegar código/configuración.  
**Recomendación:** añadir `ingestion_enabled`, `publication_enabled`, motivo, fecha y auditoría en `sources`; consultar esas restricciones antes de acceder a la página y antes de publicar. Mantener controles equivalentes por organizador y evento.

## Hallazgos de prioridad media

### P2 — Identidad de fuente derivada del nombre visible

Renombrar una fuente puede generar una identidad nueva y dejar atrás la anterior. Conviene que cada JSON tenga un `source_key` estable e inmutable, separado del nombre mostrado.

### P2 — Salud de la URL oficial no se comprueba por separado

La verificación de disponibilidad prioriza la URL de procedencia. Cuando un evento tiene además una web oficial externa, esa segunda URL puede estar caída sin quedar registrada, como en el caso observado de Librería L.

**Recomendación:** medir `source_url_status` y `official_url_status` por separado, con fecha del último chequeo. Una caída temporal de la URL oficial no debería borrar automáticamente un evento cuyo origen y fecha siguen siendo válidos.

### P2 — Reintentos HTTP de las fuentes

Los fallos HTTP transitorios de páginas externas no tienen una política común tan completa como NVIDIA.

**Recomendación:** centralizar reintentos acotados para `429`, `502`, `503` y `504`, respetar `Retry-After`, aplicar espera exponencial con jitter y conservar límites por dominio. Los `404/410` no deben reintentarse repetidamente.

### P2 — Archivos de salida no atómicos

JSON y CSV se escriben directamente. Una interrupción puede dejar un archivo truncado y las carpetas regionales pueden conservar archivos obsoletos.

**Recomendación:** escribir en un archivo temporal, reemplazarlo atómicamente y publicar un manifiesto de la ejecución; limpiar únicamente salidas que el manifiesto anterior identifique como generadas por el programa.

### P2 — Contrato de configuración débil

Los JSON validan campos básicos, pero una opción mal escrita puede pasar inadvertida hasta ejecutar la fuente.

**Recomendación:** incorporar JSON Schema o modelos tipados por conector y validarlos en pruebas y en el workflow. Debe detectar selectores ausentes, opciones desconocidas, regiones no canónicas y combinaciones incompatibles.

### P2 — Monitoreo preventivo de cambios de estructura

Las pruebas locales cubren la lógica, pero no garantizan que las páginas reales conserven sus selectores.

**Recomendación:** ejecutar diariamente una sonda liviana que descargue solo el índice de cada fuente, compruebe su contrato y genere un reporte. La recolección completa puede seguir una frecuencia menor.

## Mejoras de prioridad baja

- Limpiar las marcas de invalidación de la cola de enriquecimiento después de un procesamiento exitoso para evitar trabajo y escrituras innecesarias.
- Probar explícitamente con la misma versión de Python que GitHub Actions para reducir diferencias entre el equipo local y CI.
- Añadir métricas históricas por fuente: duración, fichas descubiertas, eventos válidos, porcentaje descartado, errores HTTP, uso de caché y llamadas a IA.
- Separar el límite de resultados de la API del límite visual del frontend; una interfaz con paginación debe solicitar todas las páginas y no asumir un máximo de 1.000 filas.

## Plan recomendado

### Antes de automatizar la publicación

1. Crear `source_key` estable y control central de activación/exclusión.
2. Modelar `complete_empty` y probar su conciliación segura.
3. Hacer transaccional la publicación del catálogo.
4. Separar grupos de Actions o externalizar los checkpoints.

### Durante la integración de las próximas fuentes

1. Aplicar esquema estricto a los JSON.
2. Añadir una prueba contractual y una muestra HTML por cada conector nuevo.
3. Registrar salud, volumen y duración por fuente.
4. Priorizar regiones con baja cobertura en lugar de sumar fuentes redundantes de Santiago.

### Antes del backend de recomendaciones

1. Definir reglas de consentimiento, exclusión y trazabilidad de fuente/organizador.
2. Mantener preferencias de usuarios separadas del catálogo de eventos.
3. Guardar señales agregadas con minimización de datos y una política de retención definida.
4. Versionar categorías y pesos para que el algoritmo se pueda recalcular y auditar.

## Criterio de cierre

Las incorporaciones de Iquique y Molino Machmar, junto con la resiliencia regional de Ticketplus, están listas. La siguiente ejecución normal procesará y publicará las fuentes nuevas. No se ejecutó una carga completa durante esta auditoría para evitar iniciar un proceso de varias horas sin necesidad; sí se verificaron los conectores nuevos contra sus páginas reales y se ejecutó la suite automatizada.
