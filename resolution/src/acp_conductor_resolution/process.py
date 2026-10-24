# SPDX-License-Identifier: Apache-2.0
# Modified derivative of the runtime Store/process implementation.
# Changes: isolated v2 UNKNOWN-resolution protocol; Store 0.0.3 preserves
# authorized FAILED terminal readback despite later artifact-integrity failure.
# See the distributed PROVENANCE.md for lineage, scope and remaining limitations.
"""Bounded trusted POSIX process execution, with explicit owned-group closure."""

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from acp_conductor_runtime.contracts import Grant, Request

from .contracts import ExecutionResult


class SubprocessAdapter:
    def __init__(self, argv, *, pinned_files=()):
        if (
            type(argv) is not tuple
            or not argv
            or any(type(a) is not str or not a or "\x00" in a for a in argv)
        ):
            raise ValueError("explicit argv tuple required")
        executable = Path(argv[0]).resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("invalid executable")
        self.argv = (str(executable), *argv[1:])
        if type(pinned_files) is not tuple:
            raise ValueError("explicit pinned file tuple required")
        files = (executable, *(Path(p).resolve(strict=True) for p in pinned_files))
        if any(not p.is_file() for p in files):
            raise ValueError("pinned regular files required")
        self._pins = tuple((str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files)
        self.identity = (
            "posix-v2:"
            + hashlib.sha256(
                json.dumps([self.argv, self._pins], separators=(",", ":")).encode()
            ).hexdigest()
        )

    @staticmethod
    def _close(proc):
        # Only the process group freshly created by this call; never a saved PID.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)
        deadline = time.monotonic() + 1
        while True:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return
            # PermissionError and any non-ESRCH fail closed, not a closure claim.
            if time.monotonic() >= deadline:
                raise TimeoutError("owned process group still present")
            time.sleep(0.01)

    def execute_authorized(self, request, workspace, grant):
        if type(request) is not Request or type(grant) is not Grant:
            raise PermissionError("execution grant required")
        if request.adapter_id != self.identity or any(
            hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
            for path, digest in self._pins
        ):
            raise PermissionError("adapter identity changed")
        with (
            tempfile.TemporaryFile() as stdin,
            tempfile.TemporaryFile() as stdout,
            tempfile.TemporaryFile() as stderr,
        ):
            stdin.write(request.prompt.encode())
            stdin.seek(0)
            # Last authorization use point immediately before subprocess creation.
            if not grant.permits(request, time.time()):
                raise PermissionError("execution grant expired")
            proc = subprocess.Popen(
                self.argv,
                cwd=workspace,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            error = None
            try:
                deadline = time.monotonic() + request.timeout
                while proc.poll() is None:
                    if time.monotonic() >= deadline:
                        error = "timeout"
                        break
                    if (
                        max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size)
                        > request.max_output
                    ):
                        error = "output_limit"
                        break
                    time.sleep(0.01)
            finally:
                # Any failure here raises: Store MUST NOT persist closure evidence.
                self._close(proc)
            if (
                max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size)
                > request.max_output
            ):
                error = "output_limit"
            if error is None and proc.returncode != 0:
                error = "nonzero_exit"
            if error is not None:
                return ExecutionResult(None, error)
            stdout.seek(0)
            return ExecutionResult(stdout.read(request.max_output + 1), None)
