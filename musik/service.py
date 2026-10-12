from .core import Track


class MusicService:
    def __init__(self):
        self._api = None

    @property
    def api(self):
        if self._api is None:
            from ytmusicapi import YTMusic
            self._api = YTMusic()
        return self._api

    def search(self, query):
        return [Track(item["videoId"], item.get("title", "Untitled"),
                      ", ".join(a["name"] for a in item.get("artists", [])))
                for item in self.api.search(query, filter="songs", limit=30)
                if item.get("videoId")]

    def lyrics(self, track):
        if track.source == "local":
            from pathlib import Path
            text = Path(track.id).with_suffix(".lrc")
            return text.read_text() if text.exists() else "No adjacent .lrc lyrics file found."
        result = self.api.get_watch_playlist(videoId=track.id)
        if not result.get("lyrics"):
            return "Lyrics unavailable for this song."
        return self.api.get_lyrics(result["lyrics"])["lyrics"]
