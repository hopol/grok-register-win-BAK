from lib.browser_proxy import (
    apply_browser_proxy_option,
    is_proxy_connection_error,
    strip_proxy_auth,
)


def test_strip_proxy_auth_preserves_endpoint():
    assert strip_proxy_auth("http://user:pass@127.0.0.1:7890") == "http://127.0.0.1:7890"


def test_connection_error_matches_configured_proxy_endpoint():
    error = RuntimeError("Failed to connect to 127.0.0.1 port 7897")
    assert is_proxy_connection_error(error, "http://127.0.0.1:7897") is True
    assert is_proxy_connection_error(error, "") is False


def test_browser_option_falls_back_to_argument():
    class Options:
        def __init__(self):
            self.args = []

        def set_argument(self, *args):
            self.args.append(args)

    options = Options()
    apply_browser_proxy_option(options, "http://127.0.0.1:7897")
    assert options.args == [("--proxy-server=http://127.0.0.1:7897",)]
