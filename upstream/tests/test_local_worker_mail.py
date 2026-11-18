from lib.local_worker_mail import LocalWorkerMailbox


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def test_create_and_filter_messages():
    calls = []

    def post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        return FakeResponse({"ok": True, "emails": ["user@liziai.cloud"]})

    def get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        if url.endswith("/admin/all"):
            return FakeResponse(
                {
                    "messages": [
                        {"id": "1", "to_address": "user@liziai.cloud"},
                        {"id": "2", "to_address": "other@liziai.cloud"},
                    ]
                }
            )
        return FakeResponse({"message": {"id": "1", "body": "code 123456"}})

    client = LocalWorkerMailbox(
        "https://worker.example.dev/",
        "secret-token",
        "liziai.cloud",
        http_get=get,
        http_post=post,
    )

    assert client.create_address() == "user@liziai.cloud"
    assert client.list_messages("user@liziai.cloud") == [
        {"id": "1", "to_address": "user@liziai.cloud"}
    ]
    assert client.get_message("1", "user@liziai.cloud")["body"] == "code 123456"
    assert calls[0][2]["headers"]["X-Admin-Token"] == "secret-token"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer secret-token"
