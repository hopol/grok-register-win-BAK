from lib.diagnostic_redaction import redact_value, resolve_cpa_file, sanitize_text


def test_resolver_uses_latest_cpa_file(tmp_path):
    cpa_dir = tmp_path / "data" / "cpa"
    cpa_dir.mkdir(parents=True)
    first = cpa_dir / "xai-first.json"
    second = cpa_dir / "xai-second.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    first.touch()
    second.touch()
    second_mtime = first.stat().st_mtime + 10
    import os

    os.utime(second, (second_mtime, second_mtime))

    assert resolve_cpa_file(tmp_path) == second


def test_redacts_nested_tokens_and_text():
    assert redact_value({"access_token": "secret", "nested": {"password": "pw"}}) == {
        "access_token": "<redacted>",
        "nested": {"password": "<redacted>"},
    }
    text = 'Authorization: Bearer abc.def.ghi {"refresh_token":"very-secret"} sso=token;'
    sanitized = sanitize_text(text)
    assert "abc.def.ghi" not in sanitized
    assert "very-secret" not in sanitized
    assert "sso=token" not in sanitized
