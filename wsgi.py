"""
WSGI entry point for PythonAnywhere deployment
"""
import sys
from pathlib import Path

# Add the project directory to the path
project_dir = str(Path(__file__).parent.absolute())
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Import the FastAPI app
from main import app

# WSGI application
application = app
