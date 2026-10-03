from __future__ import annotations

import os
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "DATABASE.md"
MERMAID_SOURCE = ROOT / "docs" / "modelo_datos_visual.mmd"
OUTPUT = Path(
    os.environ.get(
        "DB_DOC_OUTPUT",
        ROOT / "docs" / "Documentacion_Base_de_Datos_Eventos_Culturales.docx",
    )
)
DIAGRAM = ROOT / "docs" / "assets" / "database_relationships.png"

NAVY = "17324D"
BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
MUTED = "5E6B78"
LIGHT_BLUE = "E8EEF5"
BORDER = "C9D3DE"
WHITE = "FFFFFF"
GOLD = "B8871B"

# Preset: compact_reference_guide.
# Named overrides: editorial_cover; technical_table_body (9 pt);
# code_block (Consolas 8.5 pt); relationship_figure.


def strip_inline_markdown(value: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", value).replace("**", "").strip()


def extract_control_fields(text: str) -> dict[str, str]:
    """Read cover/control metadata from DATABASE.md instead of duplicating it here."""
    fields: dict[str, str] = {}
    in_control = False
    for line in text.splitlines():
        if line.startswith("## 0. Control documental"):
            in_control = True
            continue
        if in_control and line.startswith("## "):
            break
        if not in_control or not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in {"Campo", "---"}:
            continue
        fields[strip_inline_markdown(cells[0])] = strip_inline_markdown(cells[1])
    return fields


def extract_migration_ids(text: str) -> list[str]:
    """Return the ordered migration identifiers declared by DATABASE.md."""
    match = re.search(
        r"### Migraciones de referencia\s*(.*?)(?=\n### |\n## |\Z)",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        return []
    return re.findall(
        r"supabase/migrations/(\d{14})_[^`\s]+\.sql",
        match.group(1),
    )


def validate_source_contract(text: str, fields: dict[str, str]) -> None:
    required_fields = {
        "Tipo de documento",
        "Título de portada",
        "Proyecto",
        "Subtítulo",
        "Versión documentada",
        "Esquema",
        "Uso",
        "Encabezado",
        "Pie de página",
        "Título del índice",
        "Leyenda de figura 1",
    }
    missing = sorted(required_fields - fields.keys())
    if missing:
        raise ValueError(
            "DATABASE.md no define los campos de control requeridos: "
            + ", ".join(missing)
        )

    if not extract_migration_ids(text):
        raise ValueError(
            "DATABASE.md no declara migraciones bajo "
            "'### Migraciones de referencia'."
        )

    match = re.search(r"```mermaid\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if match is None:
        raise ValueError("DATABASE.md no contiene un bloque Mermaid.")
    if not MERMAID_SOURCE.exists():
        raise FileNotFoundError(f"Falta la fuente Mermaid: {MERMAID_SOURCE}")
    markdown_mermaid = match.group(1).strip().replace("\r\n", "\n")
    canonical_mermaid = MERMAID_SOURCE.read_text(encoding="utf-8").strip().replace("\r\n", "\n")
    if markdown_mermaid != canonical_mermaid:
        raise ValueError(
            "El bloque Mermaid de DATABASE.md no coincide exactamente con "
            "docs/modelo_datos_visual.mmd."
        )


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for name, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{name}"))
        if node is None:
            node = OxmlElement(f"w:{name}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_cell_width(cell, width_dxa: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(width_dxa))
    tc_w.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def set_table_geometry(table, widths: list[int]) -> None:
    total = sum(widths)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(total))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), "120")
    tbl_ind.set(qn("w:type"), "dxa")
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")

    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        prevent_row_split(row)
        for index, cell in enumerate(row.cells):
            set_cell_width(cell, widths[index])
            set_cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def set_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "4")
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), BORDER)
        borders.append(node)


def set_run_font(run, name="Calibri", size=None, color=None, bold=None, italic=None) -> None:
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def add_hyperlink(paragraph, text: str, url: str) -> None:
    part = paragraph.part
    rel_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), BLUE)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.extend([color, underline])
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.extend([props, text_node])
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


