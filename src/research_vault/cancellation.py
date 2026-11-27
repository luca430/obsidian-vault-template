"""Cooperative, per-run cancellation at safe boundaries on every supported OS."""

import os
import signal
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar

_marker = ContextVar("sync_cancel_marker", default=None)


class SyncInterrupted(KeyboardInterrupt):
    pass


def check_cancelled():
    marker = _marker.get()
    if marker is not None and marker.exists():
        raise SyncInterrupted("Synchronization interrupted; completed work remains resumable")


@contextmanager
def cancellation(marker):
    token = _marker.set(marker)
    try:
        check_cancelled()
        yield
    finally:
        _marker.reset(token)
        if marker is not None:
            marker.unlink(missing_ok=True)


def run_process(command, **kwargs):
    """Use normal subprocess semantics unless a cancellable sync is active."""
    if _marker.get() is None:
        return subprocess.run(command, **kwargs)
    check_cancelled()
    timeout = kwargs.pop("timeout", None)
    input_text = kwargs.pop("input", None)
    capture = kwargs.pop("capture_output", False)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input_text is not None:
        kwargs["stdin"] = subprocess.PIPE
    started = time.monotonic()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    try:
        first = True
        while True:
            check_cancelled()
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(
                    input=input_text if first else None, timeout=0.25
                )
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                first = False
    finally:
        if process.poll() is None:
            if os.name == "nt":
                # Native npm wrappers can spawn another CLI process. Terminate only
                # this known child tree; never use a shell or kill the WSL launcher.
                taskkill = os.path.join(
                    os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"
                )
                try:
                    subprocess.run(
                        [taskkill, "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=3,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    process.terminate()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.communicate()
