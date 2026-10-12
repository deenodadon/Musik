import asyncio
import os
import shutil
import signal
import subprocess
import sys
import wave
from dataclasses import asdict

import pytest
from textual.widgets import DataTable

from musik.app import Musik
from musik.core import Store, Track
from musik.daemon import BackgroundPlayer, request, runtime_directory, stop_service
from musik.visualizer import bars, rms_level


def test_visualizer_uses_real_levels():
    assert rms_level({"lavfi.astats.Overall.RMS_level": "-30"}) == .5
    assert rms_level({"lavfi.astats.Overall.RMS_level": "-inf"}) == 0
    assert rms_level({}) == 0
    assert "█" not in bars([0] * 32)
    assert bars([1], height=3) == "█\n█\n█"


@pytest.mark.skipif(not (os.environ.get("MUSIK_MPV") or shutil.which("mpv")), reason="mpv not installed")
async def test_tui_detaches_daemon_advances_reconnects_and_cli_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MUSIK_AUDIO_OUTPUT", "null")
    # Loud tone ensures audio metadata can distinguish it from silence.
    import math
    import struct
    for name, duration in [("first", 3), ("second", 8)]:
        with wave.open(str(tmp_path / f"{name}.wav"), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b"".join(struct.pack("<h", int(10000 * math.sin(i * 2 * math.pi * 440 / 16000)))
                                  for i in range(16000 * duration)))
    store = Store()
    client = BackgroundPlayer(store)
    try:
        app = Musik(local=tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            assert app.ready
            await pilot.press("escape", "a")
            app.query_one(DataTable).move_cursor(row=1)
            await pilot.press("a", "2", "enter")
            await pilot.pause(.3)
            assert app.state.current.title == "first"
            # Wait for measured audio instead of assuming every mpv/CI host
            # has produced metadata after exactly 300 ms. The first tone is
            # long enough that playback still advances after detaching.
            async with asyncio.timeout(2):
                while max(app.player.levels) <= 0:
                    await pilot.pause(.05)
            assert max(app.player.levels) > 0
        # on_unmount returns but playback and queue advancement continue.
        await client.start()
        async with asyncio.timeout(5):
            while client.state.current.title != "second" or client.position <= 0:
                await asyncio.sleep(.05)
                await client.poll()
        assert client.state.current.title == "second"
        assert client.position > 0
        assert not client.idle
        before = client.position
        await client.close()
        # Reopening the interface must not reset position or playback settings.
        reopened = Musik()
        async with reopened.run_test(size=(110, 40)) as pilot:
            assert reopened.state.current.title == "second"
            await pilot.press("ctrl+p")
            await pilot.pause(.1)
            assert (await request(store, "status"))["paused"] is True
            await pilot.press("ctrl+p")
            await pilot.pause(.1)
            assert (await request(store, "status"))["paused"] is False
            await pilot.press("ctrl+up")
            assert reopened.state.volume == 75
            assert (await request(store, "status"))["state"]["volume"] == 75
            await pilot.pause(.2)
            assert reopened.player.position >= before
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "musik.app", "stop",
                                                        stdout=asyncio.subprocess.PIPE)
            stdout, _ = await proc.communicate()
            assert proc.returncode == 0
            assert b"Musik stopped" in stdout
            await pilot.pause(.4)
            assert not reopened.is_running
        assert not (runtime_directory(store) / "service.sock").exists()
        assert not await stop_service(store)
    finally:
        await stop_service(store)


@pytest.mark.skipif(not (os.environ.get("MUSIK_MPV") or shutil.which("mpv")), reason="mpv not installed")
async def test_service_survives_parent_terminal_hangup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MUSIK_AUDIO_OUTPUT", "null")
    store = Store()
    # Launch a terminal/session leader; hang up its entire process group after
    # it starts Musik. The daemon has its own session and must remain reachable.
    code = """
import asyncio
from musik.daemon import BackgroundPlayer
from musik.core import Store
async def main():
    await BackgroundPlayer(Store()).start()
    print('ready', flush=True)
    await asyncio.sleep(60)
asyncio.run(main())
"""
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              start_new_session=True, text=True)
    try:
        assert await asyncio.to_thread(parent.stdout.readline) == "ready\n"
        os.killpg(parent.pid, signal.SIGHUP)
        await asyncio.to_thread(parent.wait, 5)
        await asyncio.sleep(.1)
        response = await request(store, "status")
        assert response["idle"] is True
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        parent.stdout.close()
        await stop_service(store)
