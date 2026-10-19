"""TLS passthrough for the native Az vault client's fixed HTTPS port, inside its lab."""

import asyncio


async def forward(reader, writer):
    upstream = None
    tasks = []
    try:
        remote, upstream = await asyncio.open_connection("127.0.0.1", 8448)

        async def copy(source, destination):
            while chunk := await source.read(65536):
                destination.write(chunk)
                await destination.drain()

        tasks = [
            asyncio.create_task(copy(reader, upstream)),
            asyncio.create_task(copy(remote, writer)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for stream in (writer, upstream):
            if stream is not None:
                stream.close()
                try:
                    await stream.wait_closed()
                except ConnectionError:
                    pass


async def main():
    server = await asyncio.start_server(forward, "127.0.0.1", 443)
    print("ready", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
