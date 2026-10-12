from .core import Track, Store
import json
import os
import tempfile
from pathlib import Path
from .auth import cookie_path, cookie_jar, auth_headers
import requests
import http.cookiejar


class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = 20
        return super().request(method, url, **kwargs)


def song_track(item):
    if not isinstance(item, dict) or not item.get("videoId") or item.get("isAvailable") is False:
        return None
    names = [artist.get("name") for artist in (item.get("artists") or [])
             if isinstance(artist, dict) and isinstance(artist.get("name"), str)]
    return Track(item["videoId"], item.get("title") or "Untitled", ", ".join(names) or "Unknown artist")


def explain_library_error(error):
    """Actionable categories without echoing response bodies or credentials."""
    import re
    if isinstance(error, requests.exceptions.Timeout):
        return "YouTube request timed out. Try opening the playlist again."
    if isinstance(error, requests.exceptions.ConnectionError):
        return "Could not connect to YouTube. Check your connection or proxy."
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is None:
        match = re.search(r"\b(401|403|404|429|5\d\d)\b", str(error))
        status = int(match.group(1)) if match else None
    if status in (401, 403):
        return "YouTube denied playlist access. Re-import the browser account that owns this playlist with musik login --browser brave."
    if status == 404:
        return "Playlist not found or unavailable to this account. Press 5 to refresh your library."
    if status == 429:
        return "YouTube rate-limited the request. Wait a little and try again."
    if status and status >= 500:
        return "YouTube returned a server error. Try again later."
    if isinstance(error, (KeyError, TypeError, IndexError)):
        return f"Unsupported YouTube playlist response ({type(error).__name__}). Refresh your sign-in and retry."
    if isinstance(error, (RuntimeError, ValueError, http.cookiejar.LoadError)):
        return "Account session unavailable. Run musik login --browser brave, then press 5 to refresh."
    return f"Playlist loading failed ({type(error).__name__}). Report this message so the cause can be checked."


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
        return [track for item in (result.get("tracks") or []) if (track := song_track(item))]

    def search(self, query):
        return [track for item in self.api.search(query, filter="songs", limit=30)
                if (track := song_track(item))]

    def radio(self, track, limit=25):
        if track.source == "local":
            from pathlib import Path
            from .core import scan_local
            return [item for item in scan_local(Path(track.id).parent) if item != track][:limit]
        result = self.api.get_watch_playlist(videoId=track.id, radio=True, limit=limit + 1)
        seen = {track.id}
        tracks = []
        for item in (result.get("tracks") or []):
            if not isinstance(item, dict):
                continue
            video_id = item.get("videoId")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            track = song_track(item)
            if track is None:
                continue
            tracks.append(track)
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
