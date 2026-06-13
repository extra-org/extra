"""Smoke tests proving the package layout and console-script wiring work.

These are packaging checks only — no product behavior is exercised.
"""

from __future__ import annotations


def test_package_imports() -> None:
    import agent_engine as agentplatform

    assert isinstance(agentplatform.__version__, str)
    assert agentplatform.__version__


def test_cli_app_imports() -> None:
    from agent_engine.cli.main import app

    assert app is not None
