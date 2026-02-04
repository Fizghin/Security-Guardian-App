import pyttsx3
import sys

def speak(text):
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 150)
        engine.setProperty('volume', 1.0)
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        text = sys.argv[1]
        speak(text)
    else:
        print("Usage: python speak.py 'Text to speak'")
