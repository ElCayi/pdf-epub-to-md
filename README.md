# pdf-epub-to-md

Script de Python para convertir un PDF o EPUB en un archivo Markdown pensado para leer dentro de Obsidian.

La idea es sacar el texto del PDF, reconstruirlo en párrafos razonables, detectar títulos simples, conservar listas básicas y guardar las imágenes en una carpeta común `img` para que queden enlazadas en la nota final.
Además, añade propiedades al principio de la nota para que sea fácil filtrar libros por autor, serie, trilogía o traductor dentro de Obsidian.

En EPUB se aprovecha la estructura del libro para conservar el orden de lectura,
los capítulos, los párrafos, el énfasis y las notas al pie. Se mantiene la misma
salida de Obsidian: frontmatter YAML, portada e imágenes dentro de `img/`.

## Estructura del proyecto

El ejecutable principal es `pdf_epub_to_md.py`. La entrada histórica
`pdf_to_md.py` sigue funcionando por compatibilidad. La lógica está separada
por responsabilidades dentro de `novel_pdf/`:

- `common.py`: modelos, constantes y extracción básica de líneas.
- `genres.py`: ISBN, consultas bibliográficas y caché de géneros.
- `metadata.py`: autoría, saga, traductor y otros metadatos.
- `layout.py`: párrafos, partes, capítulos, escenas, cursivas e imágenes.
- `converter.py`: selección del formato y conversión de PDF.
- `epub.py`: lectura de EPUB 2/3, metadatos, imágenes y referencias internas.
- `epub_html.py`: XHTML a Markdown y notas al pie para Obsidian.
- `cli.py`: argumentos y mensajes de consola.

Las pruebas de regresión se ejecutan con:

```bash
python -m unittest discover -s tests -v
```

## Qué hace, a grandes rasgos

- Abre un PDF o EPUB desde la consola.
- Extrae el texto página por página.
- Intenta reconstruir párrafos.
- Detecta títulos aproximados según el tamaño de letra.
- Conserva listas simples.
- Guarda las imágenes del PDF en una carpeta común `img`.
- Inserta esas imágenes con sintaxis de Obsidian: `![[archivo.png]]`.
- Si la primera página parece una portada, coloca esa imagen al principio del documento.
- Añade propiedades al principio de la nota en formato YAML de Obsidian.
- Añade la ruta de la imagen de portada en la propiedad `cover`.
- Añade el número total de páginas del PDF en la propiedad `pages`.
- Busca el ISBN y consulta fuentes bibliográficas para añadir `genre` y etiquetas `genero/...`.
- Genera un archivo `.md` en la misma carpeta que el libro, salvo que indiques otra salida.

## Preparar el entorno en NixOS

El proyecto incluye un `flake.nix` con Python y PyMuPDF. Si ya tienes
`direnv` integrado con tu shell, autoriza el entorno una sola vez:

```bash
direnv allow
```

Al entrar en esta carpeta, `direnv` cargará las dependencias automáticamente.
También puedes abrir el entorno de forma manual:

```bash
nix develop
```

No es necesario instalar Python ni usar `pip` globalmente.

### Sin Nix

También funciona con Python 3 y un entorno virtual:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python pdf_epub_to_md.py archivo.epub
```

## Cómo usarlo

### 1. Localizar el libro

Puedes dejar el PDF o EPUB junto al script o indicar la ruta completa del libro.

### 2. Ejecutar la conversión

```bash
python pdf_epub_to_md.py archivo.pdf
python pdf_epub_to_md.py archivo.epub
```

Ejemplo:

```bash
python pdf_epub_to_md.py aniquilacion.pdf
```

### 3. Revisar el resultado

Se creará:

- `Aniquilacion.md`
- `img/`

La carpeta `img` contendrá las imágenes extraídas del libro. Si trasladas la nota
a tu bóveda de Obsidian, lleva también sus imágenes y conserva la ruta `img/`.

### EPUB en Obsidian

```bash
python pdf_epub_to_md.py "Mi novela.epub" --no-genre-lookup
python pdf_epub_to_md.py "Mi novela.epub" -o "Mi novela.md" --genre fantasia
```

El ejecutable `pdf_epub_to_md.py` detecta el formato por la extensión, también
si está en mayúsculas. Puedes usar igualmente
`python -m novel_pdf archivo.epub`.

- Sigue el orden de lectura del EPUB (`spine`), incluyendo los anexos enlazados.
- Conserva encabezados, párrafos, cursivas, negritas, citas, listas anidadas,
  saltos de línea, separadores de escena y tablas sencillas.
- Conserva cursivas y negritas de estilos en línea y reglas CSS con selectores
  sencillos de etiqueta, clase o identificador.
- Lee título, autoría, traductor, editorial, serie y materias de los metadatos.
  Los géneros manuales tienen prioridad; si las materias del libro permiten
  reconocer el género, evita la consulta bibliográfica.
- Extrae la portada declarada por el libro y las imágenes locales, reutilizando
  las imágenes repetidas. Las referencias SVG a imágenes también se resuelven.
- Convierte notas semánticas EPUB a notas al pie de Markdown (`[^epub-…]`).
- Reescribe enlaces internos como referencias a bloques de Obsidian (`#^epub-…`)
  para navegar dentro de la misma nota.
- No añade `pages`: un EPUB de texto adaptable no tiene una paginación fija.

Las opciones `--keep-page-breaks` y `--keep-page-numbers` también funcionan con
EPUB: la primera separa sus documentos internos y la segunda conserva sus
marcadores de página, si existen.

