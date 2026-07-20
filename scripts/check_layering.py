#!/usr/bin/env python3
"""
StudyHub - service/route layering guardrail.

Enforces the one dependency rule the service-layer migration depends on:

    routes/student/*  -->  services/*  -->  models.py, extensions.py

i.e. `services/*.py` must never import Flask's `request`/`session`/`g`
objects, and must never import anything from `routes/`. If it needs
something HTTP-specific, that's a signal the function doesn't belong in
the service layer (or needs its HTTP-specific bits passed in as plain
arguments instead).

Run as part of CI (or manually: `python scripts/check_layering.py`).
Exits non-zero and prints every violation found if any exist.

Deliberately dependency-free (stdlib only) so it can run before any other
project dependency is even installed.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVICES_DIR = Path("services")

# Flask symbols that are HTTP-request-scoped and therefore not allowed to be
# imported inside services/*.py. `current_app` is intentionally NOT in this
# list — reading config values via `current_app.config` is a reasonable,
# already-established pattern for services that need a config value (e.g.
# a cache TTL or a feature flag) without needing the full request context.
DISALLOWED_FLASK_IMPORTS = {"request", "session", "g", "jsonify", "abort"}


def _iter_service_files():
    if not SERVICES_DIR.is_dir():
        return
    yield from SERVICES_DIR.rglob("*.py")


def _check_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: SyntaxError while parsing for layering check: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("routes"):
                violations.append(
                    f"{path}:{node.lineno}: services/ must not import from '{module}' "
                    f"(routes/ depends on services/, never the other way around)"
                )
            if module == "flask":
                imported_names = {alias.name for alias in node.names}
                bad = imported_names & DISALLOWED_FLASK_IMPORTS
                if bad:
                    violations.append(
                        f"{path}:{node.lineno}: services/ must not import "
                        f"request-scoped Flask symbols {sorted(bad)} — pass the "
                        f"needed values in as plain function arguments instead"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("routes"):
                    violations.append(
                        f"{path}:{node.lineno}: services/ must not import '{alias.name}'"
                    )
    return violations


def main() -> int:
    all_violations: list[str] = []
    for path in _iter_service_files():
        all_violations.extend(_check_file(path))

    if all_violations:
        print("Service/route layering violations found:\n")
        for v in all_violations:
            print(f"  - {v}")
        print(f"\n{len(all_violations)} violation(s). See services/*.py layering rule "
              f"in the architecture refactor docs.")
        return 1

    print("Layering check passed — no services/* file imports routes/ or "
          "request-scoped Flask symbols.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
