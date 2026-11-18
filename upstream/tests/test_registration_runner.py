from lib.registration_runner import RegistrationActions, RegistrationRunner


class Cancelled(Exception):
    pass


class RetryNeeded(Exception):
    pass


class FakeActions:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.restarted = 0

    def build(self):
        return RegistrationActions(
            start_browser=self.start_browser,
            stop_browser=self.stop_browser,
            restart_browser=self.restart_browser,
            browser_available=lambda: True,
            get_email_provider=lambda: "fake",
            open_signup_page=lambda **kwargs: None,
            fill_email_and_submit=lambda **kwargs: ("user@example.com", "mail-token"),
            fill_code_and_submit=lambda *args, **kwargs: "123456",
            fill_profile_and_submit=lambda **kwargs: {"given_name": "Test", "family_name": "User", "password": "pw"},
            wait_for_sso_cookie=lambda **kwargs: "sso-token",
            enable_nsfw_for_token=lambda *args, **kwargs: (True, "ok"),
            add_token_to_pools=lambda *args, **kwargs: None,
            rotate_email_provider=lambda **kwargs: None,
            is_mail_related_error=lambda exc: False,
            sleep_with_cancel=lambda *args: None,
            cleanup_runtime_memory=lambda **kwargs: None,
            email_failover_enabled=lambda: True,
            nsfw_enabled=lambda: True,
            cancelled_error=Cancelled,
            retry_error=RetryNeeded,
        )

    def start_browser(self, **kwargs):
        self.started += 1

    def stop_browser(self):
        self.stopped += 1

    def restart_browser(self, **kwargs):
        self.restarted += 1


def test_runner_persists_success_and_stops_browser(tmp_path):
    fake = FakeActions()
    logs = []
    runner = RegistrationRunner(fake.build(), log_callback=logs.append, stop_callback=lambda: False)

    summary = runner.run(1, tmp_path / "accounts.txt")

    assert (summary.success, summary.fail, summary.completed) == (1, 0, 1)
    assert "user@example.com----pw----sso-token" in (tmp_path / "accounts.txt").read_text()
    assert fake.started == 1
    assert fake.stopped == 1
    assert fake.restarted == 0


def test_runner_skips_slot_after_retry_limit(tmp_path):
    fake = FakeActions()
    actions = fake.build()
    actions.open_signup_page = lambda **kwargs: (_ for _ in ()).throw(RetryNeeded("stuck"))
    runner = RegistrationRunner(actions, log_callback=lambda message: None, stop_callback=lambda: False)

    summary = runner.run(1, tmp_path / "accounts.txt")

    assert (summary.success, summary.fail, summary.completed) == (0, 1, 1)
    assert fake.stopped == 1
