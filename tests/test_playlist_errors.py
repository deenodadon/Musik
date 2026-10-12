import requests

from musik.core import Store, Track
from musik.service import MusicService, explain_library_error


def test_playlist_metadata_missing_and_unavailable_tracks(tmp_path):
    service = MusicService(Store(tmp_path))
    class API:
        def get_playlist(self, *args, **kwargs):
            return {'tracks': [None, {'videoId': 'a', 'artists': None},
                               {'videoId': 'b', 'artists': [None, {}, {'name': 'Artist'}]},
                               {'videoId': 'hidden', 'isAvailable': False}, {'title': 'Deleted'}]}
    service._api = API()
    assert service.playlist_tracks(Track('PL', 'Mix', source='playlist')) == [
        Track('a', 'Untitled'), Track('b', 'Untitled', 'Artist')]


def test_playlist_errors_distinguish_network_access_and_parser():
    assert 'timed out' in explain_library_error(requests.exceptions.Timeout())
    assert 'connect' in explain_library_error(requests.exceptions.ConnectionError())
    response = requests.Response()
    response.status_code = 403
    assert 'denied playlist access' in explain_library_error(requests.HTTPError(response=response))
    assert 'KeyError' in explain_library_error(KeyError('sensitive response'))
    assert 'sensitive' not in explain_library_error(KeyError('sensitive response'))
    assert 'rate-limited' in explain_library_error(RuntimeError('HTTP 429'))
