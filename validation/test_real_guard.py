from types import SimpleNamespace

from real_guard import GuardedExecutor


def test_real_guard_only_forwards_authorized_selection():
    called = []
    wrapped = SimpleNamespace(start=lambda request: called.append(request) or "sent")
    for provider, model, effort in [
        ("claude", "gpt-6.1-sol", "high"), ("codex", "gpt-6-astra", "high"), ("codex", "gpt-6.1-sol", "low")
    ]:
        outcome = GuardedExecutor(provider, wrapped).start(SimpleNamespace(model=model, effort=effort))
        assert outcome.error_class == "unauthorized_model"
    assert not called
    assert GuardedExecutor("codex", wrapped).start(SimpleNamespace(model="gpt-6.1-sol", effort="high")) == "sent"
    assert len(called) == 1
