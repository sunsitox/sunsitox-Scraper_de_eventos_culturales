# Estándar de documentación del proyecto

**Versión:** 1.0
**Aplicación:** documentación técnica, funcional, de arquitectura, evidencias y anexos del proyecto.

Este estándar busca que los documentos sean consistentes, fáciles de leer y mantenibles. No reemplaza una pauta de formato que solicite explícitamente la institución o el profesor; en ese caso, prevalece la pauta externa.

## Formato y soporte

- La documentación mantenida en el repositorio se redacta preferentemente en **LaTeX** y se versiona como archivo `.tex` junto a sus recursos.
- El PDF es el formato de distribución. Los archivos auxiliares de compilación (`.aux`, `.log`, `.out`, `.toc`, `.synctex.gz`) no se editan ni se versionan.
- Los diagramas editables se mantienen en **draw.io** (`.drawio`) y se exportan a PDF o PNG solo para su inclusión.
- Markdown se reserva para guías operativas breves, README y documentación que deba leerse directamente en GitHub. Debe respetar la misma jerarquía de títulos y el mismo tono.
- Word se utiliza solo si una entrega institucional lo exige. Debe conservar las mismas reglas de tipografía, márgenes y jerarquía indicadas a continuación.

## Plantilla LaTeX

Los documentos nuevos deben comenzar con:

```tex
\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[spanish,es-nodecimaldot]{babel}
\usepackage{estilo_capstone}
```

La plantilla reutilizable está en `docs/estilo_capstone.sty`. Se usa desde un archivo `.tex` ubicado dentro de `docs/`. Si un documento vive en otra carpeta, debe referenciarla con una ruta relativa, por ejemplo `\usepackage{../docs/estilo_capstone}`.

## Tipografía y composición

| Elemento | Regla |
|---|---|
| Tamaño del texto | 11 pt en LaTeX; 11 pt Aptos o Arial en Word. |
| Tipografía | Latin Modern en LaTeX; Aptos o Arial como alternativa de Word. |
| Tamaño de títulos | Título de portada 24--28 pt; título de sección 16 pt; subsección 13 pt. |
| Interlineado | 1,15. |
| Márgenes | A4, 2,5 cm en los cuatro lados. |
| Alineación | Texto a la izquierda o justificado con buena separación de palabras; nunca reducir el tamaño por debajo de 10 pt para ganar espacio. |
| Párrafos | Sin sangría y con 5 pt de separación posterior. |
| Código | Monoespaciada de 9--10 pt, fondo claro y salto de línea habilitado. |

Se usa azul oscuro para títulos y enlaces, con fondos celestes suaves solo como apoyo. Todo texto debe mantener contraste suficiente sobre blanco; no se transmite significado únicamente por color.

## Estructura mínima

1. Portada o encabezado con título, alcance, versión y fecha.
2. Índice para documentos de más de cuatro secciones.
3. Propósito y alcance, antes de explicar decisiones técnicas.
4. Secciones ordenadas de lo general a lo específico.
5. Fuentes, supuestos, limitaciones y referencias cuando correspondan.
6. Historial breve de cambios si el documento se actualiza de manera periódica.

Los títulos se escriben en estilo oración, no completamente en mayúsculas. Cada sección debe responder una pregunta concreta y evitar bloques de texto demasiado extensos.

## Tablas, figuras y diagramas

- Las tablas deben llevar encabezados claros, usar `booktabs` en LaTeX y evitar líneas verticales.
- Las figuras y diagramas necesitan número, título y fuente o aclaración de elaboración propia.
- Un diagrama debe mantener tipografía legible al 100 % de zoom y no depender de una captura borrosa.
- Las URL extensas se convierten en enlaces con texto descriptivo. Las fuentes externas deben conservar fecha de consulta cuando la información pueda cambiar.

## Escritura y control de calidad

- Redactar en español claro, en voz activa cuando sea posible y sin copiar texto de fuentes de eventos.
- Distinguir hechos verificados, decisiones de diseño y trabajo pendiente.
- Escribir nombres técnicos, variables y rutas con formato monoespaciado.
- Antes de distribuir un PDF, compilarlo y revisar visualmente portada, índice, enlaces, tablas, paginación y caracteres acentuados.
- Mantener los archivos fuente en UTF-8. Si se compila con una instalación antigua de pdfLaTeX que presenta problemas de codificación, usar comandos LaTeX para caracteres sensibles y corregir la configuración antes de reemplazar texto válido.
