from pathlib import Path

from helpers import Env
from workbench.executors import agy_identity
from workbench.executors.cli import AntigravityExecutor


def test_native_banner_ignores_transient_signed_out_and_requires_account():
    text = 'Welcome to the Antigravity CLI. You are currently not signed in.\r\nSigning in...'
    assert agy_identity.account_banner(text) is None
    assert agy_identity.account_banner(text + '\x1b[8AAntigravity CLI 1.5.10\n'
                                       '\x1b[36D ▀▀▀ test@example.invalid (Google AI Pro)\r\n') == 'test@example.invalid'
    assert agy_identity.account_banner('error for test@example.invalid') is None


def test_verify_never_submits_input_or_accepts_onboarding(tmp_path):
    import sys
    script = tmp_path/'agy'
    script.write_text(f'#!{sys.executable}\n'
                      'import sys,select,time\n'
                      'assert len(sys.argv)==1\n'
                      'print("Terms of Service & Data Use",flush=True)\n'
                      'if select.select([sys.stdin],[],[],1)[0]: raise RuntimeError("unexpected input")\n'
                      'time.sleep(3)\n')
    script.chmod(0o700)
    value = agy_identity.verify(script, {}, timeout=2)
    assert value['authenticated'] is None
    assert '首次使用' in value['diagnostic']


def test_verified_account_shared_with_picker_without_repeated_cli_launch(tmp_path, monkeypatch):
    env = Env(tmp_path); wb = env.wb
    executor = AntigravityExecutor()
    monkeypatch.setattr(executor, 'binary', lambda: Path('/test/agy'))
    wb.executors['antigravity'] = executor
    calls = []
    def verify(binary, settings):
        calls.append((binary, settings))
        return dict(authenticated=True, accountLabel='test@example.invalid')
    monkeypatch.setattr(agy_identity, 'verify', verify)
    monkeypatch.setattr(wb, 'clock', lambda: 1000)
    assert wb.executor_identity('antigravity')['authenticated'] is None
    assert wb.verify_executor_identity('antigravity')['authenticated'] is True
    for _ in range(3):
        assert wb.executor_identity('antigravity')['accountLabel'] == 'test@example.invalid'
    assert len(calls) == 1
    monkeypatch.setattr(wb, 'clock', lambda: 1301)
    assert wb.executor_identity('antigravity')['authenticated'] is None
    assert wb.executor_identity('antigravity')['lastVerifiedAccount'] == 'test@example.invalid'
    # A subsequent failed verification must not continue to claim a fresh login.
    monkeypatch.setattr(agy_identity, 'verify', lambda *a: dict(authenticated=None, accountLabel=None))
    assert wb.verify_executor_identity('antigravity')['authenticated'] is None
    assert 'lastVerifiedAccount' not in wb.executor_identity('antigravity')
