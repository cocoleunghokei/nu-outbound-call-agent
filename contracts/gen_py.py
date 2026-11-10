"""Generates backend/app/contracts/generated.py (pydantic v2 models) from the JSON Schemas.

    python gen_py.py <output.py>

Run it with the backend environment (``uv run --project ../backend``). Every schema becomes a
model named after its ``title``. The call-event envelope's per-type ``if/then`` payload binding
cannot be expressed as a model, so ``CallEvent.payload`` is a plain dict; validate the binding
with ``app.contracts.validator_for``.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND_CONFIG = HERE.parent / "backend" / "pyproject.toml"


def schema_files() -> list[Path]:
    return sorted(p for p in HERE.rglob("*.v1.json") if "examples" not in p.relative_to(HERE).parts)


def prepare(node: Any, file: Path) -> Any:
    """Rewrites cross-file $refs to local $defs and drops keywords the generator can't use."""
    if isinstance(node, list):
        return [prepare(item, file) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and not ref.startswith("#"):
        target = (file.parent / ref).resolve()
        title = json.loads(target.read_text(encoding="utf-8"))["title"]
        return {"$ref": f"#/$defs/{title}"}
    cleaned = {
        key: prepare(value, file)
        for key, value in node.items()
        if key not in {"$id", "$schema", "if", "then", "x-open-enum"}
    }
    enum = cleaned.get("enum")
    if isinstance(enum, list) and None in enum:
        # Nullable enum: a string enum (a StrEnum, like every other string enum) or null, so
        # the field is `Enum | None`.
        values = [value for value in enum if value is not None]
        member = {key: cleaned[key] for key in ("title", "description") if key in cleaned}
        member.update({"type": "string", "enum": values})
        rest = {k: v for k, v in cleaned.items() if k not in {"title", "type", "enum"}}
        return {**rest, "anyOf": [member, {"type": "null"}]}
    if "allOf" in cleaned:
        cleaned["allOf"] = [branch for branch in cleaned["allOf"] if branch]
        if not cleaned["allOf"]:
            del cleaned["allOf"]
    return cleaned


def call_event_type() -> dict[str, Any]:
    """The known event types, read from the envelope's if/then branches (as gen-ts.mjs does).

    ``CallEvent.type`` itself stays a plain string, because the envelope is open to unknown
    types; this enum names the known ones for writers.
    """
    envelope = json.loads((HERE / "call-event.v1.json").read_text(encoding="utf-8"))
    types = [branch["if"]["properties"]["type"]["const"] for branch in envelope["allOf"]]
    return {
        "title": "CallEventType",
        "type": "string",
        "enum": types,
        "description": "Known call event types (the call-event.v1.json if/then branches)",
    }


def main() -> None:
    output = Path(sys.argv[1])
    defs: dict[str, Any] = {}
    for file in schema_files():
        schema = json.loads(file.read_text(encoding="utf-8"))
        defs[schema["title"]] = prepare(schema, file)
    defs["CallEventType"] = call_event_type()
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle.json"
        # The titled root becomes one unused RootModel; the title keeps its name stable.
        bundle_doc = {"title": "ContractBundle", "$defs": defs}
        bundle.write_text(json.dumps(bundle_doc), encoding="utf-8")
        subprocess.run(
            [
                sys.executable, "-m", "datamodel_code_generator",
                "--input", str(bundle),
                "--input-file-type", "jsonschema",
                "--output", str(output),
                "--output-model-type", "pydantic_v2.BaseModel",
                "--target-python-version", "3.12",
                "--disable-timestamp",
                "--use-annotated",
                "--field-constraints",
                "--use-schema-description",
                "--use-title-as-name",
                "--formatters", "ruff-format",
            ],
            check=True,
        )  # noqa: S603
    subprocess.run(
        [
            sys.executable, "-m", "ruff", "format",
            "--config", str(BACKEND_CONFIG),
            # Ignore the magic trailing commas the generator's own formatter left behind, so the
            # result does not depend on how it was formatted before.
            "--config", "format.skip-magic-trailing-comma = true",
            str(output),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
