import asyncio
import math
import os
import shutil
import struct
import wave

import pytest

from musik.core import State, Track
from musik.player import Player


@pytest.mark.skipif(not (os.environ.get("MUSIK_MPV") or shutil.which("mpv")), reason="mpv not installed")
async def test_real_mpv_playback_controls_and_eof(tmp_path):
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"".join(struct.pack("<h", int(1000 * math.sin(i * 2 * math.pi * 440 / 16000))) for i in range(48000)))
    player = Player(audio_output="null")
    try:
        await player.start()
        state = State(volume=45, speed=1.25, equalizer="bass")
        await player.play(Track(str(path), "Tone", source="local"), state)
        for _ in range(50):
            if player.duration > 0 and player.position > 0:
                break
            await asyncio.sleep(0.05)
        assert player.duration == pytest.approx(3.0)
        assert player.position > 0
        assert await player.command("get_property", "volume") == 45
        assert await player.command("get_property", "speed") == 1.25
        await player.command("set_property", "pause", True)
        assert await player.command("get_property", "pause") is True
        await player.command("seek", 1, "absolute")
        state.equalizer = "vocal"
        await player.settings(state)
        state.equalizer = "flat"
        await player.settings(state)
        await player.command("set_property", "pause", False)
        async with asyncio.timeout(6):
            while True:
                event = await player.events.get()
                if event.get("reason") == "eof":
                    assert event["_musik_generation"] == player.play_generation == 1
                    break
                assert event.get("reason") != "error"
    finally:
        await player.close()
    assert player.process.returncode is not None


@pytest.mark.parametrize('message,category', [
    ('HTTP error 403 Forbidden https://private-stream?secret=COOKIE', 'HTTP 403'),
    ('Server returned 404 Not Found', 'HTTP 404'),
    ('Connection timed out', 'network or proxy'),
    ('Failed to initialize audio', 'audio output device'),
    ('Failed to recognize file format', 'decode'),
])
def test_playback_failure_categories_do_not_expose_stream_data(message, category):
    from musik.player import playback_error
    detail = playback_error(message)
    assert category in detail
    assert 'private-stream' not in detail and 'COOKIE' not in detail


async def test_audio_retry_refreshes_link_and_excludes_failed_format(tmp_path, monkeypatch):
    from musik import stream
    from musik.core import Store
    calls = []
    def resolve(url, store, **options):
        calls.append(options)
        return {'url': 'https://example.org/audio.m3u8', 'format_id': 'hls',
                'http_headers': {'User-Agent': 'test-agent', 'Cookie': 'private-cookie'}}
    monkeypatch.setattr(stream, 'resolve_audio', resolve)
    player = Player(store=Store(tmp_path))
    player.format_id = 'failed-format'
    commands = []
    async def command(*args):
        commands.append(args)
    monkeypatch.setattr(player, 'command', command)
    await player.play(Track('a', 'A'), State(), retry_stream=True)
    assert calls == [{'alternate_audio': True, 'rejected_format': 'failed-format'}]
    assert player.stream_retry_used and player.format_id == 'hls'
    assert ('loadfile', 'https://example.org/audio.m3u8', 'replace') in commands
    assert not any('private-cookie' in str(command) for command in commands)
