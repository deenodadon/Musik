"""mpv's Windows named pipe, adapted to the player reader/writer interface."""
import asyncio


class Pipe:
    def __init__(self, file):
        self.file = file

    @classmethod
    async def connect(cls, name):
        file = await asyncio.to_thread(open, name, 'r+b', buffering=0)
        return cls(file)

    async def readline(self):
        return await asyncio.to_thread(self.file.readline)

    def write(self, data):
        self.file.write(data)

    async def drain(self):
        pass

    def close(self):
        self.file.close()

    async def wait_closed(self):
        pass
