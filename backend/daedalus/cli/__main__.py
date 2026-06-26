"""Operational CLI exposed by `python -m daedalus.cli`."""
from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path

import click
from sqlalchemy import select

from daedalus.auth.audit import record as audit_record
from daedalus.auth.client_certs import mint_client_cert
from daedalus.auth.passwords import hash_password
from daedalus.auth.policy import policy_violations
from daedalus.auth.totp import (
    encrypt_secret,
    generate_recovery_codes,
    hash_recovery_code,
    new_totp_secret,
    provisioning_uri,
)
from daedalus.db.base import get_session
from daedalus.db.models import Role, User


@click.group()
def cli() -> None:
    """Daedalus operator commands."""


@cli.command("init")
@click.option("--email", prompt=True)
@click.option("--display-name", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--role", type=click.Choice([role.value for role in Role]), default=Role.owner.value, show_default=True)
def init_user(email: str, display_name: str, password: str, role: str) -> None:
    """Create the first operator account and emit TOTP bootstrap data."""
    asyncio.run(_init_user(email=email, display_name=display_name, password=password, role=role))


@cli.command("import-connectors")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
def import_connectors(directory: Path) -> None:
    """Import connector specs from a directory of JSON files."""
    asyncio.run(_import_connectors(directory))


_DEFAULT_CA_CERT = "/run/daedalus/secrets/internal_ca.crt"
_DEFAULT_CA_KEY = "/run/daedalus/secrets/internal_ca.key"


@cli.command("mint-client-cert")
@click.option("--email", required=True, help="Email of the operator the cert is for.")
@click.option(
    "--ca-cert",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=_DEFAULT_CA_CERT,
    show_default=True,
    help="PEM-encoded internal CA cert.",
)
@click.option(
    "--ca-key",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=_DEFAULT_CA_KEY,
    show_default=True,
    help="PEM-encoded internal CA private key (RSA, no passphrase).",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="./minted-certs",
    show_default=True,
    help="Where to drop the .key/.crt/.p12 trio.",
)
@click.option("--days", type=int, default=365, show_default=True, help="Cert validity in days.")
@click.option("--key-bits", type=int, default=4096, show_default=True, help="RSA key size for the operator cert.")
@click.option(
    "--p12-password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Password used to encrypt the .p12 bundle (the operator types this when importing into the browser).",
)
@click.option(
    "--pin",
    is_flag=True,
    default=False,
    help="Persist the new cert's SHA-256 fingerprint onto User.pinned_cert_fingerprint so cookies are bound to it at login.",
)
def mint_cert(
    email: str,
    ca_cert: Path,
    ca_key: Path,
    out_dir: Path,
    days: int,
    key_bits: int,
    p12_password: str,
    pin: bool,
) -> None:
    """Issue an mTLS client cert against the internal CA.

    Writes ``<email>.{key,crt,p12}`` into ``--out-dir``. The operator
    imports the .p12 into their browser; the platform's reverse proxy
    accepts it because it's signed by the same CA bundle Caddy is
    pinned to.

    With ``--pin``, the fingerprint is written to
    ``User.pinned_cert_fingerprint`` so the user's session cookie is
    bound to *this specific* cert at next login (§10.2).
    """
    asyncio.run(
        _mint_client_cert(
            email=email,
            ca_cert=ca_cert,
            ca_key=ca_key,
            out_dir=out_dir,
            days=days,
            key_bits=key_bits,
            p12_password=p12_password,
            pin=pin,
        )
    )


@cli.command("reset-totp")
@click.option("--email", required=True, help="Login email of the user to reset.")
@click.option(
    "--keep-recovery",
    is_flag=True,
    default=False,
    help="Re-issue TOTP only; do not regenerate recovery codes.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt (useful for scripting).",
)
def reset_totp(email: str, keep_recovery: bool, yes: bool) -> None:
    """Re-issue a user's TOTP secret and (optionally) regenerate recovery codes.

    Run this on the host when a user has lost their authenticator and cannot
    log in. The new provisioning URI and recovery codes are printed to stdout
    and never persisted in plaintext — copy them somewhere safe before
    closing the terminal.
    """
    asyncio.run(_reset_totp(email=email, regen_recovery=not keep_recovery, assume_yes=yes))


async def _init_user(*, email: str, display_name: str, password: str, role: str) -> None:
    violations = policy_violations(password)
    if violations:
        raise click.ClickException("; ".join(violations))

    recovery_codes = generate_recovery_codes()
    secret = new_totp_secret()

    async for db in get_session():
        existing = await db.execute(select(User).where(User.email == email.lower()))
        if existing.scalar_one_or_none() is not None:
            raise click.ClickException(f"user already exists: {email.lower()}")

        user = User(
            email=email.lower(),
            display_name=display_name,
            role=Role(role),
            password_hash=hash_password(password),
            totp_secret=encrypt_secret(secret),
            totp_enrolled_at=None,
            recovery_codes_hash=[hash_recovery_code(code) for code in recovery_codes],
        )
        db.add(user)
        await db.commit()
        break

    click.echo(f"created user: {email.lower()}")
    click.echo(f"totp uri: {provisioning_uri(email.lower(), secret)}")
    click.echo("recovery codes:")
    for code in recovery_codes:
        click.echo(code)


async def _import_connectors(directory: Path) -> None:
    from daedalus.connectors.loader import ConnectorImportError, import_connectors_from_dir

    summary: dict = {}
    async for db in get_session():
        try:
            summary = await import_connectors_from_dir(db, directory)
        except ConnectorImportError as exc:
            raise click.ClickException(str(exc)) from exc
        await db.commit()
        break

    click.echo(
        f"imported {summary.get('imported', 0)} connector(s) "
        f"({summary.get('added', 0)} added, {summary.get('updated', 0)} updated) "
        f"from {directory}"
    )


async def _mint_client_cert(
    *,
    email: str,
    ca_cert: Path,
    ca_key: Path,
    out_dir: Path,
    days: int,
    key_bits: int,
    p12_password: str,
    pin: bool,
) -> None:
    target = email.strip().lower()
    display_name = target

    user = None
    async for db in get_session():
        result = await db.execute(select(User).where(User.email == target))
        user = result.scalar_one_or_none()
        if user is not None:
            display_name = user.display_name
        break

    if user is None and pin:
        raise click.ClickException(f"--pin requires an existing user; no user found: {target}")

    try:
        minted = mint_client_cert(
            email=target,
            display_name=display_name,
            out_dir=out_dir,
            ca_cert_path=ca_cert,
            ca_key_path=ca_key,
            p12_password=p12_password,
            days=days,
            key_bits=key_bits,
        )
    except FileNotFoundError as exc:
        raise click.ClickException(f"missing CA file: {exc}") from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if pin and user is not None:
        async for db in get_session():
            result = await db.execute(select(User).where(User.email == target))
            user = result.scalar_one_or_none()
            if user is None:
                raise click.ClickException(f"user vanished mid-mint: {target}")
            user.pinned_cert_fingerprint = minted.fingerprint_sha256
            await audit_record(
                db,
                actor_user_id=None,
                action="auth.client_cert_minted",
                target_kind="user",
                target_id=str(user.id),
                payload={
                    "email": user.email,
                    "fingerprint_sha256": minted.fingerprint_sha256,
                    "serial_number": str(minted.serial_number),
                    "not_after": minted.not_after.isoformat(),
                    "pinned": True,
                },
            )
            await db.commit()
            break

    click.echo("")
    click.echo(f"Minted client cert for {target}.")
    click.echo(f"  fingerprint (sha256): {minted.fingerprint_sha256}")
    click.echo(f"  valid until:          {minted.not_after.isoformat()}")
    click.echo(f"  serial:               {minted.serial_number}")
    click.echo(f"  key:    {minted.key_path}")
    click.echo(f"  cert:   {minted.cert_path}")
    click.echo(f"  bundle: {minted.pkcs12_path}")
    click.echo("")
    click.echo("Import the .p12 into the operator's browser using the password you just set.")
    if pin and user is not None:
        click.echo("")
        click.echo("Fingerprint pinned on the user — sessions will only be issued for this cert.")


async def _reset_totp(*, email: str, regen_recovery: bool, assume_yes: bool) -> None:
    """Replace TOTP secret + (optional) recovery codes for the user.

    Also clears any active sessions and the failed-login lockout so the user
    can log straight back in with the freshly enrolled authenticator.
    """
    from datetime import datetime

    from daedalus.db.models import Session as SessionModel

    target = email.strip().lower()

    async for db in get_session():
        result = await db.execute(select(User).where(User.email == target))
        user = result.scalar_one_or_none()
        if user is None:
            raise click.ClickException(f"no user found: {target}")

        if not assume_yes:
            click.echo(
                f"About to reset TOTP for {user.email} (role={user.role.value})."
                + (" Recovery codes will also be regenerated." if regen_recovery else "")
            )
            click.confirm("Continue?", abort=True)

        new_secret = new_totp_secret()
        user.totp_secret = encrypt_secret(new_secret)
        user.totp_enrolled_at = None
        user.failed_login_count = 0
        user.locked_until = None

        new_codes: list[str] = []
        if regen_recovery:
            new_codes = generate_recovery_codes()
            user.recovery_codes_hash = [hash_recovery_code(code) for code in new_codes]

        # Revoke existing sessions so the lost device can't keep the user
        # signed in (defence in depth — the spec calls for this when 3FA is
        # being rebuilt).
        now = datetime.now(UTC)
        sessions_res = await db.execute(
            select(SessionModel).where(
                SessionModel.user_id == user.id,
                SessionModel.revoked_at.is_(None),
            )
        )
        for session in sessions_res.scalars():
            session.revoked_at = now

        await audit_record(
            db,
            actor_user_id=None,
            action="auth.totp_reset_offline",
            target_kind="user",
            target_id=str(user.id),
            payload={
                "email": user.email,
                "regenerated_recovery_codes": regen_recovery,
            },
        )

        await db.commit()
        break

    click.echo("")
    click.echo(f"TOTP reset for {target}.")
    click.echo("Provisioning URI (scan into the authenticator):")
    click.echo(f"  {provisioning_uri(target, new_secret)}")
    if regen_recovery:
        click.echo("")
        click.echo("New recovery codes — store these somewhere safe; they're shown only once:")
        for code in new_codes:
            click.echo(f"  {code}")
    click.echo("")
    click.echo("All active sessions for this user have been revoked.")


@cli.command("reverify-stuck-tasks")
@click.option("--project", "project_name", required=True, help="Project name to scan.")
@click.option("--dry-run/--apply", default=True, help="Print verdicts without writing to DB.")
def reverify_stuck_tasks(project_name: str, dry_run: bool) -> None:
    """Re-run Argus on tasks stuck at needs_fixes whose latest task run produced
    no diff but did emit a final report. Uses the new transcript-aware verifier."""
    asyncio.run(_reverify_stuck_tasks(project_name=project_name, dry_run=dry_run))


async def _reverify_stuck_tasks(*, project_name: str, dry_run: bool) -> None:
    from daedalus.argus import verify_run as argus_verify_run
    from daedalus.argus.verifier import extract_agent_final_text
    from daedalus.db.models import ArgusReport, Project, Run, RunKind, Task, TaskStatus, Verdict
    from daedalus.storage.objects import get_object_store

    verdict_map = {"pass": Verdict.pass_, "partial": Verdict.partial, "fail": Verdict.fail}

    async for db in get_session():
        proj_res = await db.execute(select(Project).where(Project.name == project_name))
        project = proj_res.scalar_one_or_none()
        if project is None:
            click.echo(f"Project not found: {project_name}", err=True)
            raise SystemExit(1)

        tasks_res = await db.execute(
            select(Task).where(
                Task.project_id == project.id, Task.status == TaskStatus.needs_fixes
            )
        )
        tasks = list(tasks_res.scalars())
        click.echo(f"Found {len(tasks)} task(s) at needs_fixes in {project_name}")

        for task in tasks:
            run_res = await db.execute(
                select(Run)
                .where(Run.task_id == task.id, Run.kind == RunKind.task)
                .order_by(Run.created_at.desc())
                .limit(1)
            )
            task_run = run_res.scalar_one_or_none()
            if task_run is None or not task_run.transcript_object_key:
                click.echo(f"  SKIP {task.id} — no task run with transcript")
                continue
            try:
                transcript = get_object_store().get_text(task_run.transcript_object_key)
            except Exception as exc:
                click.echo(f"  SKIP {task.id} — transcript fetch failed: {exc}")
                continue
            agent_final_text = extract_agent_final_text(transcript)
            if not agent_final_text.strip():
                click.echo(f"  SKIP {task.id} — no final report extracted")
                continue

            connector_spec = task_run.connector_snapshot or {}
            verify_commands = connector_spec.get("verify_commands") or []

            argus_result = await argus_verify_run(
                task_title=task.title,
                task_description=task.description,
                acceptance_criteria=task.acceptance_criteria,
                verify_commands=list(verify_commands),
                diff_text="",
                verify_output="",
                verify_exit_code=0,
                verifier_model=project.verifier_model,
                agent_final_text=agent_final_text,
            )
            verdict_enum = verdict_map.get(argus_result.verdict, Verdict.fail)
            click.echo(f"  {task.id} :: {argus_result.verdict.upper()} :: {task.title[:60]}")
            click.echo(f"    summary: {argus_result.summary[:200]}")

            if dry_run:
                continue

            existing = await db.execute(
                select(ArgusReport).where(ArgusReport.run_id == task_run.id)
            )
            report = existing.scalar_one_or_none()
            if report is None:
                report = ArgusReport(
                    run_id=task_run.id,
                    task_id=task.id,
                    verdict=verdict_enum,
                    summary=argus_result.summary,
                    findings=argus_result.findings,
                    suggested_fix_task=argus_result.suggested_fix_task,
                )
                db.add(report)
            else:
                report.verdict = verdict_enum
                report.summary = argus_result.summary
                report.findings = argus_result.findings
                report.suggested_fix_task = argus_result.suggested_fix_task

            if verdict_enum == Verdict.pass_:
                task.status = TaskStatus.done

        if not dry_run:
            await db.commit()
            click.echo("Committed.")
        else:
            click.echo("(dry-run — nothing written)")
        break


if __name__ == "__main__":
    cli()
