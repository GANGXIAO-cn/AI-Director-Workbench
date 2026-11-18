"""Read the native CLI account banner without submitting a prompt or accepting dialogs."""
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import time
import fcntl
import termios


def account_banner(output):
    text = re.sub(r'\x1b\][^\x07]*(?:\x07)', '', output)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]', '', text)
    match = re.search(r'Antigravity CLI \d+\.\d+[^\n]*\n[^\n]*?'
                      r'([A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', text)
    return match.group(1) if match else None


def verify(binary, env, timeout=45):
    from .cli import ENV_KEEP, local_proxy_env
    launch = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    launch.update(local_proxy_env()); launch.update(env)
    launch['TERM'] = 'xterm-256color'
    master, slave = pty.openpty()
    proc = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
        proc = subprocess.Popen([str(binary)], cwd=Path.home(), env=launch,
                                stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave); slave = None
        deadline = time.monotonic() + timeout
        output = ''
        while time.monotonic() < deadline:
            if select.select([master], [], [], min(.2, max(0, deadline-time.monotonic())))[0]:
                try: chunk = os.read(master, 65536)
                except OSError: break
                if not chunk: break
                output = (output + chunk.decode('utf-8', errors='replace'))[-65536:]
                account = account_banner(output)
                if account:
                    return dict(authenticated=True, accountLabel=account)
                if 'Terms of Service & Data Use' in output or 'Choose your color scheme' in output:
                    return dict(authenticated=None, accountLabel=None, diagnostic='请在终端打开 agy，完成首次使用设置后再验证')
                if 'Do you trust the contents of this project?' in output:
                    return dict(authenticated=None, accountLabel=None, diagnostic='请在本机终端打开 agy，完成原生启动设置后再验证')
            if proc.poll() is not None: break
        return dict(authenticated=None, accountLabel=None, diagnostic='原生 CLI 尚未返回账号信息，请在终端确认登录后重试')
    finally:
        if proc is not None and proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait(timeout=3)
        os.close(master)
        if slave is not None: os.close(slave)
