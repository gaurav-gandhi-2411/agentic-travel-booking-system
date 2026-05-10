"""Smoke tests for the eval CLI entrypoint."""
from __future__ import annotations

import pytest


def test_run_raises_not_implemented() -> None:
    """run.main() raises NotImplementedError until Phase 3.5 implementation."""
    from evals.run import main

    with pytest.raises(NotImplementedError, match="Phase 3.5"):
        main()


def test_run_module_importable() -> None:
    """evals.run is importable without side effects."""
    import importlib

    importlib.import_module("evals.run")


def test_lib_importable() -> None:
    """evals.lib submodules are importable."""
    import importlib

    for mod in ("evals.lib", "evals.lib.runner", "evals.lib.scorer", "evals.lib.judge"):
        importlib.import_module(mod)
