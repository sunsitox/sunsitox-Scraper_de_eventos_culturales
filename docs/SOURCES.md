# Fuentes de datos del agregador de eventos culturales

**Actualización:** 27 de octubre de 2026
**Cobertura:** las 16 regiones de Chile mediante Chile Cultura, complementadas por agendas
municipales, culturales, universitarias, vinícolas y de exposiciones.

Este inventario deja constancia de la procedencia de los datos públicos utilizados por el
prototipo. Cada evento conserva `source_name`, `source_url`, `official_url`, `extraction_method` y
`extracted_at` para poder rastrearlo hasta su publicación original. Las fechas, precios y
condiciones de acceso siempre deben confirmarse en esa publicación.

## Fuentes activas

| Fuente | Cobertura o especialidad | Método |
|---|---|---|
| [Chile Cultura](https://chilecultura.gob.cl/) | Catálogo nacional; 16 regiones | API pública paginada |
| [Maipú en Común](https://www.maipuencomun.cl/) | Cartelera comunal de Maipú | API de la aplicación consultada mediante Playwright |
| [Centro Cultural GAM](https://gam.cl/es/calendario/) | Agenda cultural de Santiago | JSON-LD |
| [Centro Cultural La Moneda](https://www.cclm.cl/actividades-y-talleres/) | Exposiciones, cine y talleres | Tarjetas HTML declarativas |
| [Centro Cultural CEINA](https://ceina.cl/cartelera/) | Teatro, música, danza, talleres y performance en Santiago | Tarjetas HTML declarativas; fechas simples y rangos entre meses |
| [Valpo Cultura](https://valpocultura.cl/agenda-cultural/) | Agenda cultural municipal de Valparaíso | API The Events Calendar |
| [Espacio Cultural Viña del Mar](https://www.culturaviva.cl/actividades/) | Agenda cultural de Viña del Mar | API The Events Calendar |
| [Teatro Municipal de Viña del Mar](https://teatrovina.cl/cartelera/) | Artes escénicas y espectáculos | JSON-LD |
| [Agenda Cultural UOH](https://www.uoh.cl/extension/agenda/) | Universidad de O'Higgins | WordPress REST; fechas propias y resumen Yoast saneado |
| [Teatro Biobío](https://teatrobiobio.cl/categoria/cartelera/) | Teatro, música, danza y circo en Concepción | WordPress REST; fechas visibles interpretadas de forma determinista |
| [Agenda Universidad de Talca](https://agenda.utalca.cl/) | Actividades universitarias y culturales | Tarjetas HTML declarativas |
| [Enoturismo Chile · Agenda](https://www.enoturismochile.cl/agenda/) | Vendimias y enoturismo | WordPress/EventON |
| [FISA · Ferias y Exposiciones](https://www.fisa.cl/nuestras-ferias/) | Ferias y exposiciones sectoriales | Portafolio público de eventos |
| [Espacio Riesco · Calendario](https://www.espacioriesco.cl/calendario/) | Exposiciones, congresos y eventos masivos | Tarjetas HTML declarativas |
| [Parque Cultural de Valparaíso](https://parquecultural.cl/agenda-de-actividades/) | Programación artística y cultural de Valparaíso | WordPress REST declarativo |
| [Fundación CorpArtes](https://corpartes.cl/programacion/) | Música, teatro y artes escénicas en Las Condes | Tarjetas HTML declarativas |
| [Cultura Vitacura](https://vitacuracultura.cl/actividades/) | Actividades culturales comunales y recurrentes | Tarjetas HTML declarativas |
| [FondasChile](https://www.fondaschile.cl/) | Fondas y ramadas de Fiestas Patrias en Chile | Tarjetas HTML declarativas; solo se ejecuta en septiembre |
| [Santiago Cultura](https://www.santiagocultura.cl/agenda-cultural/) | Agenda cultural municipal de Santiago | Índice WordPress completo y detalles incrementales por modified_gmt, con revalidación a los siete días |
| [Ticketplus · Chile](https://ticketplus.cl/states/region-metropolitana) | Carteleras de las 16 regiones | Selector regional público, fichas estructuradas y filtro cultural global |
| [Corporación Cultural de Iquique](https://culturaiquique.cl/) | Programación cultural de Tarapacá | Tarjetas institucionales con fechas, categorías y fichas oficiales |
| [Centro de Arte Molino Machmar](https://www.molinomachmar.cl/cartelera/) | Artes visuales, música, literatura y artes escénicas en Puerto Varas | Tarjetas institucionales con fechas abreviadas y fichas oficiales |
| [Teatro del Lago](https://teatrodellago.cl/agenda-eventos/) | Música, danza, exposiciones y formación en Frutillar, Los Lagos | API pública The Events Calendar paginada |
| [Teatro Municipal de Chillán](https://teatrochillan.cl/cartelera/) | Teatro, conciertos, danza, cine y humor en Ñuble | Sección de próximos eventos; fecha con año y hora en la ficha |
| [Teatro Regional Cervantes](https://www.teatroregionalcervantes.cl/cartelera/) | Cine, música, danza y artes escénicas en Valdivia, Los Ríos | Tarjetas HTML con fecha completa, categorías y enlaces individuales |
| [Corporación Cultural de Puerto Montt](https://culturapuertomontt.cl/cartelera/) | Programación de los espacios de la corporación en Puerto Montt, Los Lagos | Cartelera móvil de los próximos 30 días; descripción, imagen y organizador explícito desde la ficha |
| [Teatro Oriente](https://teatrooriente.cl/cartelera-eventos/) | Cine, teatro, conciertos, humor y temporadas líricas en Providencia | Cartelera HTML y fechas ISO del JSON-LD público; no consulta la API protegida |

## Criterios de tratamiento

- Se consultan solamente páginas y endpoints públicos.
- Las APIs, JSON-LD y estructuras declarativas tienen prioridad sobre el respaldo con IA.
- Las fuentes de dominios diferentes pueden procesarse simultáneamente; cada dominio conserva una
  pausa independiente. NVIDIA permanece en una cola global de 40 RPM.
- Se conservan eventos futuros o todavía vigentes y se deduplican por título, fecha, recinto y
  comuna.
- El HTML, los scripts, estilos y shortcodes internos de constructores visuales se eliminan de las
  descripciones antes de exportar.
- UOH publica además un cuerpo de página completo con bloques WPBakery. Ese campo no se utiliza como
  descripción y el saneador común impide que fragmentos `vc_*`, reglas CSS o scripts lleguen a la salida.
- Las ubicaciones se completan solo hasta el nivel verificable y se registra su precisión y origen.
- La configuración ejecutable de cada fuente reside en `sources/`; `_template.json` es solo una
  plantilla y no se ejecuta.

La configuración vigente contiene **26 fuentes permanentes y 1 fuente estacional de septiembre** (27 configuraciones). Todas están habilitadas; la fuente de FondasChile se ejecuta únicamente durante septiembre. Una configuración sin eventos vigentes no aparece en el conteo visible de la aplicación.

## Comprobación de las cinco incorporaciones del 27-10-2026

Se ejecutó una extracción local de solo lectura, con el cliente HTTP del proyecto, TLS verificado,
pausa de un segundo por solicitud y sin NVIDIA ni conexión a Supabase. Las cinco fuentes
completaron la prueba: Teatro del Lago (7 fichas vigentes), Chillán (5), Cervantes (18), Puerto
Montt (4) y Oriente (9): 43 fichas antes de deduplicar contra el catálogo existente. Estos conteos
son una observación de la prueba, no una cantidad esperada permanente ni garantía de disponibilidad
desde GitHub Actions. Se comprobó saneamiento y normalización de fechas, ubicación, categorías y URL.

La cartelera de Puerto Montt tiene una ventana de 30 días definida por el sitio; no se afirma
haber extraído todo su archivo ni toda su programación futura. Allí el año se interpreta dentro
de esa ventana y el organizador solo se asigna si la ficha lo declara. En Chillán se toma el año
explícito del detalle (incluidos eventos de 2027). Oriente excluye cancelaciones y avisos
permanentes que no representan funciones. Las fichas cuyo detalle falle temporalmente dejan la
fuente como parcial, para no autorizar eliminaciones por una extracción incompleta.

La accesibilidad pública y la ausencia de un bloqueo aplicable en `robots.txt` no equivalen a
una autorización contractual. Se conserva la atribución y debe atenderse cualquier solicitud
de exclusión de la fuente u organizador. El inventario Excel conserva su auditoría anterior;
esta ampliación se documenta aquí y en los JSON ejecutables, sin asignarle un puntaje técnico
histórico que no haya sido recalculado.

## Fuente retirada

Rancagua Cultura se retiró el 25 de septiembre de 2026 porque la cartelera devolvió HTTP 403 desde GitHub Actions. `settings.json` conserva su nombre en `retired_source_names`: en la siguiente sincronización exitosa, Supabase retirará únicamente los eventos que todavía pertenezcan a esa fuente. No se elimina información de ninguna otra fuente.
