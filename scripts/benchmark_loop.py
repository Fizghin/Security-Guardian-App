import asyncio
import websockets
import time
import json

async def benchmark_fps():
    uri = "ws://localhost:8000/ws/stream"
    print(f"Connecting to {uri} to benchmark FPS...")
    
    try:
        async with websockets.connect(uri) as websocket:
            start_time = time.time()
            frames = 0
            duration = 10.0 # Run for 10 seconds
            
            print(f"Sampling for {duration} seconds...")
            
            while time.time() - start_time < duration:
                message = await websocket.recv()
                if isinstance(message, bytes):
                    frames += 1
            
            fps = frames / duration
            print(f"Benchmark Complete.")
            print(f"Total Frames: {frames}")
            print(f"Average FPS: {fps:.2f}")
            
            if fps < 10:
                print("WARNING: FPS is low. Freezing risk high.")
            else:
                print("SUCCESS: FPS is stable.")

    except Exception as e:
        print(f"Benchmark Error: {e}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(benchmark_fps())
