"""Loads the JSON Schema contracts shipped inside this package.

The schemas in ``schemas/`` are copies of the top-level ``contracts/`` directory, written by
``make -C contracts gen``. Loading only from the package keeps the Railway build, which sees
only ``backend/``, self-contained.
"""

import json
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_SCHEMAS = resources.files(__name__) / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    """Return the schema stored at ``name``, a path like ``payloads/status.changed.v1.json``."""
    node: Traversable = _SCHEMAS
    for part in name.split("/"):
        node = node / part
    schema: dict[str, Any] = json.loads(node.read_text(encoding="utf-8"))
    return schema


def _schema_names(directory: Traversable, prefix: str = "") -> list[str]:
    names: list[str] = []
    for entry in directory.iterdir():
        if entry.is_dir():
            names.extend(_schema_names(entry, f"{prefix}{entry.name}/"))
        elif entry.name.endswith(".json"):
            names.append(f"{prefix}{entry.name}")
    return sorted(names)


@lru_cache(maxsize=1)
def _registry() -> Registry[Any]:
    """Registry of every packaged schema, keyed by its relative name (equal to its ``$id``)."""
    registry: Registry[Any] = Registry()
    for name in _schema_names(_SCHEMAS):
        resource = Resource.from_contents(load_schema(name), default_specification=DRAFT202012)
        registry = registry.with_resource(name, resource)
    return registry


def validator_for(name: str) -> Draft202012Validator:
    """Return a validator for ``name`` that resolves cross-file ``$ref`` and checks formats."""
    return Draft202012Validator(
        load_schema(name), registry=_registry(), format_checker=FormatChecker()
    )
