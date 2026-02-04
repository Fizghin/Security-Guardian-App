import os
import sys
from pathlib import Path

def validate_env():
    print("--- [MASTER DEBUGGER] Environment Validation ---")
    
    env_path = Path(".env")
    if not env_path.exists():
        print("WARNING: .env file not found!")
        print("Please copy .env.template to .env and configure your keys.")
        return False
        
    # Required keys depend on provider
    provider = "openai"
    with open(env_path) as f:
        content = f.read()
        if "AI_PROVIDER=ollama" in content:
            provider = "ollama"

    required_keys = []
    if provider == "openai":
        required_keys = ["OPENAI_API_KEY"]
    
    missing = []
    for key in required_keys:
        if key not in content:
            missing.append(key)
            
    if missing:
        print(f"CRITICAL ERROR: Missing required environment variables: {', '.join(missing)}")
        return False
        
    print("SUCCESS: Environment validated (Local).")
    return True

if __name__ == "__main__":
    if not validate_env():
        sys.exit(1)
    sys.exit(0)
