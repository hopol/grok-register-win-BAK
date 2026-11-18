import json

import pytest

from lib.config_store import ConfigError, JsonConfigStore


def test_load_merges_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"proxy": "custom"}), encoding="utf-8")

    loaded = JsonConfigStore(path).load({"proxy": "default", "count": 1})

    assert loaded == {"proxy": "custom", "count": 1}


def test_save_replaces_existing_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"old": true}', encoding="utf-8")

    JsonConfigStore(path).save({"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_invalid_json_raises_config_error(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ConfigError, match="无法读取配置"):
        JsonConfigStore(path).load()
