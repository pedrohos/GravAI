import asyncio
from websockets.asyncio.server import serve
from websockets.sync.client import connect
from threading import Thread, Event

class Client:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url

    def interact(self):
        with connect(self.ws_url) as websocket:
            while True:
                name = input("What's your name? ")
                if name == "stop":
                    break

                websocket.send(name)
                print(f">>> {name}")

                greeting = websocket.recv()
                print(f"<<< {greeting}")