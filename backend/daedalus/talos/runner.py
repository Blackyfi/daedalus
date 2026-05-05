"""Multi-run PTY agent supervisor — Talos.

One Talos process supervises up to MAX_CONCURRENT_PROJECTS concurrent runs
(one per project). State lives in a dict of `RunContext` keyed by run_id, so
lifecycle signals (`pause`/`resume`/`interrupt`/`kill`/`detach`/`inject`/
`resize`) route deterministically to the right PTY without any "current run"
ambient state. See project-plan.md §6.2.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis
import structlog

from daedalus.connectors.usage import UsageRecord, parse_usage
from daedalus.core.settings import get_settings
from daedalus.storage.objects import get_object_store
from daedalus.talos.cgroups import RunCgroup, create_run_cgroup
from daedalus.talos.claude_trust import trust_workdir
from daedalus.talos.pty import PTYSession

log = structlog.get_logger()


_VALID_LIFECYCLE_ACTIONS = {"pause", "resume", "interrupt", "kill", "detach", "inject", "resize"}


@dataclass
class RunContext:
    """All per-run state. Owned by the worker thread that's running the task;
    read by the listener thread for lifecycle dispatch (under the runner's
    contexts_lock)."""

    run_id: str
    session: PTYSession | None = None
    transcript_chunks: list[bytes] = field(default_factory=list)
    transcript_lock: threading.Lock = field(default_factory=threading.Lock)
    last_output_ts: float = 0.0
    idle_killed: bool = False
    completion_published: bool = False
    tool_call_seen: bool = False
    done_signal_seen: bool = False
    cgroup: RunCgroup | None = None
    connector_spec: dict | None = None
    project_id: str | None = None


class TalosRunner:
    """Multi-run PTY supervisor."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self.redis = redis_client
        self.settings = get_settings()
        self.contexts: dict[str, RunContext] = {}
        self._contexts_lock = threading.Lock()
        self._shutdown = False
        # Worker pool: one thread per concurrent run. Argus role still runs
        # one verification at a time per process; we let max_concurrent_projects
        # bound it for symmetry.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, self.settings.max_concurrent_projects),
            thread_name_prefix="talos-run",
        )
        self._pythia_thread: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────

    def run_loop(self) -> None:
        log.info(
            "talos.start",
            role=self.settings.role,
            max_concurrent=self.settings.max_concurrent_projects,
        )
        self._recover_orphans()
        if self.settings.role == "talos":
            self._start_pythia_thread()
        self._listen_for_jobs()

    def request_shutdown(self) -> None:
        self._shutdown = True
        log.info("talos.shutdown_requested", in_flight=len(self.contexts))

    # ── listener (sync, single thread) ────────────────────────────────────

    def _listen_for_jobs(self) -> None:
        pubsub = self.redis.pubsub()
        pubsub.psubscribe("hermes:signal:*")

        while not self._shutdown:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                time.sleep(0.05)
                continue
            if message["type"] != "pmessage":
                continue

            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            data = message["data"]

            parts = channel.split(":")
            if len(parts) < 3:
                continue
            run_id = ":".join(parts[2:])

            try:
                if isinstance(data, bytes):
                    data = data.decode()
                payload = json.loads(data) if isinstance(data, str) else data
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("talos.signal_decode_failed", run_id=run_id)
                continue
            if not isinstance(payload, dict):
                continue

            action = payload.get("action", "run")
            if action == "argus_verify" and self.settings.role != "argus":
                continue
            if action == "run" and self.settings.role == "argus":
                continue

            try:
                self._handle_signal(run_id, action, payload)
            except Exception:
                log.exception("talos.signal_handle_error", run_id=run_id, action=action)

        log.info("talos.shutdown_complete", in_flight=len(self.contexts))

    def _handle_signal(self, run_id: str, action: str, payload: dict) -> None:
        if action in _VALID_LIFECYCLE_ACTIONS:
            self._handle_lifecycle(run_id, action, payload)
        elif action == "run":
            self._spawn_run(run_id, payload, kind="task")
        elif action == "argus_verify":
            self._spawn_run(run_id, payload, kind="argus")

    def _spawn_run(self, run_id: str, payload: dict, *, kind: str) -> None:
        with self._contexts_lock:
            if run_id in self.contexts:
                log.warning("talos.duplicate_run", run_id=run_id)
                return
            ctx = RunContext(
                run_id=run_id,
                project_id=(payload.get("project") or {}).get("id"),
            )
            self.contexts[run_id] = ctx

        connector_spec = payload.get("connector_spec", {})
        task_info = payload.get("task", {})
        project_info = payload.get("project", {})
        resource_limits = payload.get("resource_limits", {})

        if kind == "task":
            self._executor.submit(
                self._safe_execute_task,
                ctx, connector_spec, task_info, project_info, resource_limits,
            )
        else:
            self._executor.submit(
                self._safe_execute_argus,
                ctx, connector_spec, task_info, project_info, resource_limits,
            )

    def _handle_lifecycle(self, run_id: str, action: str, payload: dict) -> None:
        with self._contexts_lock:
            ctx = self.contexts.get(run_id)
        if ctx is None or ctx.session is None:
            buffer_key = f"hermes:pending_signals:{run_id}"
            self.redis.rpush(buffer_key, json.dumps({"action": action, "payload": payload}))
            self.redis.expire(buffer_key, 600)
            log.info("talos.signal_buffered", run_id=run_id, action=action)
            return

        self._apply_lifecycle(ctx, action, payload)
        self.redis.set(
            f"hermes:ack:{run_id}",
            json.dumps({"action": action, "done": True}),
            ex=60,
        )

    def _apply_lifecycle(self, ctx: RunContext, action: str, payload: dict) -> None:
        session = ctx.session
        if session is None:
            return
        if action == "pause":
            session.pause()
        elif action == "resume":
            session.resume()
        elif action == "interrupt":
            session.interrupt()
        elif action == "kill":
            session.kill()
        elif action == "detach":
            log.info("talos.detached", run_id=ctx.run_id)
        elif action == "inject":
            text = payload.get("text", "")
            if text:
                session.write_text(text)
        elif action == "resize":
            rows = int(payload.get("rows") or 0)
            cols = int(payload.get("cols") or 0)
            if rows > 0 and cols > 0:
                session.resize(rows, cols)

    def _drain_pending_signals(self, ctx: RunContext) -> None:
        buffer_key = f"hermes:pending_signals:{ctx.run_id}"
        while True:
            raw = self.redis.lpop(buffer_key)
            if not raw:
                return
            try:
                entry = json.loads(raw if isinstance(raw, str) else raw.decode())
            except (json.JSONDecodeError, ValueError):
                continue
            self._apply_lifecycle(ctx, entry.get("action", ""), entry.get("payload", {}))

    # ── execution ────────────────────────────────────────────────────────

    def _safe_execute_task(self, ctx: RunContext, *args, **kwargs) -> None:
        try:
            self._execute_task(ctx, *args, **kwargs)
        except Exception:
            log.exception("talos.task_thread_crashed", run_id=ctx.run_id)
            self._complete_run(ctx, exit_code=-1)
        finally:
            self._cleanup_context(ctx)

    def _safe_execute_argus(self, ctx: RunContext, *args, **kwargs) -> None:
        try:
            self._execute_argus(ctx, *args, **kwargs)
        except Exception:
            log.exception("talos.argus_thread_crashed", run_id=ctx.run_id)
            self._complete_run(ctx, exit_code=-1)
        finally:
            self._cleanup_context(ctx)

    def _cleanup_context(self, ctx: RunContext) -> None:
        if ctx.cgroup is not None:
            try:
                ctx.cgroup.remove()
            except Exception:
                log.warning("talos.cgroup_remove_failed", run_id=ctx.run_id, exc_info=True)
            ctx.cgroup = None
        with self._contexts_lock:
            self.contexts.pop(ctx.run_id, None)
        self._release_lock(ctx.run_id)

    def _execute_task(
        self,
        ctx: RunContext,
        connector_spec: dict,
        task_info: dict,
        project_info: dict,
        resource_limits: dict,
    ) -> None:
        ctx.last_output_ts = time.time()
        ctx.connector_spec = connector_spec

        command = connector_spec.get("command", "claude")
        args = connector_spec.get("args", [])
        workdir = connector_spec.get("workdir", self.settings.workspaces_root)
        env = connector_spec.get("env", {})

        workdir = self._render_template(workdir, task_info, project_info)
        env = {k: self._render_template(v, task_info, project_info) for k, v in env.items()}

        project_task_model = project_info.get("task_model")
        if project_task_model and "ANTHROPIC_MODEL" not in env:
            env["ANTHROPIC_MODEL"] = project_task_model

        active_worktree = project_info.get("active_worktree_path")
        if active_worktree:
            workdir = active_worktree
        else:
            worktree_path = self._create_worktree(project_info, ctx.run_id)
            if worktree_path:
                workdir = worktree_path

        input_text = self._build_prompt(connector_spec, task_info)

        prompt_via_arg = (
            "--print" in args
            or "-p" in args
            or bool(connector_spec.get("prompt_as_arg"))
        )
        if prompt_via_arg and input_text:
            args = list(args) + [input_text]
            input_text = ""

        egress_allowlist = connector_spec.get("egress_allowlist")
        if egress_allowlist:
            log.info("talos.egress_allowlist", run_id=ctx.run_id, allow=egress_allowlist)

        log.info("talos.executing", run_id=ctx.run_id, command=command, workdir=workdir)

        if "claude" in os.path.basename(command):
            trust_workdir(workdir)

        try:
            session = PTYSession(
                command=command,
                args=args,
                env=env,
                workdir=workdir,
                rows=40,
                cols=160,
            )
            session.spawn()
            ctx.session = session

            cgroup_limits = connector_spec.get("resource_limits", {})
            cg = create_run_cgroup(
                ctx.run_id,
                cpu_shares=cgroup_limits.get("cpu_shares"),
                memory_mb=cgroup_limits.get("memory_mb"),
                pids_max=cgroup_limits.get("pids_max"),
            )
            if cg is not None and session.pid is not None:
                cg.add_pid(session.pid)
            ctx.cgroup = cg

            self._drain_pending_signals(ctx)

            self._stream_output(ctx)

            if input_text:
                session.write_text(input_text)

            self._wait_for_completion(ctx, connector_spec)

        except Exception:
            log.exception("talos.exec_error", run_id=ctx.run_id)
            self._complete_run(ctx, exit_code=-1)
            return

        self._complete_run(ctx, exit_code=ctx.session.exit_code if ctx.session else None)

    def _stream_output(self, ctx: RunContext) -> None:
        stream_key = f"pty:{ctx.run_id}"

        def publish(data: bytes) -> None:
            if not data:
                return
            with ctx.transcript_lock:
                ctx.transcript_chunks.append(data)
            ctx.last_output_ts = time.time()
            try:
                self.redis.xadd(
                    stream_key,
                    {"data": data.hex()},
                    id="*",
                    maxlen=10000,
                    approx=True,
                )
            except Exception:
                pass

        def streamer() -> None:
            session = ctx.session
            if session is None:
                return
            while session.is_running:
                publish(session.read(timeout=0.05))
            # Drain final buffered tail.
            publish(session.read(timeout=0.1))

        thread = threading.Thread(target=streamer, daemon=True, name=f"talos-stream-{ctx.run_id[:8]}")
        thread.start()

    def _wait_for_completion(self, ctx: RunContext, connector_spec: dict) -> None:
        session = ctx.session
        if session is None:
            return

        done_signal = connector_spec.get("done_signal", {})
        done_kind = done_signal.get("kind", "exit_code")
        expected_exit = done_signal.get("exit_code")
        tool_name = done_signal.get("tool_name")
        exit_on_done = connector_spec.get("exit_on_done", True)
        resource_limits = connector_spec.get("resource_limits", {})
        wall_clock = resource_limits.get("wall_clock_minutes", 60) * 60
        idle_timeout = resource_limits.get("idle_output_minutes", 0) * 60

        regex_pattern = re.compile(done_signal["pattern"]) if done_kind == "regex" and done_signal.get("pattern") else None
        tool_pattern = (
            re.compile(rf'"name"\s*:\s*"{re.escape(tool_name)}"|<<TASK_DONE:{re.escape(tool_name)}>>')
            if done_kind == "tool_call" and tool_name
            else None
        )

        start = time.time()
        while session.is_running:
            if regex_pattern is not None:
                output = session.read_all().decode(errors="replace")
                if regex_pattern.search(output):
                    log.info("talos.done_signal", run_id=ctx.run_id, kind="regex")
                    ctx.done_signal_seen = True
                    if exit_on_done:
                        self._stop_after_done_signal(session)
                    break

            if tool_pattern is not None and not ctx.tool_call_seen:
                output = session.read_all().decode(errors="replace")
                if tool_pattern.search(output):
                    log.info(
                        "talos.done_signal", run_id=ctx.run_id, kind="tool_call", tool=tool_name
                    )
                    ctx.tool_call_seen = True
                    ctx.done_signal_seen = True
                    if exit_on_done:
                        self._stop_after_done_signal(session)
                    break

            poll = session.poll()
            if poll is not None:
                log.info("talos.process_exited", run_id=ctx.run_id, exit_code=poll)
                if done_kind == "exit_code" and expected_exit is not None and poll != expected_exit:
                    log.warning(
                        "talos.exit_code_mismatch",
                        run_id=ctx.run_id,
                        expected=expected_exit,
                        got=poll,
                    )
                break

            now = time.time()
            elapsed = now - start
            if elapsed > wall_clock:
                log.warning("talos.wall_clock_exceeded", run_id=ctx.run_id)
                session.kill()
                break
            if idle_timeout > 0 and (now - ctx.last_output_ts) > idle_timeout:
                log.warning(
                    "talos.idle_output_exceeded",
                    run_id=ctx.run_id,
                    idle_seconds=int(now - ctx.last_output_ts),
                )
                ctx.idle_killed = True
                session.kill()
                break

            time.sleep(0.5)

    def _stop_after_done_signal(self, session: PTYSession) -> None:
        session.interrupt()
        for _ in range(20):
            if not session.is_running:
                return
            time.sleep(0.1)
        if session.is_running:
            session.kill()

    def _complete_run(self, ctx: RunContext, exit_code: int | None) -> None:
        if ctx.completion_published:
            return
        ctx.completion_published = True

        transcript_text = self._render_transcript_text(ctx)
        transcript_object_key = self._persist_transcript(ctx)
        usage = self._parse_usage(ctx, transcript_text)
        log.info(
            "talos.completed",
            run_id=ctx.run_id,
            exit_code=exit_code,
            token_input=usage.token_input,
            token_output=usage.token_output,
            cost_usd_micros=usage.cost_usd_micros,
        )
        if ctx.idle_killed:
            state = "failed"
        elif ctx.done_signal_seen:
            state = "completed"
        else:
            state = "completed" if exit_code == 0 else "failed"
        completion = {
            "run_id": ctx.run_id,
            "exit_code": exit_code,
            "state": state,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "transcript_object_key": transcript_object_key,
            "idle_killed": ctx.idle_killed,
            "token_input": usage.token_input,
            "token_output": usage.token_output,
            "cost_usd_micros": usage.cost_usd_micros,
        }
        self.redis.hset("hermes:run", ctx.run_id, json.dumps(completion))
        self.redis.set(f"hermes:completion:{ctx.run_id}", json.dumps(completion), ex=86400)
        self.redis.publish(
            "hermes:complete",
            json.dumps({"run_id": ctx.run_id, "exit_code": exit_code}),
        )
        if ctx.session:
            try:
                ctx.session.close()
            except Exception:
                pass
        with ctx.transcript_lock:
            ctx.transcript_chunks = []

    def _render_transcript_text(self, ctx: RunContext) -> str:
        with ctx.transcript_lock:
            if not ctx.transcript_chunks:
                return ""
            try:
                return b"".join(ctx.transcript_chunks).decode("utf-8", errors="replace")
            except Exception:
                return ""

    def _parse_usage(self, ctx: RunContext, transcript_text: str) -> UsageRecord:
        spec = ctx.connector_spec or {}
        parser_spec = spec.get("usage_parser") if isinstance(spec, dict) else None
        if not parser_spec or not transcript_text:
            return UsageRecord()
        return parse_usage(transcript_text, parser_spec)

    def _persist_transcript(self, ctx: RunContext) -> str | None:
        with ctx.transcript_lock:
            if not ctx.transcript_chunks:
                return None
            transcript = b"".join(ctx.transcript_chunks)
        key = f"runs/{ctx.run_id}/transcript.log"
        try:
            return get_object_store().put_bytes(key, transcript, content_type="text/plain; charset=utf-8")
        except Exception:
            log.warning("talos.transcript_persist_failed", run_id=ctx.run_id, exc_info=True)
            return None

    def _release_lock(self, run_id: str) -> None:
        try:
            self.redis.delete(f"hermes:lock:{run_id}")
        except Exception:
            log.warning("talos.lock_release_failed", run_id=run_id, exc_info=True)

    def _render_template(self, template: str, task_info: dict, project_info: dict) -> str:
        result = template
        for key, value in task_info.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        for key, value in project_info.items():
            result = result.replace(f"{{{{project.{key}}}}}", str(value))
        return result

    def _build_prompt(self, connector_spec: dict, task_info: dict) -> str:
        input_format = connector_spec.get("input_format", {})
        template = input_format.get("template", "{{task.title}}\n\n{{task.description}}")
        for key, value in task_info.items():
            template = template.replace(f"{{{{task.{key}}}}}", str(value))
        return template

    def _create_worktree(self, project_info: dict, run_id: str) -> str | None:
        try:
            workspace = project_info.get("workspace_path", "")
            branch = project_info.get("git_default_branch", "main")
            run_dir = f"{workspace}/runs/{run_id}"
            os.makedirs(run_dir, exist_ok=True)
            subprocess.run(
                ["git", "worktree", "add", "-b", f"daedalus-run-{run_id}", run_dir, branch],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            log.info("talos.worktree_created", path=run_dir, run_id=run_id)
            return run_dir
        except Exception:
            log.warning("talos.worktree_failed", run_id=run_id, exc_info=True)
            return None

    def _recover_orphans(self) -> None:
        # Mostly handled by Hermes now (per-run lock + project lease). Talos
        # only needs to ensure the legacy single-runner global lock isn't
        # carried into the multi-run world.
        legacy = self.redis.get("hermes:lock")
        if legacy:
            try:
                self.redis.delete("hermes:lock")
                log.info("talos.legacy_lock_cleared")
            except Exception:
                pass

    # ── argus ────────────────────────────────────────────────────────────

    def _execute_argus(
        self,
        ctx: RunContext,
        connector_spec: dict,
        task_info: dict,
        project_info: dict,
        resource_limits: dict,
    ) -> None:
        verify_commands = connector_spec.get("verify_commands", [])
        if verify_commands:
            argus_profile = {
                "command": "bash",
                "args": ["-lc", "set -e\n" + "\n".join(verify_commands)],
                "workdir": "{{project.active_worktree_path}}",
                "env": {},
                "done_signal": {"kind": "exit_code", "exit_code": 0},
                "exit_on_done": True,
                "input_format": {"kind": "stdin_prompt", "template": ""},
                "resource_limits": connector_spec.get("resource_limits", {}),
                "workdir_readonly": True,
            }
        else:
            argus_profile = connector_spec.get("argus_profile", {})
            if not argus_profile:
                argus_profile = connector_spec.copy()
            if "args" not in argus_profile:
                argus_profile["args"] = []
            argus_profile["args"].extend(["--permission-mode=read-only"])

        if argus_profile.get("workdir_readonly"):
            log.info("talos.argus_readonly", run_id=ctx.run_id)

        argus_project_info = dict(project_info)
        argus_project_info["task_model"] = (
            project_info.get("verifier_model") or project_info.get("task_model")
        )

        self._execute_task(ctx, argus_profile, task_info, argus_project_info, resource_limits)

    # ── Pythia (subscription oracle) ─────────────────────────────────────

    def _start_pythia_thread(self) -> None:
        from daedalus.pythia.probe import probe_and_cache  # local import (optional)

        def loop() -> None:
            # First probe shortly after boot, then on a fixed cadence.
            time.sleep(5)
            while not self._shutdown:
                try:
                    probe_and_cache(self.redis)
                except Exception:
                    log.exception("pythia.probe_loop_error")
                # Sleep in 5-second slices so shutdown is responsive.
                slept = 0
                while slept < self.settings.pythia_refresh_seconds and not self._shutdown:
                    time.sleep(5)
                    slept += 5

        self._pythia_thread = threading.Thread(target=loop, daemon=True, name="pythia")
        self._pythia_thread.start()
        log.info(
            "talos.pythia_started",
            refresh_seconds=self.settings.pythia_refresh_seconds,
        )


def main() -> None:
    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    runner = TalosRunner(r)
    runner.run_loop()


if __name__ == "__main__":
    main()