INLINE_PATTERN = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def add_inline(paragraph, text: str, *, size: float | None = None, color: str | None = None) -> None:
    position = 0
    for match in INLINE_PATTERN.finditer(text):
        if match.start() > position:
            run = paragraph.add_run(text[position : match.start()])
            set_run_font(run, size=size, color=color)
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            set_run_font(run, size=size, color=color, bold=True)
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            set_run_font(run, name="Consolas", size=(size or 10) - 0.3, color=DARK_BLUE)
            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), "EEF2F6")
            run._element.get_or_add_rPr().append(shading)
        else:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token).groups()
            add_hyperlink(paragraph, label, url)
        position = match.end()
    if position < len(text):
        run = paragraph.add_run(text[position:])
        set_run_font(run, size=size, color=color)


def add_field(paragraph, instruction: str) -> None:
    run = paragraph.add_run()
    set_run_font(run, size=8.5, color=MUTED)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instr, separate, text, end])


def add_numbering_definition(document: Document, kind: str) -> int:
    numbering = document.part.numbering_part.element
    abstract_ids = [int(x.get(qn("w:abstractNumId"))) for x in numbering.findall(qn("w:abstractNum"))]
    num_ids = [int(x.get(qn("w:numId"))) for x in numbering.findall(qn("w:num"))]
    abstract_id = max(abstract_ids, default=-1) + 1
    num_id = max(num_ids, default=0) + 1

    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "singleLevel")
    abstract.append(multi)
    lvl = OxmlElement("w:lvl")
    lvl.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    fmt = OxmlElement("w:numFmt")
    fmt.set(qn("w:val"), "bullet" if kind == "bullet" else "decimal")
    lvl_text = OxmlElement("w:lvlText")
    lvl_text.set(qn("w:val"), "•" if kind == "bullet" else "%1.")
    justification = OxmlElement("w:lvlJc")
    justification.set(qn("w:val"), "left")
    p_pr = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "num")
    tab.set(qn("w:pos"), "540")
    tabs.append(tab)
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), "540")
    indent.set(qn("w:hanging"), "270")
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:after"), "80")
    spacing.set(qn("w:line"), "300")
    spacing.set(qn("w:lineRule"), "auto")
    p_pr.extend([tabs, indent, spacing])
    lvl.extend([start, fmt, lvl_text, justification, p_pr])
    abstract.append(lvl)
    numbering.append(abstract)

    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), str(abstract_id))
    num.append(abstract_ref)
    numbering.append(num)
    return num_id


def apply_numbering(paragraph, num_id: int) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "0")
    num_id_element = OxmlElement("w:numId")
    num_id_element.set(qn("w:val"), str(num_id))
    num_pr.extend([ilvl, num_id_element])
    p_pr.append(num_pr)


def configure_styles(doc: Document) -> None:
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    normal.font.size = Pt(11)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25

    settings = {
        "Title": (30, NAVY, 0, 8),
        "Subtitle": (14, MUTED, 0, 10),
        "Heading 1": (16, BLUE, 18, 10),
        "Heading 2": (13, BLUE, 14, 7),
        "Heading 3": (12, DARK_BLUE, 10, 5),
    }
    for name, (size, color, before, after) in settings.items():
        style = styles[name]
        style.font.name = "Calibri"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True


def configure_section(doc: Document, fields: dict[str, str]) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.right_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)
    section.different_first_page_header_footer = True

    header = section.header
    p = header.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(fields.get("Encabezado", "EVENTO CULTURAL · DOCUMENTACIÓN 2026"))
    set_run_font(run, size=8.5, color=MUTED, bold=True)

    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_before = Pt(0)
    run = p.add_run(fields.get("Pie de página", "Uso personal · Página") + " ")
    set_run_font(run, size=8.5, color=MUTED)
    add_field(p, "PAGE")


