import json

from panel import app as panel_app


def test_email_config_never_returns_secrets():
    public = panel_app.email_config_public(
        {
            "email_provider": "local_worker",
            "local_worker_admin_token": "worker-secret",
            "cloudflare_api_key": "custom-secret",
        }
    )
    assert "local_worker_admin_token" not in public
    assert "custom_api_key" not in public
    assert public["local_worker_token_set"] is True
    assert public["custom_api_key_set"] is True


def test_blank_secret_inputs_preserve_existing_values(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "email_provider": "local_worker",
                "local_worker_admin_token": "keep-worker-secret",
                "cloudflare_api_key": "keep-custom-secret",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_app, "CONFIG_PATH", config_path)

    panel_app.apply_email_config_from_ui(
        {
            "provider": "local_worker",
            "email_failover": True,
            "local_worker_api_base": "https://worker.example.dev",
            "local_worker_domain": "mail.example.dev",
            "local_worker_admin_token": "",
        }
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["local_worker_admin_token"] == "keep-worker-secret"


def test_mutating_api_requires_csrf(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_app, "CONFIG_PATH", config_path)
    panel_app.app.config.update(TESTING=True)

    with panel_app.app.test_client() as client:
        assert client.get("/").status_code == 200
        response = client.post(
            "/api/config/email",
            json={
                "provider": "tempmailer",
                "tempmailer_domain": "bluenode.cc",
            },
        )
        assert response.status_code == 403

        with client.session_transaction() as session:
            token = session["csrf_token"]
        response = client.post(
            "/api/config/email",
            headers={"X-CSRF-Token": token},
            json={
                "provider": "tempmailer",
                "tempmailer_domain": "bluenode.cc",
            },
        )
        assert response.status_code == 200


def test_panel_secret_is_persistent(tmp_path, monkeypatch):
    monkeypatch.delenv("PANEL_SECRET", raising=False)
    first = panel_app.load_panel_secret(tmp_path)
    second = panel_app.load_panel_secret(tmp_path)
    assert first == second
    assert len(first) >= 32
