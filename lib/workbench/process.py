"""Stream installer output, including partial lines, without hiding quiet work."""
import codecs
import locale
import os
import re
import selectors
import subprocess
import time

from .ui import ANSI, Console


def elapsed(seconds):
    seconds = int(seconds)
    return f'{seconds // 60}分{seconds % 60:02d}秒' if seconds >= 60 else f'{seconds}秒'


def stream_command(command, *, label, env=None, log=None, heartbeat=15):
    console = Console()
    started = last_output = time.monotonic()
    decoder = codecs.getincrementaldecoder(locale.getpreferredencoding(False))(errors='replace')
    pending = ''
    at_line_start = True

    def emit(value, final=False):
        nonlocal pending, at_line_start
        value = pending + value
        pending = ''
        # Escape sequences, just like UTF-8 characters, can cross read boundaries.
        partial = re.search(r'\x1b(?:\[[0-?]*[ -/]*)?$', value) if not final else None
        if partial:
            pending = value[partial.start():]
            value = value[:partial.start()]
        if not value:
            return
        plain = ANSI.sub('', value)
        print(value if console.color else plain, end='', flush=True)
        at_line_start = value.endswith(('\n', '\r'))
        if log is not None:
            log.write(plain.replace('\r\n', '\n').replace('\r', '\n'))
            log.flush()

    with subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    # A child may close its output before exiting; still reap it
                    # promptly rather than waiting for the heartbeat deadline.
                    timeout = min(.25, max(0, heartbeat - (time.monotonic() - last_output)))
                    for key, _ in selector.select(timeout):
                        chunk = os.read(key.fd, 65536)
                        if chunk:
                            emit(decoder.decode(chunk))
                            last_output = time.monotonic()
                        else:
                            selector.unregister(key.fileobj)
                            emit(decoder.decode(b'', final=True), final=True)
                    now = time.monotonic()
                    if now - last_output >= heartbeat and process.poll() is None:
                        emit(('' if at_line_start else '\n') +
                             f'[WAIT] {label}：已运行 {elapsed(now - started)}，等待安装器输出…\n')
                        last_output = now
            if not at_line_start:
                emit('\n')
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