El soporte EPUB usa únicamente la biblioteca estándar de Python; no requiere
instalar otras dependencias ni cambiar el entorno Nix. Los módulos conservan
docstrings con campos Sphinx como `:param:`, `:returns:` y `:raises:`.

La lectura del paquete sigue la estructura descrita en la
[especificación EPUB del W3C](https://www.w3.org/TR/epub-33/).

## Opciones del script

### Cambiar el nombre de salida

```bash
python pdf_epub_to_md.py archivo.pdf -o salida.md
```

### Dejar separadores entre páginas

```bash
python pdf_epub_to_md.py archivo.pdf --keep-page-breaks
```

### Mantener los números de página

```bash
python pdf_epub_to_md.py archivo.pdf --keep-page-numbers
```

### Corregir o indicar el género manualmente

```bash
python pdf_epub_to_md.py archivo.pdf --genre ciencia-ficcion --genre terror
```

Al indicar al menos un `--genre`, no se realiza la consulta bibliográfica.
Para convertir sin buscar géneros en Internet, usa `--no-genre-lookup`.

## Detalles de comportamiento

### Texto

- Las líneas de texto se agrupan en párrafos.
- Los bloques de texto del PDF se usan para reconocer el comienzo de cada párrafo,
  evitando confundir bloques completos sangrados con muchos párrafos separados.
- Los huecos verticales anormalmente grandes se conservan como separadores de escena.
- Las líneas compuestas completamente en cursiva conservan ese formato en Markdown.
- Los párrafos que continúan en la página siguiente se unen para evitar cortes
  propios del formato impreso.
- Si una palabra queda cortada con guion al final de línea, el script intenta recomponerla.
- Los espacios duplicados y caracteres de separación raros se limpian.
- Los pies de página muy bajos o repetidos en varias páginas se filtran automáticamente.

### Títulos

- El script intenta detectar títulos comparando el tamaño de la fuente con el tamaño normal del cuerpo.
- Si una línea parece suficientemente grande, la convierte en `#`, `##` o `###`.
- Las portadas internas como `PRIMERA PARTE` y su subtítulo se conservan como
  encabezados aunque ocupen una página casi vacía.
- Esto es una aproximación. En PDFs muy irregulares puede acertar o fallar.

### Listas

- Detecta listas simples que empiezan por:
  - `-`
  - `*`
  - `•`
  - `1.`
  - `1)`

### Imágenes

- Las imágenes se extraen página por página.
- La primera imagen extraída se coloca siempre al principio de la nota, después
  del frontmatter, como portada.
- En los preliminares se omiten únicamente banners muy apaisados y bajos; las
  imágenes del cuerpo no se filtran por tamaño.
- Se guardan con nombres del estilo `aniquilacion-xref-123.png`.
- El Markdown las referencia con sintaxis de Obsidian:

```md
![[img/aniquilacion-xref-123.png]]
```

- Si una imagen aparece repetida en el PDF, el script la reutiliza.

### Propiedades de Obsidian

El script intenta rellenar estas propiedades:

- `title`
- `saga`
- `author`
- `translator`
- `publisher`
- `cover`
- `pages`
- `genre`
- `tags`

El script consulta Google Books y Open Library por ISBN o, si el PDF no lo
incluye, por título y autor. Normaliza sus categorías y guarda el resultado en
la caché del usuario. Si la red o los servicios no están disponibles, la
conversión continúa sin `genre`.

Las etiquetas generadas suelen seguir esta idea:

- `autor/jeff-vandermeer`
- `saga/southern-reach`
- `novela/aniquilacion`
- `novela`

El frontmatter también incluye `cssclasses: novela` para poder aplicar estilos CSS solo a estas notas dentro de Obsidian.

### Detección de saga o secuela

El script intenta reconocer expresiones como:

- `trilogía`
- `saga`
- `serie`
- `volumen 2`
- `libro 3`
- `parte 2`
- `secuela de`
- `continuación de`
- `segunda parte de`

Si encuentra una de esas pistas en la primera página o en las páginas iniciales, puede rellenar `saga` y una etiqueta de relación como `saga` o `secuela`.

La segunda página se analiza con prioridad porque en muchos libros ahí aparece la información editorial o de colección.
Si un bloque o párrafo de la primera o segunda página contiene literalmente una pista como `trilogía`, `saga` o `serie`, el script la guarda también en la propiedad `saga`.
La propiedad `saga` se normaliza con capitalización de título para que quede más limpia en Obsidian.

### Portada

- Si la primera página parece una portada, la imagen se coloca al principio del documento.
- La detección de portada es simple:
  - página 1
  - contiene imágenes
  - contiene poco texto

## Estructura de salida

Si conviertes `aniquilacion.pdf`, el resultado esperado es algo parecido a esto:

```text
aniquilacion.md
img/
  aniquilacion-xref-12.png
  aniquilacion-xref-18.jpg
  aniquilacion-xref-22.png
```

## Requisitos

- Python 3
- PyMuPDF (`pymupdf`)

## Notas

- El script está pensado para documentos de texto, especialmente novelas en PDF o EPUB.
- No hace OCR.
- No reconstruye tablas complejas.
- No intenta conservar el diseño exacto del PDF.
- En EPUB conserva el formato textual compatible con Markdown; no reproduce
  fuentes, márgenes, columnas, CSS complejo ni maquetaciones fijas. Las celdas
  combinadas de tablas se aplanan.
- No convierte EPUB con texto o imágenes protegidos mediante DRM.
- Prioriza un Markdown usable dentro de Obsidian.