def add_cover(
    doc: Document,
    fields: dict[str, str],
    migration_ids: list[str],
) -> None:
    for _ in range(4):
        doc.add_paragraph()
    kicker = doc.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.space_after = Pt(18)
    run = kicker.add_run(fields.get("Tipo de documento", "Documentación técnica").upper())
    set_run_font(run, size=10.5, color=GOLD, bold=True)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cover_title = fields.get("Título de portada", "Base de datos de eventos culturales")
    title.add_run(cover_title.replace(" de eventos", " de\neventos", 1))

    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(fields.get("Subtítulo", "Diccionario de tablas, columnas, relaciones y categorías"))

    line = doc.add_paragraph()
    line.paragraph_format.space_before = Pt(18)
    line.paragraph_format.space_after = Pt(52)
    p_pr = line._p.get_or_add_pPr()
    p_borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "10")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), BLUE)
    p_borders.append(bottom)
    p_pr.append(p_borders)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.paragraph_format.space_after = Pt(5)
    run = meta.add_run(fields.get("Proyecto", "MVP · Agregador de eventos culturales de Chile"))
    set_run_font(run, size=11, color=NAVY, bold=True)
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.paragraph_format.space_after = Pt(5)
    run = meta.add_run(f"Versión documentada: {fields.get('Versión documentada', 'sin especificar')}")
    set_run_font(run, size=10.5, color=MUTED)
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.paragraph_format.space_after = Pt(5)
    run = meta.add_run("Migraciones: " + " · ".join(migration_ids))
    set_run_font(run, size=9.2, color=MUTED)
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(fields.get("Uso", "Documento de uso personal"))
    set_run_font(run, size=10.5, color=MUTED, italic=True)
    doc.add_page_break()


def add_contents(doc: Document, headings: list[str], fields: dict[str, str]) -> None:
    doc.add_heading(fields.get("Título del índice", "Contenido"), level=1)
    for index, heading in enumerate(headings, start=1):
        numbered = re.match(r"^(\d+)\.\s*(.+)$", heading)
        number = int(numbered.group(1)) if numbered else index
        heading = numbered.group(2) if numbered else heading
        heading = strip_inline_markdown(heading)
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.15)
        p.paragraph_format.space_after = Pt(5)
        run = p.add_run(f"{number:02d}")
        set_run_font(run, size=9.5, color=GOLD, bold=True)
        run = p.add_run(f"   {heading}")
        set_run_font(run, size=11, color=NAVY, bold=True)
    doc.add_page_break()


def parse_markdown_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return [rows[0], *rows[2:]]


def choose_widths(headers: list[str]) -> list[int]:
    count = len(headers)
    normalized = [h.lower() for h in headers]
    if count == 4 and "columna" in normalized:
        return [1800, 1400, 1800, 4360]
    if count == 3 and "eventos observados" in normalized:
        return [2500, 1100, 5760]
    if count == 3:
        return [2100, 1500, 5760]
    if count == 2:
        return [2700, 6660]
    return [9360 // count] * count


def add_table(doc: Document, rows: list[list[str]]) -> None:
    headers = rows[0]
    table = doc.add_table(rows=len(rows), cols=len(headers))
    set_table_geometry(table, choose_widths(headers))
    set_table_borders(table)
    set_repeat_table_header(table.rows[0])
    for row_index, values in enumerate(rows):
        for col_index, value in enumerate(values):
            cell = table.cell(row_index, col_index)
            if row_index == 0:
                set_cell_shading(cell, LIGHT_BLUE)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.08
            if len(headers) == 3 and col_index == 1:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_inline(paragraph, value, size=9, color=NAVY if row_index == 0 else None)
            for run in paragraph.runs:
                if row_index == 0:
                    run.bold = True
    # Do not leave a table header isolated at the foot of a page.
    for cell in table.rows[0].cells:
        for paragraph in cell.paragraphs:
            paragraph.paragraph_format.keep_with_next = True
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)


def add_code_block(doc: Document, lines: list[str]) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.15)
    paragraph.paragraph_format.right_indent = Inches(0.15)
    paragraph.paragraph_format.space_before = Pt(3)
    paragraph.paragraph_format.space_after = Pt(8)
    paragraph.paragraph_format.line_spacing = 1.05
    p_pr = paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), "F1F3F5")
    p_pr.append(shading)
    for index, line in enumerate(lines):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(line)
        set_run_font(run, name="Consolas", size=8.5, color=NAVY)


def add_callout(doc: Document, text: str) -> None:
    """Render a Markdown blockquote as a single, readable callout."""
    table = doc.add_table(rows=1, cols=1)
    set_table_geometry(table, [9360])
    set_table_borders(table)
    cell = table.cell(0, 0)
    set_cell_shading(cell, "EEF4F8")
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.paragraph_format.line_spacing = 1.15
    add_inline(paragraph, text, size=9.5, color=NAVY)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)


