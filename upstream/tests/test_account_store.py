import base64
import json

from panel.account_store import AccountStore


def _jwt(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_collects_and_deduplicates_accounts(tmp_path):
    older = tmp_path / "accounts_old.txt"
    newer = tmp_path / "accounts_new.txt"
    token = _jwt({"session_id": "session-1"})
    line = f"user@example.com----password----{token}"
    older.write_text(line + "\n", encoding="utf-8")
    newer.write_text(line + "\nsecond@example.com----pw----token\n", encoding="utf-8")

    accounts = AccountStore(tmp_path).unique()

    assert len(accounts) == 2
    assert accounts[0]["session_id"] == "session-1"


def test_resolve_file_rejects_traversal_and_non_account_files(tmp_path):
    account = tmp_path / "accounts_ok.txt"
    account.write_text("user@example.com", encoding="utf-8")
    other = tmp_path / "notes.txt"
    other.write_text("no", encoding="utf-8")
    store = AccountStore(tmp_path)

    assert store.resolve_file(account.name) == account.resolve()
    assert store.resolve_file("../accounts_ok.txt") is None
    assert store.resolve_file(other.name) is None


def test_reads_new_accounts_directory_and_legacy_root(tmp_path):
    accounts_dir = tmp_path / "data" / "accounts"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts_new.txt").write_text("new@example.com", encoding="utf-8")
    (tmp_path / "accounts_legacy.txt").write_text("legacy@example.com", encoding="utf-8")

    store = AccountStore(tmp_path)

    assert {path.name for path in store.list_files()} == {
        "accounts_new.txt",
        "accounts_legacy.txt",
    }
    assert store.resolve_file("accounts_new.txt").parent == accounts_dir.resolve()
