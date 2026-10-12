from .core import Track
import requests


class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = 20
        return super().request(method, url, **kwargs)


class MusicService:
    def __init__(self):
        self._api = None

    @property
    def api(self):
        if self._api is None:
            from ytmusicapi import YTMusic
            self._api = YTMusic(requests_session=TimeoutSession())
        return self._api

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
