# Pipeline de validación de fuentes

`validate_sources.py` audita el inventario sin ejecutar el scraper de eventos ni escribir en
Supabase. Separa agendas utilizables de páginas inaccesibles, enlaces muertos, portales genéricos
y simples referencias territoriales.

## Estados de salida

- `aprobada`: accesible, compatible, chilena, cultural y con señales comprobables de una agenda
  o cartelera recurrente.
- `revision_manual`: presenta señales útiles, pero está bloqueada, agotó el tiempo o no aporta
  evidencia suficiente para aprobarla automáticamente.
- `rechazada`: URL inválida o muerta, contenido incompatible, página estacionada, fuente no
  cultural o prohibición explícita en `robots.txt`.
- `no_aplica`: referencia territorial sin URL de eventos. Se conserva para investigación, pero no
  se cuenta como fuente digital.

## Reglas

Cada resultado conserva el detalle y puntaje de estas comprobaciones:

1. URL HTTP/HTTPS y respuesta accesible.
2. Permiso indicado por `robots.txt`.
3. Tipo de contenido HTML, JSON o PDF.
4. Relevancia cultural.
5. Vocabulario de agenda y programación.
6. Objetos `Event` en JSON-LD.
7. Fechas visibles.
8. Enlaces de eventos o cartelera.
9. Relación con Chile mediante dominio, localidad o contenido.
10. Ruta recurrente (`agenda`, `eventos`, `cartelera`, etc.).
11. Ausencia de páginas estacionadas, CAPTCHA o suspensión.

La aprobación exige 65 puntos, relevancia cultural, cobertura chilena y una señal fuerte de
eventos. Una prohibición de `robots.txt`, un `404` o una página estacionada impiden aprobar aunque
existan palabras coincidentes.

## Ejecutar

En Windows:

```bat
validar_fuentes.bat
```

De forma directa:

```powershell
python validate_sources.py --workers 6 --timeout 25 --domain-delay 1.25
```

Los resultados se escriben en `data/source_validation/`:

- `results.json` y `results.csv`: auditoría completa.
- `approved_sources.json`: solo las fuentes aprobadas.
- `review_queue.json`: cola de revisión humana.
- `summary.json`: conteos, porcentaje y duración.

## Actualizar el inventario

El archivo versionable usado por defecto es `data/source_candidates.json`. Para reconstruirlo
desde el Excel de investigación:

```powershell
python tools/import_source_inventory.py "ruta\Inventario_fuentes_eventos_culturales_Chile.xlsx"
```

El importador también acepta CSV y JSON. Una nueva ejecución vuelve a revisar todas las entradas;
no reutiliza automáticamente un veredicto antiguo.

## Rendimiento y cortesía

Se procesan dominios distintos en paralelo, pero cada dominio conserva una pausa mínima entre
`robots.txt` y la página. El cuerpo leído se limita a 1,5 MB y no se descargan imágenes, videos ni
archivos adjuntos. Los parámetros pueden ajustarse por CLI sin modificar código.

NVIDIA NIM no participa en esta etapa: la clasificación debe ser repetible y explicable. La cola
`revision_manual` permite realizar después una segunda revisión humana o asistida por IA sin
consumir llamadas en las decisiones evidentes.
