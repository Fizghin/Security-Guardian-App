import asyncio
import websockets
import os

async def test_stream_and_save():
    uri = "ws://localhost:8000/ws/stream"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to WebSocket.")
            
            # First message might be text (if I am lucky) or just bytes if the protocol is simple
            # In main.py: 
            # await websocket.send_bytes(buffer.tobytes())
            
            # It does NOT send headers first in the loop, wait.
            # In video_service.py generate_frames IT DOES send multipart headers.
            # BUT main.py websocket_endpoint sends RAW BYTES or JSON TEXT.
            
            # Let's read a few messages
            saved_count = 0
            max_save = 3
            
            while saved_count < max_save:
                message = await websocket.recv()
                
                if isinstance(message, bytes):
                    print(f"Received {len(message)} bytes.")
                    filename = f"debug_frame_{saved_count}.jpg"
                    with open(filename, "wb") as f:
                        f.write(message)
                    print(f"Saved {filename}")
                    saved_count += 1
                else:
                    print(f"Received text: {message[:50]}...")
            
            print("Done. Please check the saved .jpg files to ensure they are valid images.")
            return True

    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(test_stream_and_save())
