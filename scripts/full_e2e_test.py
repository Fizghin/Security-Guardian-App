"""
Full E2E Test - Connects to WebSocket, triggers simulation, monitors stats
"""
import asyncio
import websockets
import requests
import json
import time

async def run_test():
    print("=" * 60)
    print("FULL E2E TEST - AI Security Guardian")
    print("=" * 60)
    
    ws_url = "ws://127.0.0.1:8000/ws/stream"
    api_base = "http://127.0.0.1:8000"
    
    # Initial stats with retry
    print("\n[1] Checking initial stats (waiting for server)...")
    stats = None
    for attempt in range(30):
        try:
            stats = requests.get(f"{api_base}/api/stats", timeout=5).json()
            print(f"    Server online! Initial threat_level: {stats['threat_level']}")
            print(f"    System status: {stats['system_status']}")
            break
        except Exception:
            if attempt % 5 == 0:
                print(f"    Waiting for server... ({attempt+1}/30)")
            time.sleep(2)
            
    if not stats:
        print("    ERROR: Server failed to start after 60 seconds")
        return
    
    # Connect to WebSocket
    print("\n[2] Connecting to WebSocket stream...")
    try:
        async with websockets.connect(ws_url) as ws:
            print("    Connected!")
            
            # Receive a few frames to confirm stream is working
            print("\n[3] Receiving initial frames...")
            for i in range(5):
                data = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"    Frame {i+1}: {len(data)} bytes")
            
            # Trigger simulation
            print("\n[4] Triggering person simulation...")
            resp = requests.post(f"{api_base}/api/simulate/person", timeout=5)
            print(f"    Response: {resp.status_code} - {resp.text}")
            
            # Keep receiving frames while monitoring stats
            print("\n[5] Monitoring for 50 seconds (waiting for Level 4)...")
            start_time = time.time()
            max_threat = 0
            ai_message = ""
            
            while time.time() - start_time < 50:
                # Keep receiving frames (important - this drives the detection loop)
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                
                # Check stats every 2 seconds
                elapsed = time.time() - start_time
                if int(elapsed) % 2 == 0:
                    try:
                        stats = requests.get(f"{api_base}/api/stats", timeout=10).json()
                        if stats['threat_level'] > max_threat:
                            max_threat = stats['threat_level']
                            print(f"    [{elapsed:.0f}s] Threat Level: {stats['threat_level']}")
                        if stats['last_ai_message'] and stats['last_ai_message'] != ai_message:
                            ai_message = stats['last_ai_message']
                            print(f"    [{elapsed:.0f}s] AI Message: {ai_message[:60]}...")
                    except:
                        pass
                
                await asyncio.sleep(0.1)
            
            # Final stats
            print("\n[6] Final Results:")
            stats = requests.get(f"{api_base}/api/stats", timeout=5).json()
            print(f"    Max Threat Level Reached: {max_threat}")
            print(f"    Current Threat Level: {stats['threat_level']}")
            print(f"    Total Events: {stats['total_events']}")
            print(f"    Last AI Message: {stats['last_ai_message'] or '(none)'}")
            
            if max_threat > 0:
                print("\n    [SUCCESS] Threat detection is working!")
            else:
                print("\n    [FAILED] No threat was detected")
                
            if stats['last_ai_message']:
                print("    [SUCCESS] AI response was generated!")
            else:
                print("    [FAILED] No AI response was generated")
                
    except Exception as e:
        print(f"    ERROR: WebSocket connection failed: {e}")
        return
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_test())
