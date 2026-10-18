"""Unit: the relay protocol imports nothing from the rest of cuttlefish (ADR-0037).

It is meant to move to its own repository one day, and a module that quietly depended on `fleet`
or `secrets` would make that a tangle. Every import in the package must be the standard library,
`cryptography`, or the package itself.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).parents[3] / "src" / "cuttlefish" / "relay" / "protocol"
ALLOWED_THIRD_PARTY = {"cryptography"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            found.add("." * node.level + (node.module or ""))
    return found


def test_the_package_exists_and_has_modules() -> None:
    assert len(list(PACKAGE.glob("*.py"))) >= 4


def test_every_import_is_standard_library_cryptography_or_the_package_itself() -> None:
    stdlib = set(sys.stdlib_module_names)
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        for name in sorted(_imports(path)):
            top = name.split(".")[0]
            ok = (
                top in stdlib
                or top in ALLOWED_THIRD_PARTY
                or name == "cuttlefish.relay.protocol"
                or name.startswith("cuttlefish.relay.protocol.")
                or name == "cuttlefish.relay"  # the package's own docstring-only parent
            )
            if not ok:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_the_rules_would_catch_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from cuttlefish.fleet import daemon\nimport cuttlefish.secrets\n")
    assert _imports(bad) == {"cuttlefish.fleet", "cuttlefish.secrets"}


RELAY = PACKAGE.parent
#: The relay server and the connector may use web libraries, but may not reach into the rest of
#: cuttlefish: the relay holds no project data (ADR-0037), and the connector is given an ASGI app
#: instead of importing the fleet.
SERVICE_MODULES = ("server.py", "connector.py", "accounts.py", "limits.py")
SERVICE_THIRD_PARTY = {"cryptography", "fastapi", "pydantic", "starlette", "uvicorn", "websockets"}


def test_the_relay_server_and_connector_import_nothing_from_the_rest_of_cuttlefish() -> None:
    stdlib = set(sys.stdlib_module_names)
    offenders: list[str] = []
    for module in SERVICE_MODULES:
        for name in sorted(_imports(RELAY / module)):
            top = name.split(".")[0]
            ok = (
                top in stdlib
                or top in SERVICE_THIRD_PARTY
                or name == "cuttlefish.relay"
                or name.startswith("cuttlefish.relay.")
            )
            if not ok:
                offenders.append(f"{module}: {name}")
    assert offenders == []
