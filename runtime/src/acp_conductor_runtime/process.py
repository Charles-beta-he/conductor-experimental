# SPDX-License-Identifier: Apache-2.0
"""Bounded POSIX subprocess adapter for trusted operator-selected programs."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .contracts import Grant, Request


class SubprocessAdapter:
    def __init__(self, argv: tuple[str, ...], *, pinned_files: tuple[Path, ...] = ()):
        if not argv or not all(type(a) is str and a and "\0" not in a for a in argv):
            raise ValueError("invalid explicit argv")
        if not Path(argv[0]).is_absolute():
            raise ValueError("executable must be absolute")
        self.argv = tuple(argv)
        self.pinned_files = tuple(Path(p).absolute() for p in pinned_files)
        self.identity = "process:" + self._fingerprint()

    def _fingerprint(self):
        payload = {"argv": self.argv, "files": [(str(p), hashlib.sha256(p.read_bytes()).hexdigest())
                                               for p in self.pinned_files]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def execute(self, request: Request, workspace: Path) -> bytes:
        """Direct trusted invocation; Store uses execute_authorized instead."""
        return self._execute(request, workspace, None)

    def execute_authorized(self, request: Request, workspace: Path, grant: Grant) -> bytes:
        return self._execute(request, workspace, grant)

    def _execute(self, request: Request, workspace: Path, grant: Grant | None) -> bytes:
        if self.identity != "process:" + self._fingerprint():
            raise ValueError("pinned adapter drift")
        deadline = time.monotonic() + request.timeout
        output = bytearray()
        total = 0
        # A regular file avoids blocking on stdin when a child refuses to read its pipe.
        with tempfile.TemporaryFile() as stdin, selectors.DefaultSelector() as selector:
            stdin.write(request.encoded.encode())
            stdin.seek(0)
            # Authorization linearizes here, after all pin/stdin preparation.
            # This is not a promise that OS scheduling/child entry precedes expiry.
            if grant is not None and not grant.permits(request, time.time()):
                raise PermissionError("local execution grant expired or mismatched at dispatch")
            proc = subprocess.Popen(self.argv, stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=workspace, start_new_session=True)
            assert proc.stdout is not None and proc.stderr is not None
            try:
                for pipe in (proc.stdout, proc.stderr):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("adapter deadline")
                    for key, _ in selector.select(min(remaining, .1)):
                        data = os.read(key.fd, 8192)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(data)
                        if total > request.max_output:
                            raise ValueError("combined output cap")
                        if key.fileobj is proc.stdout:
                            output.extend(data)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("adapter deadline")
                if proc.wait(timeout=remaining) != 0:
                    raise ValueError("nonzero adapter exit")
                return bytes(output)
            finally:
                # Only this invocation's new session; never adopt or kill stored PIDs.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
                proc.stdout.close()
                proc.stderr.close()
