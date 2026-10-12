import sys

import pytest

from musik import app


@pytest.mark.parametrize("arguments", [[], ["start"], ["start", "--local", "/music"]])
def test_start_command_opens_terminal(monkeypatch, arguments):
    opened = []

    class Terminal:
        def __init__(self, local=None):
            self.local = local

        def run(self):
            opened.append(self.local)

    monkeypatch.setattr(app, "Musik", Terminal)
    monkeypatch.setattr(sys, "argv", ["musik", *arguments])
    app.main()
    assert opened == (["/music"] if "--local" in arguments else [None])
