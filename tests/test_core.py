from pathlib import Path

import pytest

from musik.core import State, Store, Track, scan_local


def test_persistence_and_queue_edits(tmp_path):
    tracks = [Track(str(i), f"Song {i}") for i in range(3)]
    state = State(queue=tracks.copy(), index=1, favorites=[tracks[0]])
    assert state.move(1, -1) == 0
    assert state.current == tracks[1]
    state.remove(1)
    assert state.current == tracks[1]
    state.remember(tracks[1])
    state.remember(tracks[1])
    store = Store(tmp_path)
    store.save(state)
    assert store.load() == state
    assert len(state.history) == 1


def test_repeat_shuffle_and_end_of_queue():
    state = State(queue=[Track("a", "A"), Track("b", "B")])
    assert state.advance().id == "a"
    assert state.advance().id == "b"
    assert state.advance() is None
    state.repeat = True
    assert state.advance().id == "a"
    state.shuffle = True
    assert state.advance().id == "b"


def test_scan_and_corrupt_state(tmp_path):
    (tmp_path / "song.FLAC").touch()
    (tmp_path / "notes.txt").touch()
    assert len(scan_local(tmp_path)) == 1
    (tmp_path / "state.json").write_text("broken")
    with pytest.raises(ValueError, match="Invalid saved state"):
        Store(tmp_path).load()
