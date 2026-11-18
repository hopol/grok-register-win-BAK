from panel import app as panel_app
from panel.cpa_service import BatchCpaResult


def test_finalize_batch_cpa_delegates_to_service(monkeypatch):
    calls = []

    def finalize(before, expected_success, timeout):
        calls.append((before, expected_success, timeout))
        return BatchCpaResult(queued=3, ok=2, fail=1, completed=True)

    monkeypatch.setattr(panel_app._cpa_service, "finalize_batch", finalize)

    result = panel_app.finalize_batch_cpa({"before"}, 3)

    assert result == {"queued": 3, "ok": 2, "fail": 1, "completed": True}
    assert calls == [({"before"}, 3, panel_app.CPA_BATCH_WAIT_TIMEOUT)]
