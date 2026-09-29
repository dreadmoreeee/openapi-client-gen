"""Shared fixtures: import path, loading generated modules, the local mock server."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_counter = itertools.count()


def import_source(source: str, directory: Path, stem: str = "client"):
    """Write ``source`` to ``directory`` and import it under a unique name."""
    name = f"_generated_{stem}_{next(_counter)}"
    path = directory / f"{name}.py"
    path.write_text(source, encoding="ascii", newline="\n")
    return _import_path(name, path)


def _import_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look the module up while building classes
    spec.loader.exec_module(module)
    return module


# Loaded by path so that no other project's "examples" package can shadow it.
petstore_mock = _import_path("_openapi_client_gen_petstore_mock", ROOT / "examples" / "petstore_mock.py")


@pytest.fixture
def load_generated(tmp_path):
    names_before = set(sys.modules)
    yield lambda source, stem="client": import_source(source, tmp_path, stem)
    for name in set(sys.modules) - names_before:
        if name.startswith("_generated_"):
            del sys.modules[name]


@pytest.fixture
def petstore_server():
    """The mock Petstore on a free local port; yields ``(base_url, state)``."""
    with petstore_mock.serve() as running:
        yield running
