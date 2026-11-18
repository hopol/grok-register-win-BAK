import pytest

import grok_register_ttk as register


class FakePage:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.url = "https://accounts.x.ai/sign-up"

    def run_js(self, _script):
        return self.snapshots.pop(0)


def test_email_submit_accepts_verification_state(monkeypatch):
    monkeypatch.setattr(register, "page", FakePage([{"state": "verification"}]))
    assert register.wait_for_email_submission_result(timeout=1) == "verification"


def test_email_submit_surfaces_domain_rejection(monkeypatch):
    monkeypatch.setattr(
        register,
        "page",
        FakePage(
            [
                {
                    "state": "email-form",
                    "errors": ["This email domain is not supported"],
                }
            ]
        ),
    )
    with pytest.raises(Exception, match="邮箱提交被 xAI 拒绝"):
        register.wait_for_email_submission_result(timeout=1)
