# Definición operativa de cultura y criterio de inclusión

## Definición amplia

La cultura es el conjunto dinámico de significados, conocimientos, valores,
lenguajes, símbolos, memorias, expresiones, prácticas, técnicas y formas de vida
mediante las cuales una persona o comunidad comprende el mundo, construye
identidad, se relaciona y transmite experiencia entre generaciones.

Esta definición comprende las artes, pero no se limita a ellas. Incluye también
el patrimonio material e inmaterial, las tradiciones, lenguas y relatos; la
gastronomía y la cultura del vino; los oficios y la artesanía; el diseño y la
arquitectura cuando poseen una dimensión expresiva o patrimonial; las culturas
territoriales, indígenas, migrantes, urbanas, juveniles y digitales; la memoria,
la lectura, el juego, la ciencia o la tecnología cuando se presentan como
prácticas de creación, mediación, identidad o participación social.

La cultura no es estática ni equivale solamente a una lista de disciplinas. Se
crea, disputa, mezcla y transforma con el tiempo.

## Definición operativa de evento cultural

Para este catálogo, un evento es cultural cuando su finalidad principal o una
parte sustantiva de su programación permite crear, expresar, conservar,
interpretar, compartir o participar en manifestaciones culturales. Puede ser
artístico, patrimonial, comunitario, gastronómico, literario, audiovisual,
festivo, artesanal, educativo, territorial o perteneciente a culturas digitales.

Que una actividad refleje la vida de una sociedad no basta para incorporarla:
si toda actividad humana se clasificara automáticamente como cultural, el
catálogo perdería utilidad. Una exposición minera o inmobiliaria puede tener un
contexto cultural, pero se considera pertinente solo cuando ofrece una dimensión
cultural explícita, por ejemplo memoria minera, patrimonio industrial, diseño,
mediación pública o programación artística.

## Regla de decisión

1. **Aceptado:** existe una dimensión cultural explícita en el título,
   categorías o descripción.
2. **Revisión:** parece una actividad pública, pero la evidencia disponible es
   insuficiente o ambigua. Por defecto se conserva para evitar falsos negativos.
3. **Excluido:** predominan objetivos industriales o comerciales y no aparece
   una dimensión cultural sustantiva.

El filtro nunca decide únicamente por las palabras “feria”, “exposición” o
“evento”. Estas describen un formato, no una finalidad cultural.

## Alcance técnico

El filtro se ejecuta globalmente después del saneamiento, consolidación y filtro
regional, y antes de exportar o enviar a Supabase. Las decisiones quedan en
`data/cultural_filter_audit.json`, incluyendo las señales y el motivo. El ajuste
`cultural_review_action` permite conservar (`keep`) o excluir (`exclude`) los
casos de revisión sin cambiar el código.
