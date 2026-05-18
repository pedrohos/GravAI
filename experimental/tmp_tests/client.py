import asyncio
from websockets.asyncio.server import serve
from websockets.sync.client import connect
from threading import Thread, Event

class Client:
    def __init__(self):
        pass

    def interact(self):
        uri = "ws://localhost:8765"
        with connect(uri) as websocket:
            name = input("What's your name? ")

            websocket.send(name)
            print(f">>> {name}")

            greeting = websocket.recv()
            print(f"<<< {greeting}")

class Server:
    def __init__(self):
        pass

    async def serve(self):
        async with serve(self.echo, "localhost", 8765) as server:
            await server.serve_forever()

    async def echo(self, websocket):
        name = await websocket.recv()
        print(f"<<< {name}")

        greeting = f"Hello {name}!"
        await websocket.send(greeting)
        print(f">>> {greeting}")
        

def run_server(ready: Event):
    async def worker():
        async with serve(Server().echo, "localhost", 8765) as server:
            ready.set()
            await server.serve_forever()

    asyncio.run(worker())

async def main():
    ready = Event()
    thread = Thread(target=run_server, args=(ready,), daemon=True)
    thread.start()
    ready.wait()

    client = Client()
    client.interact()

asyncio.run(main())