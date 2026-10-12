"""mpv's full-duplex Windows pipe using native overlapped asynchronous I/O."""
import asyncio


class Pipe:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, name):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await loop.create_pipe_connection(lambda: protocol, name)
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return cls(reader, writer)

    async def readline(self):
        return await self.reader.readline()

    def write(self, data):
        self.writer.write(data)

    async def drain(self):
        await self.writer.drain()

    def close(self):
        self.writer.close()

    async def wait_closed(self):
        await self.writer.wait_closed()
