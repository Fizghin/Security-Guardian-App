import requests
import os

def test_upload_face():
    url = "http://localhost:8002/api/faces/upload"
    
    # Create a dummy image file
    with open("test_face.jpg", "wb") as f:
        f.write(os.urandom(1024))
        
    files = {'file': ('test_face.jpg', open('test_face.jpg', 'rb'), 'image/jpeg')}
    data = {'name': 'Test User Upload'}
    
    try:
        response = requests.post(url, files=files, data=data)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        # Verify file exists
        expected_path = os.path.join("backend", "faces_db", "Test_User_Upload", "test_face.jpg")
        if os.path.exists(expected_path):
            print("SUCCESS: File saved correctly.")
        else:
            print(f"FAILURE: File not found at {expected_path}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup
        if os.path.exists("test_face.jpg"):
            os.remove("test_face.jpg")

if __name__ == "__main__":
    test_upload_face()
