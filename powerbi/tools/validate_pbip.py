"""Validate the Power BI Project files.

1. PBIR / PBIP JSON files: validated against the JSON schema named in their `$schema` property (official schemas
   published at developer.microsoft.com/json-schemas/fabric; relative `$ref`s are fetched and cached).
2. TMDL: structural lint (tabs, declarations, partitions, dataType/sourceColumn) and cross-references:
   M partitions vs CSV headers in data/gold, relationships, DAX 'Table'[Column] and [Measure] references,
   and every field used by a report visual. This is NOT Microsoft's TMDL parser: open the project in
   Power BI Desktop for the final check (see BUILD_GUIDE.md).

Usage: python powerbi/tools/validate_pbip.py      (needs internet access for the schemas)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from jsonschema import Draft7Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
PBI = ROOT / "powerbi"
CACHE = PBI / "tools" / ".schema_cache"
errors: list[str] = []


def fetch(url: str) -> dict:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / re.sub(r"[^A-Za-z0-9._-]", "_", url.split("json-schemas/")[-1])
    if not f.exists():
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        f.write_text(r.text, encoding="utf-8")
    return json.loads(f.read_text(encoding="utf-8"))


def retrieve(uri: str) -> Resource:
    return Resource.from_contents(fetch(uri))


def validate_json() -> int:
    registry = Registry(retrieve=retrieve)
    n = 0
    files = [p for p in PBI.rglob("*") if p.suffix in (".json", ".pbir", ".pbism", ".pbip")
             and ".schema_cache" not in p.parts and "StaticResources" not in p.parts and p.name != "theme.json"]
    for f in sorted(files):
        doc = json.loads(f.read_text(encoding="utf-8"))
        url = doc.get("$schema")
        if not url:
            errors.append(f"{f.relative_to(PBI)}: no $schema")
            continue
        schema = fetch(url)
        validator = Draft7Validator(schema, registry=registry.with_resource(url, Resource.from_contents(schema)))
        for e in validator.iter_errors(doc):
            errors.append(f"{f.relative_to(PBI)}: {'/'.join(map(str, e.absolute_path))}: {e.message[:200]}")
        n += 1
    return n


def parse_tmdl() -> tuple[dict, list]:
    """Tables -> {'columns': {name: sourceColumn}, 'measures': {name: dax}, 'calculated': bool}; relationships."""
    tables: dict[str, dict] = {}
    rels = []
    unq = lambda s: s.strip().strip("'").replace("''", "'")  # noqa: E731
    d = PBI / "PleinTarif.SemanticModel" / "definition"
    for f in sorted(d.rglob("*.tmdl")):
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip() and re.match(r"^ +\S", line):
                errors.append(f"{f.name}:{i}: indentation must use tabs")
        if f.parent.name == "tables":
            m = re.search(r"^table (.+)$", text, re.M)
            t = unq(m.group(1))
            info = {"columns": {}, "measures": {}, "calculated": "= calculated" in text, "file": f.name,
                    "partition": bool(re.search(r"^\tpartition ", text, re.M))}
            for block in re.split(r"\n(?=\t(?:column|measure|partition) )", text):
                head = block.lstrip("\t").split("\n", 1)[0]
                if head.startswith("column "):
                    name = unq(head[7:])
                    src = re.search(r"^\t\tsourceColumn: (.+)$", block, re.M)
                    if not re.search(r"^\t\tdataType: ", block, re.M):
                        errors.append(f"{f.name}: column {name} has no dataType")
                    if not src:
                        errors.append(f"{f.name}: column {name} has no sourceColumn")
                    info["columns"][name] = src.group(1) if src else None
                elif head.startswith("measure "):
                    name, _, expr = head[8:].partition("=")
                    body = expr + "\n" + "\n".join(l for l in block.split("\n")[1:] if l.startswith("\t\t\t"))
                    info["measures"][unq(name)] = body
            if not info["partition"]:
                errors.append(f"{f.name}: table {t} has no partition")
            info["m"] = text
            tables[t] = info
        elif f.name == "relationships.tmdl":
            for blk in text.split("relationship ")[1:]:
                fc = re.search(r"fromColumn: (.+)", blk).group(1)
                tc = re.search(r"toColumn: (.+)", blk).group(1)
                rels.append((fc, tc))
    return tables, rels


def split_ref(ref: str) -> tuple[str, str]:
    m = re.match(r"^('(?:[^']|'')+'|[^.]+)\.(.+)$", ref.strip())
    return m.group(1).strip("'").replace("''", "'"), m.group(2).strip("'")


def check_model(tables: dict, rels: list) -> None:
    measures = {m for t in tables.values() for m in t["measures"]}
    gold = ROOT / "data" / "gold"
    for t, info in tables.items():
        csv = re.search(r'DataFolder & "([^"]+)"', info["m"])
        if csv:
            header = (gold / csv.group(1)).read_text(encoding="utf-8").split("\n", 1)[0].split(",")
            for c, src in info["columns"].items():
                if src not in header:
                    errors.append(f"{info['file']}: column {c} (sourceColumn {src}) not in {csv.group(1)}")
            if len(header) != len(info["columns"]):
                errors.append(f"{info['file']}: {len(info['columns'])} columns vs {len(header)} in CSV")
            n_cols = re.search(r"Columns=(\d+)", info["m"])
            if n_cols and int(n_cols.group(1)) != len(header):
                errors.append(f"{info['file']}: Csv.Document Columns={n_cols.group(1)} but CSV has {len(header)}")
    for fc, tc in rels:
        for ref in (fc, tc):
            t, c = split_ref(ref)
            if t not in tables or c not in tables[t]["columns"]:
                errors.append(f"relationship refers to unknown column {ref}")
    for t, info in tables.items():
        for name, dax in info["measures"].items():
            for tbl, c in re.findall(r"('(?:[^']|'')+'|\b[A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]", dax):
                tbl = tbl.strip("'").replace("''", "'")
                if tbl in ("VALUES", "RETURN"):
                    continue
                if tbl not in tables or c not in tables[tbl]["columns"]:
                    errors.append(f"measure [{name}]: unknown column {tbl}[{c}]")
            for ref in re.findall(r"(?<![A-Za-z0-9_'\]])\[([^\]]+)\]", dax):
                if ref not in measures:
                    errors.append(f"measure [{name}]: unknown measure [{ref}]")
            if dax.count("(") != dax.count(")"):
                errors.append(f"measure [{name}]: unbalanced parentheses")


def check_visuals(tables: dict) -> int:
    n = 0
    for f in sorted((PBI / "PleinTarif.Report" / "definition" / "pages").rglob("visual.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for m in re.finditer(r'"(Column|Measure)": \{\s*"Expression": \{\s*"SourceRef": \{\s*"Entity": "([^"]+)"\s*\}\s*\},\s*"Property": "([^"]+)"',
                             json.dumps(doc, indent=1, ensure_ascii=False)):
            kind, ent, prop = m.groups()
            n += 1
            if ent not in tables:
                errors.append(f"{f.parent.name}: unknown table {ent}")
            elif kind == "Column" and prop not in tables[ent]["columns"]:
                errors.append(f"{f.parent.name}: unknown column {ent}[{prop}]")
            elif kind == "Measure" and prop not in tables[ent]["measures"]:
                errors.append(f"{f.parent.name}: unknown measure {ent}[{prop}]")
    return n


def main() -> int:
    n_json = validate_json()
    tables, rels = parse_tmdl()
    check_model(tables, rels)
    n_fields = check_visuals(tables)
    n_meas = sum(len(t["measures"]) for t in tables.values())
    print(f"JSON files validated against official schemas: {n_json}")
    print(f"TMDL tables: {len(tables)}, measures: {n_meas}, relationships: {len(rels)}, visual field references: {n_fields}")
    if errors:
        print(f"\n{len(errors)} problem(s):")
        for e in errors:
            print(" -", e)
        return 1
    print("No problem found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