def starts_markdown_block(value: str) -> bool:
    stripped = value.strip()
    return bool(
        not stripped
        or stripped.startswith(("```", "## ", "### ", "#### ", "| ", ">"))
        or re.match(r"^- ", stripped)
        or re.match(r"^\d+\. ", stripped)
    )


def collect_wrapped_lines(lines: list[str], index: int) -> tuple[str, int]:
    """Join soft-wrapped Markdown lines into one semantic paragraph or list item."""
    fragments = [lines[index].strip()]
    index += 1
    while index < len(lines) and not starts_markdown_block(lines[index]):
        fragments.append(lines[index].strip())
        index += 1
    return " ".join(fragment for fragment in fragments if fragment), index


def add_relationship_figure(doc: Document, fields: dict[str, str]) -> None:
    if not DIAGRAM.exists():
        raise FileNotFoundError(
            f"Falta el render Mermaid versionado: {DIAGRAM}. "
            "Genérelo desde docs/modelo_datos_visual.mmd antes de crear el Word."
        )
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(4)
    run = paragraph.add_run()
    inline = run.add_picture(str(DIAGRAM), width=Inches(6.25))
    inline._inline.docPr.set("descr", "Vista por capas del modelo de datos: ingesta, catálogo cultural, aplicación y usuarios.")
    caption = doc.add_paragraph(
        fields.get("Leyenda de figura 1", "Figura 1. Vista por capas del modelo de datos vigente.")
    )
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption.paragraph_format.space_before = Pt(0)
    caption.paragraph_format.space_after = Pt(10)
    for run in caption.runs:
        set_run_font(run, size=9, color=MUTED, italic=True)


def build_document() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    source_lines = text.splitlines()
    level_two_headings = [line[3:].strip() for line in source_lines if line.startswith("## ")]
    control_fields = extract_control_fields(text)
    migration_ids = extract_migration_ids(text)
    validate_source_contract(text, control_fields)

    doc = Document()
    configure_styles(doc)
    configure_section(doc, control_fields)
    doc.core_properties.title = control_fields.get("Título de portada", "Base de datos de eventos culturales")
    doc.core_properties.subject = control_fields.get("Subtítulo", "Diccionario de tablas, columnas, relaciones y categorías")
    doc.core_properties.author = ""
    doc.core_properties.last_modified_by = ""
    doc.core_properties.keywords = "Supabase, PostgreSQL, eventos culturales, diccionario de datos"

    bullet_num_id = add_numbering_definition(doc, "bullet")
    decimal_num_id = add_numbering_definition(doc, "decimal")
    add_cover(doc, control_fields, migration_ids)
    add_contents(doc, level_two_headings, control_fields)

    lines = source_lines[1:]
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            block = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            if language == "mermaid":
                add_relationship_figure(doc, control_fields)
            else:
                add_code_block(doc, block)
            index += 1
            continue

        if stripped.startswith("## "):
            doc.add_heading(strip_inline_markdown(stripped[3:]), level=1)
        elif stripped.startswith("### "):
            doc.add_heading(strip_inline_markdown(stripped[4:]), level=2)
        elif stripped.startswith("#### "):
            doc.add_heading(strip_inline_markdown(stripped[5:]), level=3)
        elif stripped.startswith("| "):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            add_table(doc, parse_markdown_table(table_lines))
            continue
        elif re.match(r"^- ", stripped):
            item, index = collect_wrapped_lines(lines, index)
            paragraph = doc.add_paragraph()
            apply_numbering(paragraph, bullet_num_id)
            add_inline(paragraph, item[2:])
            continue
        elif re.match(r"^\d+\. ", stripped):
            item, index = collect_wrapped_lines(lines, index)
            paragraph = doc.add_paragraph()
            apply_numbering(paragraph, decimal_num_id)
            add_inline(paragraph, re.sub(r"^\d+\. ", "", item))
            continue
        elif stripped.startswith(">"):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip("> "))
                index += 1
            add_callout(doc, " ".join(quote_lines))
            continue
        elif stripped:
            paragraph_text, index = collect_wrapped_lines(lines, index)
            paragraph = doc.add_paragraph()
            add_inline(paragraph, paragraph_text)
            continue
        index += 1

    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
