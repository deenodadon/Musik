import sys

import pytest

from musik import app


@pytest.mark.parametrize("arguments", [[], ["start"], ["start", "--local", "/music"]])
def test_start_command_opens_terminal(monkeypatch, arguments):
    opened = []
    checks = []
    monkeypatch.setattr(app, "start_check", lambda store: checks.append(True))

    class Terminal:
        def __init__(self, local=None):
            self.local = local

        def run(self):
            opened.append(self.local)

    monkeypatch.setattr(app, "Musik", Terminal)
    monkeypatch.setattr(sys, "argv", ["musik", *arguments])
    app.main()
    assert opened == (["/music"] if "--local" in arguments else [None])
    assert checks == [True]


@pytest.mark.parametrize('browser,flow', [('edge', 'login_window'), ('firefox', 'login_window')])
def test_login_uses_detected_default_browser(monkeypatch, browser, flow):
    from musik import auth
    calls = []
    monkeypatch.setattr(auth, 'default_browser', lambda: browser)
    monkeypatch.setattr(auth, flow, lambda selected, *args, **kwargs: calls.append(selected) or 2)
    monkeypatch.setattr(sys, 'argv', ['musik', 'login'])
    app.main()
    assert calls == [browser]
