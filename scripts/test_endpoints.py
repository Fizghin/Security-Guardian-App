import requests
import time

def test_endpoints():
    base_url = "http://localhost:8000/api"
    
    # Test Config
    print("Testing /api/config...")
    try:
        res = requests.post(f"{base_url}/config", json={"test": "value"})
        print(f"Config Status: {res.status_code}, Response: {res.json()}")
    except Exception as e:
        print(f"Config Failed: {e}")

    # Test Panic
    print("Testing /api/panic...")
    try:
        res = requests.post(f"{base_url}/panic")
        print(f"Panic Status: {res.status_code}, Response: {res.json()}")
    except Exception as e:
        print(f"Panic Failed: {e}")

if __name__ == "__main__":
    time.sleep(2) # Wait for server
    test_endpoints()
