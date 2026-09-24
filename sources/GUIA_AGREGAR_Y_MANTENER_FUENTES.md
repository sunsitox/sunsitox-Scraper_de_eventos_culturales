# Guía para agregar y mantener fuentes de eventos

Esta carpeta contiene una configuración JSON por fuente. El pipeline carga
automáticamente todos los archivos `*.json`, excepto aquellos cuyo nombre
comienza con `_`. En la mayoría de los casos se puede agregar o reparar una
fuente sin modificar `scrape_events.py`.

## 1. Antes de integrar una fuente

Comprueba que:

- sea una agenda chilena pública y accesible sin iniciar sesión;
- publique eventos individuales, no solamente noticias institucionales;
- permita obtener como mínimo título, fecha y una URL de referencia;
- sus términos de uso y `robots.txt` no prohíban la consulta automatizada;
- la frecuencia de ejecución sea razonable para el sitio;
- los eventos puedan cumplir la definición del catálogo descrita en
  [`../docs/DEFINICION_Y_FILTRO_CULTURAL.md`](../docs/DEFINICION_Y_FILTRO_CULTURAL.md).

Una fuente puede contener eventos comerciales o industriales. El filtro global
evaluará cada ficha antes de exportarla o enviarla a Supabase; las palabras
“feria” y “exposición” no garantizan por sí solas su pertinencia cultural.

## 2. Elegir el método de extracción

Usa la opción estructurada más estable disponible, en este orden:

1. **API JSON pública:** `json_api`.
2. **WordPress REST:** `wordpress_rest`.
3. **The Events Calendar:** `tribe_events_api`.
4. **Tarjetas HTML repetibles:** `html_cards`.
5. **JSON-LD o página común:** `generic`.
6. **Conector Python específico:** únicamente cuando la estructura anterior no
   se puede expresar mediante configuración.

`generic` busca primero datos JSON-LD. NVIDIA NIM es solamente un respaldo y no
debe elegirse como primera opción cuando exista una API o estructura estable.

Los conectores `eventon_wordpress`, `chilecultura_api`, `fisa_portfolio` y
`maipu_browser` responden a plataformas o sitios particulares; no deben
reutilizarse para otro dominio sin comprobar que comparte exactamente el mismo
formato.

### Validación de enlaces individuales

Si una API o cartelera conserva registros cuyo enlace público ya fue eliminado, agrega:

```json
"validate_event_urls": true
```

La validación descarta únicamente HTTP 404, HTTP 410 y páginas 404 simuladas reconocibles. Los
timeouts, errores del servidor y bloqueos temporales no eliminan eventos. Actívala cuando cada
evento tenga una URL individual; no aporta información si todos comparten la URL general de agenda.

## 3. Ruta rápida: asistente interactivo

Desde la raíz del proyecto y con el entorno virtual activo:

```powershell
python add_source.py
```

El asistente crea el JSON dentro de esta carpeta. Después revisa manualmente el
archivo generado: el asistente no puede verificar que las rutas JSON o los
selectores CSS elegidos devuelvan los campos correctos.

## 4. Ruta manual: copiar una plantilla

Hay ejemplos en [`templates`](templates) para `json_api`, `wordpress_rest` y
`html_cards`. También se puede copiar [`_template.json`](_template.json) para
una página genérica. Usa un nombre de archivo descriptivo en minúsculas y con
guiones, por ejemplo `municipalidad-de-ejemplo.json`.

Configuración básica:

```json
{
  "name": "Agenda Cultural de Ejemplo",
  "url": "https://ejemplo.cl/agenda/",
  "connector": "generic",
  "region": "Región del Maule",
  "commune": "Talca",
  "organizer": "Municipalidad de Ejemplo",
  "default_categories": ["Cultura"],
  "official": true,
  "max_pages": 30,
  "max_results": 0,
  "enabled": true
}
```

Campos comunes:

| Campo | Uso |
|---|---|
| `name` | Nombre único que aparecerá como fuente. |
| `url` | Agenda pública o página principal de extracción. |
| `connector` | Estrategia registrada que interpreta la fuente. |
| `region`, `commune`, `city` | Valores predeterminados; no inventarlos si la fuente abarca varias zonas. |
| `organizer` | Organizador asumido solamente cuando es común a todos sus eventos. |
| `default_categories` | Lista de categorías de respaldo, no texto separado por comas. |
| `location_defaults` | Recinto, dirección, código postal o coordenadas comunes y comprobadas para todos los eventos. |
| `official` | Indica si la fuente puede considerarse URL oficial del evento. |
| `max_results` | `0` significa sin límite. Un valor positivo sirve para pruebas controladas. |
| `max_pages` | Máximo de páginas; su interpretación depende del conector. |
| `verify_ssl` | Déjalo en `true`; usa `false` solo como excepción documentada ante un certificado defectuoso. |
| `enabled` | Permite desactivar temporalmente una fuente sin borrar su configuración. |
| `incremental` | Activa caché de detalles parseados para `generic` y `eventon_wordpress`/`santiago_wordpress`. |
| `detail_refresh_hours` | Máxima antigüedad del detalle sin versión de origen: 24 horas por defecto. |
| `full_audit_days` | En EventON, revalida cada ficha aunque su modificación no cambie: 7 días por defecto. |
| `link_path_prefixes` | Restringe el descubrimiento genérico a rutas de eventos; ejemplo `["/events/"]`. |

