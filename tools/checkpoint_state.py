"""Genera una copia SQLite consistente, incluso tras interrupción de una fase."""

import argparse
import os
from pathlib import Path
import sqlite3


def snapshot(source: Path, destination: Path):
    if not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    reader = sqlite3.connect(source)
    writer = sqlite3.connect(temporary)
    try:
        reader.backup(writer)
        if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Checkpoint SQLite inconsistente")
    finally:
        writer.close()
        reader.close()
    os.replace(temporary, destination)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("state/pipeline.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("state-export/pipeline.sqlite3"))
    args = parser.parse_args()
    snapshot(args.source, args.output)
