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
