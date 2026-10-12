from .core import Track, Store
import json
import re
import unicodedata
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


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", value))


def query_matches(query, value):
    wanted = set(normalized(query).split())
    return bool(wanted) and wanted.issubset(set(normalized(value).split()))


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
        account = read_account(self.store)
        stamp = (path.stat().st_mtime_ns, account) if path.exists() else None
        if self._api is None or stamp != self._auth_stamp:
            from ytmusicapi import YTMusic
            jar = cookie_jar(self.store)
            self._api = YTMusic(auth=auth_headers(jar, account) if jar else None, requests_session=TimeoutSession())
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

    def search_videos(self, query):
        """Search all YouTube uploads, without music-catalog credit filtering."""
        from urllib.parse import urlparse, parse_qs
        from yt_dlp import YoutubeDL
        from .stream import extractor_options
        parsed = urlparse(query)
        video_id = None
        if parsed.scheme in ("http", "https"):
            host = (parsed.hostname or "").lower()
            if host == "youtu.be":
                video_id = parsed.path.strip("/").split("/")[0]
            elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
                video_id = parse_qs(parsed.query).get("v", [None])[0]
                if not video_id and parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
                    video_id = parsed.path.split("/")[2]
            if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise ValueError("Paste a YouTube video link, or search by title.")
        target = f"https://www.youtube.com/watch?v={video_id}" if video_id else f"ytsearch50:{query}"
        options = dict(extractor_options(), extract_flat=True, skip_download=True)
        with YoutubeDL(options) as ydl:
            data = ydl.extract_info(target, download=False)
        entries = [data] if video_id else (data or {}).get("entries", [])
        rows, seen = [], set()
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            identity = item.get("id")
            if not identity or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity) or identity in seen:
                continue
            seen.add(identity)
            rows.append(Track(identity, item.get("title") or "Untitled video",
                              item.get("channel") or item.get("uploader") or "Unknown channel", "video"))
        return rows

    def search(self, query):
        artists = []
        for item in self.api.search(query, filter="artists", limit=20):
            if not isinstance(item, dict):
                continue
            name = item.get("artist") or item.get("title") or ""
            if item.get("browseId") and query_matches(query, name):
                artists.append(Track(item["browseId"], name, "Artist", "artist"))
        exact = next((artist for artist in artists if normalized(artist.title) == normalized(query)), None)
        if exact is not None:
            # Use the artist's own release shelves, never a broad album query.
            catalog = self.artist_catalog(exact, full_songs=True)
            songs = [item for item in catalog if item.source == "youtube"]
            albums = [item for item in catalog if item.source == "album"]
            seen = {song.id for song in songs}
            if len(songs) < 25:
                for item in self.api.search(exact.title, filter="songs", limit=100):
                    if not isinstance(item, dict):
                        continue
                    credited = [a for a in (item.get("artists") or []) if isinstance(a, dict)]
                    matches = any(a.get("id") == exact.id or normalized(a.get("name", "")) == normalized(exact.title)
                                  for a in credited)
                    track = song_track(item)
                    if matches and track and track.id not in seen:
                        songs.append(track)
                        seen.add(track.id)
            # Smaller top-song shelves can omit released tracks; load official
            # album tracks until the minimum is met or releases are exhausted.
            for album in albums:
                if len(songs) >= 25:
                    break
                for track in self.album_tracks(album):
                    if track.id not in seen:
                        songs.append(track)
                        seen.add(track.id)
            precise_artists = [artist for artist in artists if normalized(artist.title) == normalized(query)]
            return [*precise_artists, *songs, *albums]
        songs = []
        seen = set()
        for item in self.api.search(query, filter="songs", limit=100):
            track = song_track(item)
            if track and track.id not in seen and query_matches(query, f"{track.title} {track.artist}"):
                songs.append(track)
                seen.add(track.id)
        albums = []
        for item in self.api.search(query, filter="albums", limit=50):
            if not isinstance(item, dict) or not item.get("browseId"):
                continue
            names = ", ".join(a["name"] for a in (item.get("artists") or [])
                              if isinstance(a, dict) and isinstance(a.get("name"), str))
            if query_matches(query, f"{item.get('title', '')} {names}"):
                albums.append(Track(item["browseId"], item.get("title") or "Untitled album", names or "Unknown artist", "album"))
        return [*songs, *albums, *artists]

    def artist_catalog(self, artist, full_songs=False):
        data = self.api.get_artist(artist.id)
        name = data.get("name") or artist.title
        rows = []
        songs = data.get("songs") or {}
        for item in songs.get("results") or []:
            if track := song_track(item):
                if track.artist == "Unknown artist":
                    track = Track(track.id, track.title, name)
                rows.append(track)
        if full_songs and songs.get("browseId"):
            complete = self.api.get_playlist(songs["browseId"], limit=None)
            seen_songs = {track.id for track in rows}
            for item in complete.get("tracks") or []:
                if track := song_track(item):
                    if track.id not in seen_songs:
                        rows.append(Track(track.id, track.title, name if track.artist == "Unknown artist" else track.artist))
                        seen_songs.add(track.id)
        if songs.get("browseId") and not full_songs:
            rows.append(Track(songs["browseId"], "All songs", name, "playlist"))
        seen = set()
        for section in ("albums", "singles"):
            content = data.get(section) or {}
            releases = content.get("results") or []
            if content.get("browseId") and content.get("params"):
                releases = self.api.get_artist_albums(content["browseId"], content["params"], limit=None)
            for item in releases:
                if not isinstance(item, dict) or not item.get("browseId") or item["browseId"] in seen:
                    continue
                seen.add(item["browseId"])
                label = " · ".join(str(value) for value in (name, item.get("year"), "Single" if section == "singles" else None) if value)
                rows.append(Track(item["browseId"], item.get("title") or "Untitled album", label, "album"))
        return rows

    def album_tracks(self, album):
        data = self.api.get_album(album.id)
        artists = data.get("artists") or []
        name = ", ".join(a["name"] for a in artists if isinstance(a, dict) and isinstance(a.get("name"), str)) or album.artist
        tracks = []
        for item in data.get("tracks") or []:
            if track := song_track(item):
                if track.artist == "Unknown artist":
                    track = Track(track.id, track.title, name)
                tracks.append(track)
        return tracks

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


def save_account(store, account):
    fd, name = tempfile.mkstemp(dir=store.directory, prefix=".account-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"index": account}, handle)
        os.replace(name, store.directory / "account.json")
    finally:
        Path(name).unlink(missing_ok=True)


def read_account(store):
    try:
        return max(0, int(json.loads((store.directory / "account.json").read_text())["index"]))
    except (OSError, ValueError, TypeError, KeyError):
        return 0
