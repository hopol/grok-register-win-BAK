from panel.job_manager import RegistrationJobManager
from panel.panel_log import PanelLog


class FakeAccounts:
    def line_set(self):
        return {"before"}


class FakeCpaResult:
    queued = 2
    ok = 2
    fail = 0


class FakeCpaService:
    def __init__(self):
        self.calls = []

    def finalize_batch(self, before, successful, timeout):
        self.calls.append((before, successful, timeout))
        return FakeCpaResult()


def build_manager(tmp_path):
    cpa = FakeCpaService()
    manager = RegistrationJobManager(
        base_dir=tmp_path,
        log_dir=tmp_path,
        python="python",
        main_script=tmp_path / "main.py",
        panel_log=PanelLog(),
        account_store=FakeAccounts(),
        cpa_service=cpa,
        config_loader=lambda: {},
        config_saver=lambda config: None,
        proxy_resolver=lambda: "http://proxy",
        proxy_setter=lambda proxy: None,
        exit_ip_probe=lambda: "127.0.0.1",
        process_terminator=lambda process, log_callback=None: None,
        cpa_wait_timeout=10,
        sleep=lambda seconds: None,
    )
    return manager, cpa


def test_batch_transitions_through_cpa_and_preserves_counts(tmp_path, monkeypatch):
    manager, cpa = build_manager(tmp_path)
    monkeypatch.setattr(manager, "_run_round", lambda *args: True)

    manager.run_batch(2)

    state = manager.snapshot()
    assert state["status"] == "idle"
    assert state["success"] == 2
    assert state["cpa_ok"] == 2
    assert cpa.calls == [({"before"}, 2, 10)]


def test_stop_terminates_only_current_process(tmp_path):
    terminated = []
    manager, _ = build_manager(tmp_path)
    process = object()
    manager.process_terminator = lambda item, log_callback=None: terminated.append(item)
    manager.process = process
    manager.state["running"] = True

    assert manager.stop() == (True, "已发送停止")
    assert terminated == [process]
