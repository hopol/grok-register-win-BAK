import json

from panel.account_store import AccountStore
from panel.cpa_service import CpaConversionService


def build_service(tmp_path, converter):
    account_dir = tmp_path / "accounts"
    account_dir.mkdir()
    store = AccountStore(tmp_path, accounts_dir=account_dir)
    return CpaConversionService(
        account_store=store,
        cpa_dir=tmp_path / "cpa",
        config_loader=lambda: {"proxy": "http://proxy"},
        default_proxy=lambda: "http://default",
        converter=converter,
        normalize_sso=lambda token: token.strip(),
        fingerprint=lambda token: f"fp-{token}",
        safe_filename=lambda value: value.replace("@", "_"),
        log_callback=lambda message: None,
        delay=0,
    )


def test_service_converts_and_indexes_account(tmp_path):
    service = build_service(
        tmp_path,
        lambda sso, email, proxy: {
            "email": email,
            "sso": sso,
            "auth_kind": "oauth",
            "proxy_used": proxy,
        },
    )
    service.start()

    queued, _ = service.enqueue(email="user@example.com", sso="token", password="pw")
    assert queued is True
    assert service.wait_until_idle(2) is True

    output = tmp_path / "cpa" / "xai-user_example.com.json"
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["password"] == "pw"
    assert data["proxy_used"] == "http://proxy"
    assert service.status()["files"] == 1


def test_service_records_conversion_failure(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("conversion failed")

    service = build_service(tmp_path, fail)
    service.start()
    service.enqueue(email="user@example.com", sso="token")
    assert service.wait_until_idle(2) is True

    assert service.status()["fail"] == 1
    assert "conversion failed" in service.failed_path.read_text(encoding="utf-8")


def test_start_is_idempotent(tmp_path):
    service = build_service(tmp_path, lambda *args, **kwargs: {})
    service.start()
    first_queue = service.queue
    service.start()
    assert service.queue is first_queue
