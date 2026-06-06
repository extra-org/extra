"""Placeholder CLI entrypoint.

Exposes a single ``version`` command so the packaging/console-script wiring can be
verified. Feature commands (``validate``, ``graph``, ``run``, ``serve``,
``deploy``) are intentionally **not** implemented in this phase; see
``tasks/0008-cli.md``.
"""

from __future__ import annotations

import typer

from agentplatform import __version__

app = typer.Typer(
    name="agentctl",
    help="Declarative AI-agent platform CLI (foundation phase — only `version` works).",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Declarative AI-agent platform CLI (foundation phase).

    Feature commands (validate, graph, run, serve, deploy) are not implemented
    yet — see tasks/0008-cli.md.
    """


@app.command()
def version() -> None:
    """Print the installed agentplatform version."""
    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
