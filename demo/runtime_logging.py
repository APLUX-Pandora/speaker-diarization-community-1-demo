# SPDX-License-Identifier: Apache-2.0
"""Keep application results on stdout and redact runtime diagnostics on stderr."""
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

PATHS = re.compile(r"(?<![\w:/])/(?:home|Users|data\d*|root|local|tmp|private|mnt|opt|usr|var|etc|run|source|workspace)(?:/[^\s\"'<>:,]*)?")
PRIVATE_IPS = re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")

LICENSE_IDS = re.compile(r"(?i)(license ID:\s*)aid[a-z0-9]+")


def sanitize(text):
    return LICENSE_IDS.sub(r"\1<device-license>", PRIVATE_IPS.sub("<private-address>", PATHS.sub("<path>", text)))


# Native libraries retain fd 1 through shutdown. Python results use its saved copy.
BOOTSTRAP = """
import io, os, runpy, signal, sys
script = sys.argv[1]
if os.environ.get('SDIAR_BLOCK_TERMINATION') == script:
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
result_fd = os.dup(1)
os.dup2(2, 1)
sys.stdout = io.TextIOWrapper(os.fdopen(result_fd, 'wb'), encoding='utf-8', write_through=True)
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(script))
runpy.run_path(script, run_name='__main__')
"""


def private_log(path):
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, 'w', encoding='utf-8')


def entrypoint(main, *, block_termination=False):
    script = str(Path(sys.argv[0]).resolve())
    if os.environ.get('SDIAR_LOG_CHILD') == script:
        os.environ.pop('SDIAR_LOG_CHILD', None)
        os.environ.pop('SDIAR_BLOCK_TERMINATION', None)
        result = main()
        return 0 if result is None else result
    raw_path = os.environ.get('SDIAR_RAW_LOG')
    raw = private_log(Path(raw_path).expanduser()) if raw_path else None
    child = None
    previous = {}
    def forward(signum, _frame):
        if child is not None and child.poll() is None:
            child.send_signal(signum)
    try:
        # Inherit the caller's process group so meeting cancellation also reaches
        # native descendants. The application owns any separate worker groups.
        environment={**os.environ, 'SDIAR_LOG_CHILD': script}
        if block_termination:
            environment['SDIAR_BLOCK_TERMINATION']=script
        child = subprocess.Popen([sys.executable, '-u', '-c', BOOTSTRAP, script, *sys.argv[1:]],
                                 stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                 env=environment)
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, forward)
        for line in child.stderr:
            if raw:
                raw.write(line)
                raw.flush()
            sys.stderr.write(sanitize(line))
            sys.stderr.flush()
        code = child.wait()
        return code if code >= 0 else 128 - code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            child.stderr.close()
        if raw:
            raw.close()
