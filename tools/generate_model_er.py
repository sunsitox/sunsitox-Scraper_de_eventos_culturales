from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "evento_cultural_modelo.dbml"
DEFAULT_OUTPUT = ROOT / "docs" / "modelo_er.mmd"


@dataclass
class Column:
    name: str
    data_type: str
    attributes: str = ""

    @property
    def is_primary(self) -> bool:
        return bool(re.search(r"(?:^|,)\s*pk(?:\s*,|$)", self.attributes))

    @property
    def is_required(self) -> bool:
        return self.is_primary or "not null" in self.attributes


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    composite_primary_columns: set[str] = field(default_factory=set)


TABLE_RE = re.compile(r"^Table\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
COLUMN_RE = re.compile(
    r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?)"
    r"(?:\s+\[(.*)\])?\s*$"
)
COMPOSITE_PK_RE = re.compile(r"^\s*\(([^)]+)\)\s*\[[^]]*\bpk\b[^]]*\]\s*$")
REF_RE = re.compile(
    r"\bref:\s*([>-])\s*([A-Za-z_][A-Za-z0-9_]*)\."
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
NOTE_RE = re.compile(r"\bnote:\s*'([^']*)'")


def parse_dbml(text: str) -> list[Table]:
    tables: list[Table] = []
    current: Table | None = None
    in_indexes = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        table_match = TABLE_RE.match(line)
        if table_match:
            current = Table(table_match.group(1))
            tables.append(current)
            in_indexes = False
            continue
        if current is None:
            continue
        if line.strip() == "indexes {":
            in_indexes = True
            continue
        if in_indexes:
            composite_match = COMPOSITE_PK_RE.match(line)
            if composite_match:
                current.composite_primary_columns.update(
                    name.strip() for name in composite_match.group(1).split(",")
                )
            if line.strip() == "}":
                in_indexes = False
            continue
        if line.strip() == "}":
            current = None
            continue

        column_match = COLUMN_RE.match(line)
        if column_match:
            current.columns.append(
                Column(
                    name=column_match.group(1),
                    data_type=column_match.group(2),
                    attributes=column_match.group(3) or "",
                )
            )

    if not tables:
        raise ValueError("El DBML no contiene tablas reconocibles.")
    return tables


def mermaid_key(column: Column, table: Table) -> str:
    keys: list[str] = []
    if column.is_primary or column.name in table.composite_primary_columns:
        keys.append("PK")
    if REF_RE.search(column.attributes):
        keys.append("FK")
    if re.search(r"(?:^|,)\s*unique(?:\s*,|$)", column.attributes):
        keys.append("UK")
    return ", ".join(keys)


def mermaid_comment(column: Column) -> str:
    parts: list[str] = []
    note_match = NOTE_RE.search(column.attributes)
    if note_match:
        parts.append(note_match.group(1))
    if "not null" in column.attributes and not column.is_primary:
        parts.append("not null")
    return "; ".join(parts).replace('"', "'")


def relationship_lines(tables: list[Table]) -> list[str]:
    relationships: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for table in tables:
        for column in table.columns:
            ref_match = REF_RE.search(column.attributes)
            if ref_match is None:
                continue
            operator, parent, _parent_column = ref_match.groups()
            relation_key = (parent, table.name, column.name)
            if relation_key in seen:
                continue
            seen.add(relation_key)
            if operator == "-":
                cardinality = "||--||"
            else:
                required = column.is_required or column.name in table.composite_primary_columns
                cardinality = "||--o{" if required else "|o--o{"
            relationships.append(
                f'    {parent} {cardinality} {table.name} : "{column.name}"'
            )
    return relationships


def render_mermaid(tables: list[Table]) -> str:
    lines = [
        "%% AUTO-GENERATED FILE. DO NOT EDIT BY HAND.",
        "%% Source: docs/evento_cultural_modelo.dbml",
        "%% Regenerate: python tools/generate_model_er.py",
        "%%{init: {",
        '  "theme": "base",',
        '  "themeVariables": {',
        '    "background": "#ffffff",',
        '    "primaryColor": "#f7f9fa",',
        '    "primaryTextColor": "#26343b",',
        '    "primaryBorderColor": "#7f9aa5",',
        '    "lineColor": "#506874",',
        '    "secondaryColor": "#eaf2f3",',
        '    "tertiaryColor": "#f5ece8",',
        '    "fontFamily": "Arial",',
        '    "fontSize": "15px"',
        "  },",
        '  "er": {',
        '    "diagramPadding": 24,',
        '    "layoutDirection": "TB",',
        '    "minEntityWidth": 150,',
        '    "minEntityHeight": 42,',
        '    "entityPadding": 12,',
        '    "stroke": "#7f9aa5",',
        '    "fill": "#f7f9fa"',
        "  }",
        "}}%%",
        "erDiagram",
        *relationship_lines(tables),
        "",
    ]

    for table in tables:
        lines.append(f"    {table.name} {{")
        for column in table.columns:
            key = mermaid_key(column, table)
            comment = mermaid_comment(column)
            suffix = f" {key}" if key else ""
            if comment:
                suffix += f' "{comment}"'
            lines.append(f"        {column.data_type} {column.name}{suffix}")
        lines.extend(["    }", ""])
    return "\n".join(lines).rstrip() + "\n"


def generate(source: Path = DEFAULT_SOURCE) -> str:
    return render_mermaid(parse_dbml(source.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera el modelo ER Mermaid desde el DBML versionado."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Falla si el Mermaid versionado no coincide con el DBML.",
    )
    args = parser.parse_args()

    rendered = generate(args.source)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.output} está desactualizado; ejecute "
                "python tools/generate_model_er.py"
            )
            return 1
        print(f"{args.output} está sincronizado con {args.source}.")
        return 0

    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
