# Arquitectura

El proyecto usa una arquitectura de pipeline con conectores intercambiables. Los dos ejecutables de la raíz solo reciben la orden; el trabajo vive en el paquete `eventos/`.

## Flujo

1. `SourceRepository` carga y valida los archivos de `sources/`.
2. El pipeline agrupa fuentes por dominio y procesa varios dominios mediante un pool acotado.
3. `ConnectorFactory` selecciona una Strategy a partir de `connector` y cada conector devuelve objetos `Event` con el mismo esquema.
4. `EventSanitizer` limpia todos los campos, limita descripciones y descarta vencidos.
5. El pipeline deduplica primero por fuente y luego globalmente, fijando el identificador estable.
6. `LocationNormalizer` aplica defaults, reglas y luego IA solo a ubicaciones incompletas.
7. El pipeline filtra por región y construye una ubicación canónica siempre presente.
8. `DatasetExporter` escribe el respaldo local.
9. Si se habilita Supabase, `SupabaseExporter` transforma el lote, lo sube a `catalog_staging` y una RPC publica y limpia el catálogo dentro de una transacción.

`Event.categories` es una lista normalizada y sin duplicados. El exportador conserva el arreglo
nativo en JSON y lo serializa como JSON dentro de la celda CSV. En Supabase se transforma en una
relación muchos-a-muchos mediante `categories` y `event_categories`, sin modificar los conectores.

## Carpetas

```text
eventos/
├── connectors/       # Una Strategy por familia de fuentes
├── extractors/       # Formatos reutilizables, por ejemplo JSON-LD
├── services/         # NVIDIA NIM y carga remota a Supabase
├── config.py         # SourceConfig, Settings y SourceRepository
├── consolidation.py  # Filtro regional y deduplicación
├── exporters.py      # JSON y CSV
├── cache.py          # Caché HTTP condicional persistente
├── http.py           # Sesión HTTP, espera, métricas y errores
├── mapping.py        # Rutas JSON y construcción canónica declarativa
├── models.py         # Esquema Event
├── locations.py      # Ubicación canónica, precisión, procedencia y fallback
├── pipeline.py       # Orquestación
├── sanitization.py   # Limpieza y vigencia obligatorias para toda fuente
└── text.py           # Limpieza de texto y fechas

docs/                 # Documentación funcional y técnica
sources/              # Una configuración JSON por agenda
supabase/             # Migraciones SQL versionadas
tests/                # Pruebas automáticas
tools/                # Utilidades de mantenimiento y documentación
```

## Patrones aplicados

- **Strategy:** cada sitio o familia de sitios implementa `Connector.collect()`.
- **Registry/Factory:** `@register_connector(...)` registra Strategies y la factoría las crea desde el JSON.
- **Repository:** el pipeline no conoce cómo se almacenan los archivos de fuentes.
- **Dependency injection:** las pruebas pueden sustituir cliente HTTP, repositorio y exportador.
- **Adapter:** `SupabaseExporter` traduce `Event` al esquema relacional sin acoplar los conectores.
- **Chain of Responsibility:** ubicación publicada → default → regla → IA → fallback geográfico.
- **Rate Limiter:** todas las llamadas a NVIDIA comparten el mismo límite global de RPM.
- **Bulkhead por dominio:** distintos sitios avanzan en paralelo, pero un mismo dominio conserva
  una cola secuencial y su pausa entre solicitudes.
- **Cache-Aside condicional:** ETag y Last-Modified permiten reutilizar respuestas solamente cuando
  el servidor confirma que no cambiaron.
- **Post-processing pipeline:** toda Strategy atraviesa el mismo saneamiento y filtro de vigencia;
  un conector nuevo no puede saltarse accidentalmente esas reglas.

## Concurrencia y límites

`SOURCE_WORKERS=4` crea hasta cuatro trabajadores internos; no crea cuatro procesos que escriban
los mismos archivos. Los resultados se vuelven a ordenar según la configuración antes de
deduplicar, por lo que la salida es determinista. Si dos fuentes comparten dominio se asignan al
mismo trabajador y se procesan una tras otra.

NVIDIA usa un limitador global adicional: aunque varios conectores terminen simultáneamente, el
inicio de las peticiones NIM se espacia según `NVIDIA_RPM_LIMIT=40`. Las respuestas pueden terminar
en paralelo, pero la fase paralela nunca aumenta esa frecuencia. El conector de Maipú abre una sola ventana visible y pagina su backend desde
esa sesión, bloqueando imágenes, fuentes y multimedia; las demás fuentes trabajan a la vez sin
ventana porque usan HTTP directo.

## Añadir una fuente

Para una agenda común, ejecuta `python add_source.py`. El conector `generic` busca primero eventos Schema.org/JSON-LD y usa NVIDIA NIM solo cuando no existen datos estructurados.

Si WordPress expone los campos en su API, usa `wordpress_rest` y un `field_map`. Las rutas aceptan objetos y posiciones de listas:

```json
{
  "connector": "wordpress_rest",
  "api_url": "https://sitio.cl/wp-json/wp/v2/events",
  "field_map": {
    "title": "title.rendered",
    "start_date": "event_start",
    "venue": "_embedded.wp:term.1.0.name",
    "source_url": "link"
  }
}
```

Para una API JSON distinta usa `json_api`. `results_path` apunta a la lista,
`total_pages_path` es opcional y `field_map` utiliza el mismo sistema de rutas. Una lista de rutas
actúa como cadena de alternativas:

```json
{
  "connector": "json_api",
  "api_url": "https://sitio.cl/api/events",
  "results_path": "data.results",
  "total_pages_path": "data.page_count",
  "field_map": {
    "title": "name",
    "start_date": "dates.start",
    "description": ["summary", "description"],
    "source_url": "url"
  }
}
```

Las plantillas completas se encuentran en `sources/templates/`. El conector procesa una página por
vez, de modo que no conserva la respuesta completa de una API grande en memoria.

Para carteleras HTML estáticas, `html_cards` permite declarar selectores CSS sin crear otro módulo:

```json
{
  "connector": "html_cards",
  "card_selector": "article.evento",
  "selectors": {
    "title": "h3",
    "start_date": ".fecha",
    "venue": ".lugar",
    "source_url": {"selector": "a", "attribute": "href"}
  }
}
```

`html_cards` también admite `category_rules`: expresiones regulares configurables que agregan
varias categorías según el texto de cada tarjeta. El conector `fisa_portfolio` se reserva para
el catálogo oficial de FISA porque sus fichas usan pares etiquetados de fecha y lugar.

Una estructura realmente nueva requiere un único módulo dentro de `eventos/connectors/`. La clase hereda de `Connector`, implementa `collect()` y se registra con `@register_connector("nombre")`; después se importa desde `eventos/connectors/__init__.py`.
