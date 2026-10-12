from .core import Track, Store
import json
import os
import tempfile
from pathlib import Path
from .auth import cookie_path, cookie_jar, auth_headers
import requests


class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = 20
        return super().request(method, url, **kwargs)


class MusicService:
    def __init__(self, store=None):
        self.store = store or Store()
        self._api = None
        self._auth_stamp = None

    @property
    def api(self):
        path = cookie_path(self.store)
        stamp = path.stat().st_mtime_ns if path.exists() else None
        if self._api is None or stamp != self._auth_stamp:
            from ytmusicapi import YTMusic
            jar = cookie_jar(self.store)
            self._api = YTMusic(auth=auth_headers(jar) if jar else None, requests_session=TimeoutSession())
            self._auth_stamp = stamp
        return self._api

    def playlists(self):
        if not cookie_path(self.store).exists():
            raise RuntimeError("Sign in first with musik login --browser firefox")
        playlists = self.api.get_library_playlists(limit=None)
        save_playlists(self.store, playlists)
        return [Track(p["playlistId"], p.get("title") or "Untitled playlist", "Your playlist", "playlist")
                for p in playlists if p.get("playlistId")]

    def playlist_tracks(self, playlist):
        result = self.api.get_playlist(playlist.id, limit=None)
        return [Track(p["videoId"], p.get("title") or "Untitled",
                      ", ".join(a["name"] for a in p.get("artists", [])) or "Unknown artist")
                for p in result.get("tracks", []) if p.get("videoId")]

    def search(self, query):
        return [Track(item["videoId"], item.get("title", "Untitled"),
                      ", ".join(a["name"] for a in item.get("artists", [])))
                for item in self.api.search(query, filter="songs", limit=30)
                if item.get("videoId")]

    def radio(self, track, limit=25):
        if track.source == "local":
            from pathlib import Path
            from .core import scan_local
            return [item for item in scan_local(Path(track.id).parent) if item != track][:limit]
        result = self.api.get_watch_playlist(videoId=track.id, radio=True, limit=limit + 1)
        seen = {track.id}
        tracks = []
        for item in result.get("tracks", []):
            video_id = item.get("videoId")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            tracks.append(Track(video_id, item.get("title") or "Untitled",
                                ", ".join(a["name"] for a in item.get("artists", [])) or "Unknown artist"))
            if len(tracks) >= limit:
                break
        return tracks

    def lyrics(self, track):
        if track.source == "local":
            from pathlib import Path
            text = Path(track.id).with_suffix(".lrc")
            return text.read_text() if text.exists() else "No adjacent .lrc lyrics file found."
        result = self.api.get_watch_playlist(videoId=track.id)
        if not result.get("lyrics"):
            return "Lyrics unavailable for this song."
        return self.api.get_lyrics(result["lyrics"])["lyrics"]


def save_playlists(store, playlists):
    store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=store.directory, prefix=".playlists-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump([{"playlistId": p["playlistId"], "title": p.get("title", "Untitled playlist")}
                       for p in playlists if p.get("playlistId")], handle)
        os.replace(name, store.directory / "playlists.json")
    finally:
        Path(name).unlink(missing_ok=True)


def cached_playlists(store):
    try:
        data = json.loads((store.directory / "playlists.json").read_text())
        return [Track(p["playlistId"], p["title"], "Your playlist", "playlist") for p in data]
    except (OSError, ValueError, KeyError, TypeError):
        return []
