# Fuentes de datos del agregador de eventos culturales

**Actualización:** 28 de octubre de 2026
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
| [Passline · Chile](https://home.passline.com/home) | Catálogo nacional de espectáculos | Búsqueda JSON pública paginada; país Chile explícito |
| [PuntoTicket · Chile](https://www.puntoticket.com/) | Catálogo nacional de espectáculos | Índice JSON público y detalles JSON-LD incrementales |
| [Corporación Cultural de Iquique](https://culturaiquique.cl/) | Programación cultural de Tarapacá | Tarjetas institucionales con fechas, categorías y fichas oficiales |
| [Centro de Arte Molino Machmar](https://www.molinomachmar.cl/cartelera/) | Artes visuales, música, literatura y artes escénicas en Puerto Varas | Tarjetas institucionales con fechas abreviadas y fichas oficiales |
| [Museo de la Memoria y los Derechos Humanos](https://mmdh.cl/cartelera) | Actividades culturales, memoria, cine y literatura en Santiago | API pública Strapi paginada; fechas ISO y texto original |
| [Teatro Municipal de Chillán](https://teatrochillan.cl/cartelera/) | Teatro, conciertos, danza, cine y humor en Ñuble | Sección de próximos eventos; fecha con año y hora en la ficha |
| [Teatro Regional Cervantes](https://www.teatroregionalcervantes.cl/cartelera/) | Cine, música, danza y artes escénicas en Valdivia, Los Ríos | Tarjetas HTML con fecha completa, categorías y enlaces individuales |
| [Corporación Cultural de Puerto Montt](https://culturapuertomontt.cl/cartelera/) | Programación de los espacios de la corporación en Puerto Montt, Los Lagos | Cartelera móvil de los próximos 30 días; descripción, imagen y organizador explícito desde la ficha |
| [Teatro Regional del Maule](https://www.teatroregional.cl/cartelera) | Programación de Sala TRM en Talca; no extensiones en otras comunas | Cartelera HTML y fichas con año explícito; detención antes del archivo histórico |

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

La configuración vigente contiene **28 fuentes permanentes y 1 fuente estacional de septiembre** (29 configuraciones). Todas están habilitadas; la fuente de FondasChile se ejecuta únicamente durante septiembre. Una configuración sin eventos vigentes no aparece en el conteo visible de la aplicación.

## Catálogos nacionales JSON incorporados el 28-10-2026

- **Passline · Chile:** `https://api.passline.com/v1/event/GetBillboardByFilters`.
  La consulta pública del propio sitio usa POST JSON de lectura con `country=chile` y
  `limit=OFFSET,100`. Se recorren todas las páginas hasta recibir una página corta; las
  repeticiones o fallas dejan la fuente parcial. Se utilizan las fechas completas, recinto,
  comuna y código regional publicados, no fechas de publicación. El identificador numérico
  de productor no se presenta como nombre de organizador. Descripciones o direcciones
  ausentes no se inventan. La prueba local obtuvo 1.404 registros vigentes, 1.372 después
  de normalizar ubicación y deduplicar, y 1.331 candidatos tras el filtro cultural
  (779 aceptados y 552 en revisión). Omitió tres registros inválidos y dejó la fuente parcial,
  sin autorizar eliminaciones por ausencia. Fueron 15 consultas JSON, sin NVIDIA.
- **PuntoTicket · Chile:** `https://www.puntoticket.com/Landing/GetEventosBusqueda`.
  Se consulta el índice JSON y se enriquece cada ficha con su JSON-LD y descripción propia,
  no con el texto de navegación o relacionados. Los detalles se guardan en checkpoints y
  se reutilizan si el registro del índice no cambió, con revalidación a las 24 horas.
  Se conserva el intervalo de fechas del índice: el JSON-LD puede mostrar solo la última
  función. Las entradas sin día/año explícito, abonos y agrupadores sin funciones fechadas
  no se convierten en ocurrencias. El índice observado contenía 242 entradas: la prueba
  obtuvo 235 fichas vigentes, 220 después de geografía/deduplicación y 214 candidatos
  culturales (70 aceptados y 144 en revisión). Se omitieron siete entradas sin fecha
  verificable y se completaron 237 peticiones sin NVIDIA.

La segunda pasada de PuntoTicket reutilizó sus detalles y necesitó solo dos peticiones
(índice y portada). La comparación de solo lectura con Supabase se realiza antes de publicar;
los candidatos no equivalen a altas nuevas y no se atribuye al sitio un contenido ausente.
La medición final obtuvo 1.545 candidatos únicos entre las dos fuentes (849 aceptados y
696 en revisión). La consulta remota encontró 16 coincidencias y 1.529 candidatos sin
coincidencia. Había 946 eventos publicados; sumar todos los candidatos no coincidentes
daría una referencia máxima de 2.475, no un conteo ya sincronizado ni una garantía de
calidad cultural de los casos en revisión. El total real puede cambiar con el tiempo,
el enriquecimiento, la conciliación y las decisiones administrativas.

Ambas fuentes pasan por los mismos filtros culturales, geográficos, de vigencia y duplicados
que el resto. Los géneros musicales explícitos se normalizan; «Club» o «Fiestas» no se convierten
automáticamente en Música. El deporte y las membresías de gimnasio no se admiten para inflar
el objetivo de 1.200–2.000 eventos. Los candidatos en revisión conservan esa distinción y
la visibilidad sigue sujeta al panel administrativo. No hay un cupo artificial de 2.000.

La comprobación usa TLS verificado, identificación honesta del proyecto y un segundo de pausa
por petición. El User-Agent se normalizó a ASCII para evitar incompatibilidad con servidores;
no se imita un navegador ni se eluden bloqueos 403/429. La extracción no requiere claves nuevas,
no consume IA y no cambia su límite global de 40 RPM. OCR y redacción posteriores conservan
sus presupuestos y colas. La accesibilidad desde Actions y el incremento neto publicado deben
confirmarse en la siguiente ejecución completa; estas cifras locales no son una garantía.

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

## Reemplazos del 28-10-2026

Teatro del Lago y Teatro Oriente se retiraron por HTTP 403 reproducido desde GitHub Actions.
Se reemplazaron por Museo de la Memoria y Teatro Regional del Maule, sin aumentar las 27
configuraciones. El museo utiliza `https://manager.mmdh.cl/api/post-carteleras`, el mismo
endpoint público de su cartelera, con paginación y sin credenciales. Los campos de fecha
son ISO UTC; no se usa la fecha de publicación como fecha del evento.

La validación local, con TLS y un segundo entre peticiones, obtuvo cinco eventos vigentes
del museo y seis de Maule, sin NVIDIA ni escrituras en Supabase. Maule mantiene fichas sin
año explícito: se omiten, la fuente queda parcial y no habilita borrados por ausencia. Se
limita a Sala TRM para no asignar Talca a las extensiones regionales. El acceso desde el
runner todavía debe comprobarse: una respuesta HTTP 200 local no lo garantiza.

Ticketplus conserva ahora la descripción original completa del bloque de detalle y sus
etiquetas Disciplina/Género. No toma categorías del carrusel de relacionados. La corrección
actualiza su configuración y obliga a renovar sus fichas en la siguiente fase de extracción;
no borra las colas OCR ni obliga a repetir redacciones cuyo contenido no cambió.

## Fuentes retiradas

Rancagua Cultura se retiró el 25 de septiembre de 2026 porque la cartelera devolvió HTTP 403 desde GitHub Actions. `settings.json` conserva su nombre en `retired_source_names`: en la siguiente sincronización exitosa, Supabase retirará únicamente los eventos que todavía pertenezcan a esa fuente. No se elimina información de ninguna otra fuente.

La lista de retiradas incluye ahora Teatro del Lago y Teatro Oriente. La conciliación respeta
las protecciones existentes para eventos manuales, supresiones e interacciones; no equivale
a borrar indiscriminadamente todos los registros asociados.