Los checkpoints y pendientes de IA se guardan en `state/pipeline.sqlite3`. Cambiar el JSON
invalida la caché de detalles y el checkpoint de esa fuente. Un conector que obtenga solo
parte de las fichas debe establecer `collection_complete = False`: así se conservan sus
resultados válidos sin autorizar eliminaciones por ausencia en Supabase. Consulta
[la guía del pipeline reanudable](../docs/RESUMABLE_PIPELINE.md) antes de activar caché
en una fuente nueva: el TTL determina cuánto puede demorarse la detección de cambios.

No coloques claves, tokens, contraseñas ni encabezados privados en estos JSON.
Los secretos deben permanecer en `.env` o en GitHub Secrets.

Cuando una agenda usa varios recintos conocidos y el afiche menciona solamente
el nombre, declara metadatos comprobados por recinto. Esto permite completar la
dirección sin pedirle al modelo que la invente:

```json
"location_metadata": {
  "Teatro Regional": {
    "venue": "Teatro Regional",
    "address": "Calle Cultura 123",
    "commune": "Rancagua",
    "region": "Región del Libertador Bernardo O'Higgins",
    "postal_code": "",
    "latitude": "-34.1708",
    "longitude": "-70.7444"
  }
}
```

La clave es el texto que debe encontrarse en el título, recinto, descripción u
OCR. Estos datos son estáticos: compruébalos contra una fuente oficial y
actualízalos si el recinto cambia de dirección.

## 5. Configurar una API JSON

Usa [`templates/json-api.json`](templates/json-api.json). `results_path` debe
apuntar a la lista de eventos. `field_map` relaciona el modelo canónico con las
rutas de cada objeto JSON:

```json
"field_map": {
  "title": "data.name",
  "start_date": "dates.start",
  "end_date": "dates.end",
  "venue": "location.venue",
  "address": "location.address",
  "commune": "location.commune",
  "region": "location.region",
  "postal_code": "location.postal_code",
  "latitude": "location.latitude",
  "longitude": "location.longitude",
  "organizer": "organizer.name",
  "categories": "categories",
  "description": "summary",
  "image_url": "image.url",
  "source_url": "permalink",
  "official_url": "official_url"
}
```

Las rutas anidadas se separan con puntos. Una lista usa índices numéricos, por
ejemplo `_embedded.wp:featuredmedia.0.source_url`. También se puede usar una
lista de rutas alternativas: `["start_date", "dates.start"]`.

Configura `page_param`, `page_size_param`, `page_size`, `page_start`,
`total_pages_path` y `request_params` según la API. Con `max_pages: 0` y
`max_results: 0` no se impone un límite artificial, pero la API debe ofrecer una
condición fiable para detectar la última página.

## 6. Configurar tarjetas HTML

Usa [`templates/html-cards.json`](templates/html-cards.json):

- `card_selector` identifica el contenedor repetido de cada evento;
- `selectors.title` es obligatorio;
- cada selector puede ser una cadena para leer texto o un objeto con
  `selector` y `attribute` para leer `href`, `src`, etc.;
- los campos disponibles incluyen `title`, `start_date`, `end_date`,
  `date_range`, `start_time`, `end_time`, `venue`, `address`, `commune`,
  `city`, `region`, `postal_code`, `latitude`, `longitude`, `organizer`, `categories`, `audience`, `description`,
  `image_url`, `price` y `source_url`;
- `date_range_separator` permite separar un rango publicado en un solo nodo;
- `category_rules` puede agregar categorías mediante expresiones regulares.

Ejemplo de enlace:

```json
"source_url": {
  "selector": "a.detalle",
  "attribute": "href"
}
```

Prefiere selectores semánticos y cortos, como `article.evento` o
`[data-event-id]`. Evita selectores basados en posiciones como
`:nth-child(3)`, clases generadas automáticamente o cadenas completas de la
jerarquía visual: suelen romperse con cambios menores de diseño.

## 7. Cuándo crear un conector Python

Crea un archivo en `eventos/connectors/` solamente si la fuente requiere lógica
que no cabe en los conectores declarativos: autenticación pública especial,
paginación atípica, navegador, múltiples endpoints o transformación propia.

El nuevo conector debe:

1. heredar de `Connector`;
2. registrarse con `@register_connector("nombre")`;
3. devolver objetos `Event` canónicos;
4. aplicar `is_upcoming` o dejar que el saneamiento global elimine vencidos;
5. respetar el cliente HTTP compartido, sus pausas y su caché;
6. importarse en `eventos/connectors/__init__.py`;
7. contar con pruebas sin depender de la red real.

