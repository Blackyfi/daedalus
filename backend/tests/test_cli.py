"""Smoke tests for the daedalus CLI surface.

We don't spin up a database here — these tests just verify that the click
group is discoverable, the new `reset-totp` subcommand is wired in with the
right options, and that calling it without `--email` fails fast with a clear
message. End-to-end exercise happens via `make` and a real DB.
"""
from __future__ import annotations

from click.testing import CliRunner

from daedalus.cli.__main__ import cli


def test_cli_help_lists_reset_totp() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert "reset-totp" in result.output


def test_reset_totp_requires_email() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["reset-totp"])
    # click exits non-zero with a "Missing option '--email'" message.
    assert result.exit_code != 0
    assert "--email" in result.output.lower()


def test_reset_totp_help_documents_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["reset-totp", "--help"])
    assert result.exit_code == 0, result.output
    assert "--keep-recovery" in result.output
    assert "--yes" in result.output or "-y" in result.output
    assert "--email" in result.output
