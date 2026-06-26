"""Core PTY session management."""
from __future__ import annotations

import os
import select
import signal
import threading
import time
from collections.abc import Callable

import ptyprocess
import structlog

log = structlog.get_logger()


class PTYSession:
    """Manages a PTY master/slave pair for running an agent process."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        workdir: str = ".",
        term: str = "xterm-256color",
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.workdir = workdir
        self.term = term
        self._rows = rows
        self._cols = cols
        self._proc: ptyprocess.PtyProcess | None = None
        self._reader_thread: threading.Thread | None = None
        self._output: list[bytes] = []
        self._output_lock = threading.Lock()
        self._on_output: list[Callable[[bytes], None]] = []
        self._closed = False
        # Serializes isalive() across threads. ptyprocess.isalive() calls
        # waitpid(); the streamer thread and the completion loop both poll
        # liveness, so concurrent calls race to reap the child — the loser's
        # waitpid raises ChildProcessError ("No child processes"), which used
        # to bubble up and mislabel a successful run as failed/exit -1.
        self._wait_lock = threading.Lock()

    def _isalive(self) -> bool:
        """Thread-safe liveness check that never raises.

        A reaped/never-spawned child is simply "not alive". Swallowing the
        teardown-race exception here (rather than letting it propagate) is the
        whole point — see _wait_lock.
        """
        if self._proc is None:
            return False
        with self._wait_lock:
            try:
                return self._proc.isalive()
            except (ptyprocess.PtyProcessError, ChildProcessError, OSError):
                return False

    @property
    def is_running(self) -> bool:
        return self._isalive()

    @property
    def exit_code(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.exitstatus

    @property
    def pid(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.pid

    def spawn(self) -> None:
        """Spawn the process inside a new PTY."""
        env = os.environ.copy()
        env.update(self.env)
        env["TERM"] = self.term

        cmd = [self.command] + self.args

        self._proc = ptyprocess.PtyProcess.spawn(
            cmd,
            env=env,
            cwd=self.workdir,
            dimensions=(self._rows, self._cols),
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        log.info("pty.spawned", command=self.command, args=self.args, pid=self._proc.pid)

    def read(self, timeout: float = 0.1) -> bytes:
        """Read accumulated output bytes, clearing the buffer."""
        with self._output_lock:
            data = b"".join(self._output)
            self._output.clear()
        return data

    def read_all(self, timeout: float = 0.1) -> bytes:
        """Read all accumulated output without clearing the buffer."""
        with self._output_lock:
            return b"".join(self._output)

    def write(self, data: bytes) -> None:
        """Write bytes to the PTY master (sent to the agent)."""
        if self._proc and self._isalive():
            self._proc.write(data)
            log.debug("pty.write", length=len(data))

    def write_text(self, text: str) -> None:
        """Write text to the PTY master."""
        self.write(text.encode())

    def send_eof(self) -> None:
        """Send EOF (Ctrl-D) on stdin so a process reading to end-of-input
        can finish. Required for non-interactive, batch-style connectors
        (e.g. ``bash``/``cat``/codex) whose prompt is piped via the PTY — they
        block forever otherwise and only die via the idle timeout. ``\\x04``
        works for both canonical-mode reads and readline (EOF on an empty
        line), so it is the universal end-of-input signal on a PTY."""
        self.write(b"\x04")

    def send_signal(self, sig: int) -> None:
        """Signal the child's whole process group, not just the leader.

        ptyprocess makes the child a session/group leader, so its pid is
        also its pgid. Signalling the group ensures the agent's own
        children — git, ssh, verify scripts — die with the run instead of
        being orphaned to the container init and lingering as zombies.
        """
        if self._proc and self._isalive():
            try:
                os.killpg(os.getpgid(self._proc.pid), sig)
                log.info("pty.signal", signal=sig, pid=self._proc.pid)
            except OSError:
                log.warning("pty.signal_failed", signal=sig, pid=self._proc.pid)

    def interrupt(self) -> None:
        """Send SIGINT (Ctrl+C)."""
        self.send_signal(signal.SIGINT)

    def kill(self, grace_seconds: int = 5) -> None:
        """Send SIGTERM, then SIGKILL after grace period."""
        if not (self._proc and self._isalive()):
            return
        self.send_signal(signal.SIGTERM)
        if self._wait_for_exit(grace_seconds):
            return
        self.send_signal(signal.SIGKILL)
        self._wait_for_exit(2)

    def _wait_for_exit(self, seconds: float, poll: float = 0.1) -> bool:
        """Poll isalive() for up to `seconds`. Return True if process exited."""
        if self._proc is None:
            return True
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._isalive():
                return True
            time.sleep(poll)
        return not self._isalive()

    def pause(self) -> None:
        """Send SIGSTOP to freeze the process."""
        self.send_signal(signal.SIGSTOP)

    def resume(self) -> None:
        """Send SIGCONT to resume the process."""
        self.send_signal(signal.SIGCONT)

    def wait(self, timeout: float | None = None) -> int | None:
        """Wait for the process to exit."""
        if self._proc:
            return self._proc.wait(timeout=timeout)
        return None

    def poll(self) -> int | None:
        """Check if process has exited (non-blocking)."""
        if self._proc:
            return self._proc.exitstatus
        return None

    def resize(self, rows: int, cols: int) -> None:
        """Resize the PTY."""
        if self._proc and self._isalive():
            self._proc.setwinsize(rows, cols)

    def on_output(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for each chunk of output."""
        self._on_output.append(callback)

    def close(self) -> None:
        """Clean up the PTY session."""
        self._closed = True
        if self._proc and self._isalive():
            self.kill()
        if self._reader_thread:
            self._reader_thread.join(timeout=2)

    def _read_loop(self) -> None:
        """Background thread that reads from the PTY master."""
        while self._proc and self._isalive() and not self._closed:
            try:
                ready, _, _ = select.select([self._proc.fd], [], [], 0.1)
                if not ready:
                    continue
                data = self._proc.read(4096)
                if data:
                    with self._output_lock:
                        self._output.append(data)
                    for cb in self._on_output:
                        try:
                            cb(data)
                        except Exception:
                            log.exception("pty.output_callback_error")
            except (EOFError, OSError):
                break
            except ptyprocess.PtyProcessError:
                break
