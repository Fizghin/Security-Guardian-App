import asyncio
import websockets
import sys

async def test_ws():
    ws_url = "ws://127.0.0.1:8002/ws/stream"
    try:
        async with websockets.connect(ws_url) as ws:
            print("Connected to WebSocket, receiving frames...")
            for i in range(5):
                data = await ws.recv()
                if isinstance(data, bytes):
                    print(f"Received frame {i+1}, size {len(data)} bytes")
                else:
                    print(f"Received non-bytes message: {data}")
    except Exception as e:
        print(f"WebSocket test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_ws())
