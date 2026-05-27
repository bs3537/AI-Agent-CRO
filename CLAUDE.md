# Project rules for Claude Code

## Comment style (applies to ALL code in this repo)

When writing or editing code in this project, add a leading comment
before every key code chunk explaining what it does. This overrides
Claude's default "minimal comments" preference for this repo only.

A "key code chunk" means:

- Each function or method definition (one short comment above the `def`)
- Each class definition (one short comment above the `class`)
- Each top-level constants block or configuration dict
- Each module-level schema string (e.g., `CREATE TABLE …` strings)

Do NOT add a comment before every line inside a function — only the
key chunks above. Comments should describe WHAT the block does in
plain English, not how Python works.

Keep comments concise (one short sentence is usually enough; two if
the block has a non-obvious reason to exist). Preserve existing
docstrings and module-level header comments.

Apply this to every file you touch — new code AND edits to existing
code.

## Reference docs

- `pl.md` — working plan + progress tracker (workstreams W1–W8)
- `schema.md` — architecture diagrams, module map, pydantic + SQL schemas
