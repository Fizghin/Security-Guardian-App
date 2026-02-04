import asyncio
import websockets
import cv2
import numpy as np
import time
import os

async def test_stream():
    uri = "ws://127.0.0.1:8000/ws/stream"
    print(f"Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Receiving frames...")
            
            start_time = time.time()
            frames_received = 0
            bytes_received = 0
            
            for i in range(10):
                data = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                frames_received += 1
                bytes_received += len(data)
                
                # Verify image validity
                nparr = np.frombuffer(data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if img is None:
                    print(f"FRAME {i}: DECODE FAILED! ({len(data)} bytes)")
                else:
                    h, w = img.shape[:2]
                    print(f"FRAME {i}: Valid JPEG | Size: {w}x{h} | Bytes: {len(data)}")
                    
                    # Save first frame to verify content
                    if i == 0:
                        cv2.imwrite("debug_frame.jpg", img)
                        print("Saved debug_frame.jpg")

            duration = time.time() - start_time
            fps = frames_received / duration
            print("="*40)
            print(f"STREAM DIAGNOSIS:")
            print(f"Frames Received: {frames_received}")
            print(f"Total Bytes: {bytes_received}")
            print(f"Average FPS: {fps:.2f}")
            print(f"Status: {'PASS' if frames_received > 0 else 'FAIL'}")
            print("="*40)

    except Exception as e:
        print(f"CONNECTION ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_stream())
