import json

from lib.grok2api_pool import Grok2ApiPoolPublisher, remote_api_bases


def test_remote_api_bases_normalizes_admin_paths():
    assert remote_api_bases("https://example.com") == [
        "https://example.com",
        "https://example.com/admin/api",
    ]
    assert remote_api_bases("https://example.com/admin") == [
        "https://example.com/admin",
        "https://example.com/admin/api",
    ]


def test_local_pool_is_deduplicated(tmp_path):
    token_file = tmp_path / "pool.json"
    config = {
        "grok2api_local_token_file": str(token_file),
        "grok2api_pool_name": "ssoBasic",
    }
    publisher = Grok2ApiPoolPublisher(
        root_dir=tmp_path,
        config_getter=lambda: config,
        http_get=lambda *args, **kwargs: None,
        http_post=lambda *args, **kwargs: None,
    )

    assert publisher.add_local("sso=token", email="user@example.com") is True
    assert publisher.add_local("token", email="user@example.com") is True

    data = json.loads(token_file.read_text(encoding="utf-8"))
    assert len(data["ssoBasic"]) == 1
