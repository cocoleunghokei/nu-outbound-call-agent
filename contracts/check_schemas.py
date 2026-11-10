"""Checks that every schema in contracts/ is a valid JSON Schema draft 2020-12 document."""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
files = sorted(p for p in HERE.rglob("*.v1.json") if "examples" not in p.relative_to(HERE).parts)
if not files:
    sys.exit("no schemas found")
for file in files:
    Draft202012Validator.check_schema(json.loads(file.read_text(encoding="utf-8")))
print(f"{len(files)} schemas are valid draft 2020-12")
