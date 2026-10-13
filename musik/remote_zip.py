"""Inspect remote ZIP directories with bounded HTTP range reads, never extract files."""
import io
import re
import zipfile
import requests


class RangeReader(io.RawIOBase):
    def __init__(self, url):
        self.url = url
        self.position = 0
        self.used = 0
        self.size = None
        self._fetch(0, 1)

    def _fetch(self, start, length):
        if length > 8_000_000 or self.used + length > 10_000_000:
            raise RuntimeError('ZIP directory is too large to inspect safely')
        try:
            with requests.get(self.url, headers={'Range': f'bytes={start}-{start+length-1}',
                                                  'Accept-Encoding': 'identity'}, stream=True, timeout=12) as response:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if response.status_code != 206 or not match or tuple(map(int, match.groups()[:2])) != (start, start+length-1):
                    raise RuntimeError('This ZIP server does not support byte-range streaming')
                size = int(match[3])
                if self.size is not None and size != self.size:
                    raise RuntimeError('The remote ZIP changed; reopen this torrent')
                self.size = size
                data = response.raw.read(length+1, decode_content=False)
                if len(data) != length:
                    raise RuntimeError('The ZIP server returned incomplete data')
                self.used += length
                return data
        except requests.RequestException:
            raise RuntimeError('Could not read the remote ZIP directory') from None

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.position+offset if whence == 1 else self.size+offset
        if position < 0:
            raise ValueError('Negative ZIP offset')
        self.position = position
        return position

    def read(self, size=-1):
        length = max(0, self.size-self.position) if size < 0 else min(size, max(0, self.size-self.position))
        if not length:
            return b''
        data = self._fetch(self.position, length)
        self.position += length
        return data


def audio_entries(url, extensions):
    try:
        with RangeReader(url) as source, zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > 100_000:
                raise RuntimeError('ZIP contains too many entries')
            return [entry.filename for entry in entries
                    if not entry.is_dir() and not entry.flag_bits & 1
                    and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    and not entry.filename.startswith('/')
                    and not any(part == '..' for part in entry.filename.split('/'))
                    and entry.filename.rsplit('.', 1)[-1].lower() in {extension[1:] for extension in extensions}]
    except (zipfile.BadZipFile, ValueError, NotImplementedError):
        raise RuntimeError('This ZIP cannot be streamed (invalid or unsupported archive)') from None
