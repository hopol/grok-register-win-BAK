import subprocess

from lib import process_control


class FakePsProcess:
    def __init__(self, pid, children=None):
        self.pid = pid
        self._children = children or []
        self.terminated = False
        self.killed = False

    def children(self, recursive=False):
        assert recursive is True
        return self._children

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class FakePopen:
    def __init__(self, pid):
        self.pid = pid
        self.wait_calls = []
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return 0

    def kill(self):
        self.killed = True


def test_terminate_process_tree_only_targets_descendants(monkeypatch):
    child = FakePsProcess(201)
    grandchild = FakePsProcess(202)
    unrelated = FakePsProcess(999)
    parent = FakePsProcess(200, [child, grandchild])
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: parent)
    monkeypatch.setattr(process_control.psutil, "wait_procs", lambda targets, timeout: (targets, []))

    popen = FakePopen(200)
    process_control.terminate_process_tree(popen)

    assert parent.terminated is True
    assert child.terminated is True
    assert grandchild.terminated is True
    assert unrelated.terminated is False
    assert popen.wait_calls == [5.0]


def test_timeout_forces_remaining_processes(monkeypatch):
    child = FakePsProcess(301)
    parent = FakePsProcess(300, [child])
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: parent)
    monkeypatch.setattr(process_control.psutil, "wait_procs", lambda targets, timeout: ([], [child]))

    process_control.terminate_process_tree(FakePopen(300), timeout=1)

    assert child.killed is True
