import os
import sys
import subprocess
import time
from pathlib import Path

def check_ffmpeg():
    print("Checking FFmpeg...")
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print("✅ FFmpeg found.")
        return True
    except:
        print("❌ FFmpeg NOT FOUND. Please install FFmpeg and add to PATH.")
        return False

def check_ollama(model_name="jarvis-fast"):
    print(f"Checking Ollama for model {model_name}...")
    try:
        # Check if ollama is installed
        subprocess.run(["ollama", "--version"], capture_output=True, check=True)
        print("✅ Ollama command found.")
        
        # Check if model exists, if not pull it
        print(f"Verifying model {model_name}...")
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if model_name not in result.stdout:
            print(f"Model {model_name} not found. Pulling...")
            subprocess.run(["ollama", "pull", model_name], check=True)
        print(f"✅ Model {model_name} is ready.")
        return True
    except Exception as e:
        print(f"❌ Ollama/Model error: {e}")
        return False

def check_camera(source):
    print(f"Checking Camera source: {source}...")
    import cv2
    cap = cv2.VideoCapture(source)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            print("✅ Camera verified (frame captured).")
            cap.release()
            return True
        cap.release()
    print("❌ Camera NOT found or unusable.")
    return False

def main():
    print("--- [MASTER DEBUGGER] Portability & Setup Fusion ---")
    
    # 1. Environment stuff
    from utils.validate_env import validate_env
    if not validate_env():
        print("Skipping further checks due to env errors.")
        return

    # 2. Dependencies
    f_ok = check_ffmpeg()
    
    # 3. AI Provider check
    provider = os.getenv("AI_PROVIDER", "openai")
    ai_ok = True
    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "jarvis-fast")
        ai_ok = check_ollama(model)
    
    # 4. Camera check
    source = os.getenv("VIDEO_SOURCE", "0")
    if source.isdigit(): source = int(source)
    cam_ok = check_camera(source)

    print("\n--- Setup Report ---")
    print(f"FFmpeg: {'OK' if f_ok else 'FAIL'}")
    print(f"AI ({provider}): {'OK' if ai_ok else 'FAIL'}")
    print(f"Camera: {'OK' if cam_ok else 'FAIL'}")
    
    if all([f_ok, ai_ok, cam_ok]):
        print("\n🚀 SYSTEM READY FOR FUSION.")
    else:
        print("\n⚠️ SYSTEM NOT READY. Please resolve failures above.")

if __name__ == "__main__":
    # Ensure we are in the backend dir context
    os.chdir(Path(__file__).parent.parent)
    main()
