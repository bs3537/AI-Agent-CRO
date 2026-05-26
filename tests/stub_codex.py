#!/usr/bin/env python3
"""Stub `codex` CLI for tests.

Mimics the bits of `codex exec` the CodexProvider uses:
  - reads (and discards) the prompt on stdin
  - if `--output-schema <s> -o <out>` are present, generates a JSON object that
    satisfies the schema (honoring per-property type + min/max) and writes it to <out>
  - otherwise prints a canned paragraph to stdout (the free-text path)

Point the provider at it with SMA_CODEX_BIN=<path-to-this-file>.
"""
import json
import sys
from pathlib import Path


# Produce a value satisfying one JSON-Schema property spec.
def _value_for(spec: dict):
    t = spec.get("type")
    if t == "number":
        lo, hi = spec.get("minimum", 0.0), spec.get("maximum", 10.0)
        return round((lo + hi) / 2, 2)
    if t == "integer":
        lo, hi = spec.get("minimum", 1), spec.get("maximum", 5)
        return int((lo + hi) // 2)
    if t == "string":
        return "stub rationale line one\nline two\nline three"
    if t == "array":
        return []
    if t == "object":
        return _object_for(spec)
    return None


# Build an object satisfying a schema's properties.
def _object_for(schema: dict) -> dict:
    props = schema.get("properties", {})
    return {name: _value_for(spec) for name, spec in props.items()}


def main(argv: list[str]) -> int:
    # Drain stdin (the prompt) so the parent's write side closes cleanly.
    try:
        sys.stdin.read()
    except Exception:
        pass
    schema_path = out_path = None
    for i, a in enumerate(argv):
        if a == "--output-schema" and i + 1 < len(argv):
            schema_path = argv[i + 1]
        elif a == "-o" and i + 1 < len(argv):
            out_path = argv[i + 1]
    if schema_path and out_path:
        schema = json.loads(Path(schema_path).read_text())
        Path(out_path).write_text(json.dumps(_object_for(schema)))
        print("done")
    else:
        print("Stub synthesis paragraph: one neutral sentence about today's events.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
