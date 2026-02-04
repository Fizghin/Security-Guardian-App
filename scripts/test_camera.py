"""Quick camera diagnostic script - Run this to test available cameras."""
import cv2
import sys

def test_cameras():
    print("=" * 60)
    print("CAMERA DIAGNOSTIC TOOL")
    print("=" * 60)
    
    working_cameras = []
    
    for i in range(10):
        print(f"\n[{i}] Testing camera index {i}...")
        
        # Try DirectShow (Windows) with exception handling
        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    print(f"    [OK] DirectShow: WORKING - Shape: {frame.shape}")
                    working_cameras.append((i, "DSHOW", frame.shape))
                    cap.release()
                    continue
                else:
                    print(f"    [X] DirectShow: Opens but can't read frame")
            else:
                print(f"    [X] DirectShow: Can't open")
            cap.release()
        except Exception as e:
            print(f"    [X] DirectShow: Error - {e}")
        
        # Try default backend
        try:
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    print(f"    [OK] Default: WORKING - Shape: {frame.shape}")
                    working_cameras.append((i, "DEFAULT", frame.shape))
                else:
                    print(f"    [X] Default: Opens but can't read frame")
            else:
                print(f"    [X] Default: Can't open")
            cap.release()
        except Exception as e:
            print(f"    [X] Default: Error - {e}")
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if working_cameras:
        print(f"\n[OK] Found {len(working_cameras)} working camera(s):\n")
        for idx, backend, shape in working_cameras:
            print(f"  Camera {idx}: {backend} backend, Resolution: {shape[1]}x{shape[0]}")
        print(f"\nRecommended: Set VIDEO_SOURCE={working_cameras[0][0]} in your .env file")
    else:
        print("\n[WARNING] NO WORKING CAMERAS DETECTED!")
        print("\nTroubleshooting:")
        print("  1. Check if camera is connected")
        print("  2. Check if another app is using the camera (close Zoom, Teams, etc.)")
        print("  3. Try unplugging/replugging USB camera")
        print("  4. Check Windows Device Manager for camera status")
    
    print("\n" + "=" * 60)
    return len(working_cameras) > 0

if __name__ == "__main__":
    success = test_cameras()
    sys.exit(0 if success else 1)