No agregues excepciones específicas de un sitio al pipeline global.

## 8. Comprobar una integración

Antes de activarla en una ejecución periódica:

1. crea la fuente inicialmente con `"enabled": false`;
2. revisa la URL, API, paginación, campos y ubicación;
3. actívala y ejecuta una extracción local;
4. revisa la línea de esa fuente en `data/source_metrics.json`;
5. busca sus eventos en `data/events.json` y confirma fechas, ubicación,
   categorías, URLs y ausencia de HTML/CSS residual;
6. revisa sus decisiones en `data/cultural_filter_audit.json`;
7. ejecuta todas las pruebas:

```powershell
python -m unittest discover -s tests -v
```

Para una prueba rápida se puede usar temporalmente `max_results`, pero debe
volver a `0` si la ejecución productiva tiene que extraer todo. No hagas la
primera prueba con `--supabase`: valida primero los archivos locales y luego
realiza la sincronización remota.

El pipeline independiente `validar_fuentes.bat` audita el inventario de
candidatas. Su resultado técnico no activa ni desactiva automáticamente los JSON
de esta carpeta.

## 9. Si una fuente se cae

Una fuente se considera caída cuando devuelve errores de red repetidos, un estado
HTTP de error, un certificado inválido, bloqueo por `robots.txt`, o cero eventos
cuando normalmente publicaba varios.

Procedimiento:

1. consulta `data/source_metrics.json` y el registro de la ejecución para aislar
   la fuente y el tipo de fallo;
2. abre su `url` y, si existe, su `api_url` para distinguir una caída temporal de
   un cambio permanente;
3. comprueba redirecciones, dominio nuevo, certificado, `robots.txt` y términos
   de uso;
4. repite una vez en otra ejecución; no aumentes reintentos ni concurrencia para
   forzar un servidor caído;
5. si el problema persiste, cambia `enabled` a `false` para que las demás fuentes
   y la carga a Supabase continúen;
6. registra en el JSON una nota `maintenance_note` con fecha, causa conocida y
   alternativa encontrada;
7. reactívala únicamente después de validar localmente que vuelve a producir
   eventos correctos.

No desactives la verificación TLS automáticamente. `verify_ssl: false` reduce la
seguridad y solo debe emplearse temporalmente cuando se haya comprobado que el
dominio es legítimo y el único problema es su cadena de certificados.

## 10. Si cambia la estructura de la página

Síntomas frecuentes:

- la fuente responde correctamente, pero extrae cero eventos;
- desaparecen fechas, enlaces o ubicaciones;
- títulos y descripciones contienen menús, CSS o bloques completos de la página;
- la paginación se detiene antes de tiempo o repite fichas.

Diagnóstico y reparación:

1. determina si apareció una API o JSON-LD nuevo; migrar a datos estructurados es
   preferible a reparar selectores HTML frágiles;
2. compara una ficha actual con la configuración de la fuente;
3. en `html_cards`, actualiza primero `card_selector` y luego solamente los
   selectores de campos que fallaron;
4. en APIs, actualiza `api_url`, `results_path`, la paginación o `field_map`;
5. si el contenido ahora se genera con JavaScript, busca las solicitudes de red
   que usa la página antes de crear automatización de navegador;
6. agrega o actualiza una prueba que reproduzca la estructura nueva;
7. ejecuta el pipeline local sin Supabase y compara cantidad y calidad con una
   ejecución anterior razonable;
8. reactiva la fuente y documenta el cambio en `maintenance_note`.

No arregles un cambio estructural asignando texto inventado como título, fecha o
ubicación. Cuando el dato no existe, debe quedar vacío o utilizarse un valor
predeterminado explícito y comprobable.

## 11. Control operativo recomendado

En GitHub Actions conserva como artefactos o resumen de cada ejecución:

- total final y duración (`data/run_summary.json`);
- estado, duración y cantidad por fuente (`data/source_metrics.json`);
- decisiones del filtro (`data/cultural_filter_audit.json`).

Una alerta útil debe activarse por cambios anómalos, no únicamente por cero
global: fuente fallida, reducción brusca respecto de su promedio, aumento súbito
de casos en revisión o ausencia de un campo esencial. Un sitio puede publicar
cero eventos legítimamente fuera de temporada.

## Lista de comprobación final

- [ ] La fuente es pública, pertinente y permite automatización.
- [ ] Se eligió el conector más estructurado disponible.
- [ ] El JSON no contiene secretos.
- [ ] `max_results` y `max_pages` no recortan accidentalmente la producción.
- [ ] Las fechas y la paginación fueron comprobadas.
- [ ] Ubicación y categorías provienen de datos o valores predeterminados válidos.
- [ ] El saneamiento no deja HTML, CSS ni shortcodes.
- [ ] El filtro cultural clasifica correctamente ejemplos representativos.
- [ ] Las pruebas automatizadas pasan.
- [ ] La extracción local fue revisada antes de usar `--supabase`.
