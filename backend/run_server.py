import os
import sys
import uvicorn
from pathlib import Path

if __name__ == "__main__":
    # Ensure the 'backend' directory is in sys.path
    backend_dir = Path(__file__).parent.absolute()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    
    # Inject into environment for sub-processes
    os.environ["PYTHONPATH"] = str(backend_dir)
    
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
