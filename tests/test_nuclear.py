import io
import json
import pytest
from musik import nuclear


def test_registry_can_inspect_any_indexed_plugin(monkeypatch, capsys):
    entries = [{'id': 'provider', 'name': 'Provider', 'repo': 'owner/provider'},
               {'id': 'effect', 'name': 'Effect', 'repo': 'owner/effect'}]
    monkeypatch.setattr(nuclear, 'urlopen', lambda *a, **kw: io.BytesIO(json.dumps({'plugins': entries}).encode()))
    nuclear.list_plugins('effect')
    output = capsys.readouterr().out
    assert 'https://github.com/owner/effect' in output
    assert 'https://github.com/owner/provider' not in output
    assert 'cannot run in Musik yet' in output
    with pytest.raises(ValueError, match='not found'):
        nuclear.list_plugins('missing')
