import asyncio
import websockets
import os

async def test_stream():
    uri = "ws://localhost:8000/ws/stream"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to WebSocket.")
            
            headers = await websocket.recv()
            print(f"Received first message type: {type(headers)}")
            
            # Expecting bytes for video frame or string for json
            frame_count = 0
            max_frames = 10
            
            while frame_count < max_frames:
                message = await websocket.recv()
                if isinstance(message, bytes):
                    print(f"Frame {frame_count+1}: Received {len(message)} bytes")
                    # data validation: verify it's a valid jpeg start/end if possible, or just size
                    if len(message) > 100:
                         frame_count += 1
                else:
                    print(f"Received text message: {message[:50]}...")
            
            print("Successfully received 10 frames. Stream is ACTIVE.")
            return True
            
    except ConnectionRefusedError:
        print("ERROR: Connection Refused. Is the backend server running?")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    success = loop.run_until_complete(test_stream())
    if not success:
        exit(1)
