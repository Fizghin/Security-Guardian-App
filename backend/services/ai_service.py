import pyttsx3
import os
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

class AIService:
    def __init__(self, provider=None):
        self.provider = provider or os.getenv("AI_PROVIDER", "ollama")
        print(f"DEBUG: AIService Provider = '{self.provider}'")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview")
        self.client = None
        self.tts_engine = None
        
        # Initialize pyttsx3
        try:
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', 150) # Speed
            self.tts_engine.setProperty('volume', 1.0) 
            print("AIService: TTS Local Engine Initialized")
        except Exception as e:
            print(f"AIService Warning: Failed to init TTS: {e}")

        api_key = os.getenv("OPENAI_API_KEY")
        self.personality = {
            "intimidation": 50,
            "humor": 20,
            "persistence": 80
        }
        
        # Initialize system prompt (was missing - caused 'no attribute' error)
        self.system_prompt = """
        You are an advanced AI Security Guardian. 
        Your goal is to protect the premises. 
        You speak with authority but can escalate from polite to aggressive.
        Keep responses short and direct (1-2 sentences max).
        Current settings:
        - Intimidation: 5/10
        - Humor: 2/10
        - Persistence: 8/10
        """
        
        if self.provider == "openai":
            if api_key and AsyncOpenAI:
                try:
                    self.client = AsyncOpenAI(api_key=api_key)
                    print("AIService: Async OpenAI Client Initialized")
                except Exception as e:
                    print(f"AIService Warning: Failed to init OpenAI: {e}")
            else:
                print("AIService Warning: OPENAI_API_KEY not found or AsyncOpenAI missing. AI features disabled.")

    def update_config(self, config: dict):
        """
        Updates AI personality and behavior parameters dynamically.
        """
        if "intimidation" in config:
            self.personality["intimidation"] = config["intimidation"]
        if "humor" in config:
            self.personality["humor"] = config["humor"]
        if "persistence" in config:
            self.personality["persistence"] = config["persistence"]
        print(f"AIService Config Updated: {self.personality}")
        
        self.system_prompt = """
        You are an advanced AI Security Guardian. 
        Your goal is to protect the premises. 
        You speak with authority but can escalate from polite to aggressive.
        Current settings:
        - Intimidation: 5/10
        - Humor: 2/10
        - Persistence: 8/10
        """

    async def generate_response(self, user_input, context=[]):
        """
        Generates a text response based on input and context (Async).
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.append({"role": "user", "content": user_input})

        try:
            if self.provider == "openai" and self.client:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                )
                return response.choices[0].message.content
            elif self.provider == "ollama":
                import ollama
                # We use jarvis-fast as specified by user
                model_name = os.getenv("OLLAMA_MODEL", "jarvis-fast")
                response = await ollama.AsyncClient().chat(
                    model=model_name,
                    messages=messages,
                )
                return response['message']['content']
            else:
                return "Neural core offline. Provider misconfigured or API Key missing."
                
        except Exception as e:
            print(f"AI Generation Error ({self.provider}): {e}")
            # Use the 300-message fallback system based on threat level
            try:
                import sys
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
                from data.fallback_messages import get_fallback_message
                # Get threat level from brain service if available
                threat_level = getattr(self, '_current_threat_level', 1)
                return get_fallback_message(threat_level)
            except Exception as fallback_error:
                print(f"Fallback import error: {fallback_error}")
                return "Attention! You are being monitored. Please identify yourself."

    def speak_local(self, text):
        """
        Queues text to be spoken locally (Sequential & Non-blocking).
        """
        if not hasattr(self, '_tts_queue'):
            import queue
            import threading
            self._tts_queue = queue.Queue()
            self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
            self._tts_thread.start()
            
        self._tts_queue.put(text)

    def _tts_worker(self):
        """
        Worker thread that processes the TTS queue sequentially.
        """
        import subprocess
        import sys
        while True:
            text = self._tts_queue.get()
            try:
                print(f"TTS Worker: {text[:50]}...")
                # Use call instead of Popen to wait for completion
                subprocess.run([sys.executable, "backend/scripts/speak.py", text], check=True)
            except Exception as e:
                print(f"TTS Worker Error: {e}")
            finally:
                self._tts_queue.task_done()

    def text_to_speech(self, text):
        """
        Converts text to speech audio via OpenAI API (returns bytes).
        """
        try:
            if self.provider == "openai":
                response = self.client.audio.speech.create(
                    model="tts-1",
                    voice="onyx",
                    input=text
                )
                return response.content
            else:
                return None
        except Exception as e:
            print(f"TTS Error: {e}")
            return None

ai_service = AIService()
