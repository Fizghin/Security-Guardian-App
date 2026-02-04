import requests
import asyncio
import websockets
import time

def test_panic():
    print("\n--- Testing Panic Endpoint ---")
    try:
        res = requests.post("http://localhost:8000/api/panic")
        print(f"Status: {res.status_code}")
        print(f"Response: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

async def test_stream_duration():
    print("\n--- Testing Stream Duration (30s) ---")
    uri = "ws://localhost:8000/ws/stream"
    try:
        async with websockets.connect(uri) as websocket:
            start = time.time()
            frames = 0
            while time.time() - start < 30:
                await websocket.recv()
                frames += 1
                if frames % 100 == 0:
                    print(f"Received {frames} frames...")
            
            print(f"Success! Received {frames} frames in 30s.")
            print(f"FPS: {frames/30:.2f}")

    except Exception as e:
        print(f"Stream Error: {e}")

if __name__ == "__main__":
    test_panic()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(test_stream_duration())
