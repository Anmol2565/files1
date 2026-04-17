# Jan Setu API

FastAPI backend for the Jan Setu Grievance Portal.

## Setup

1. Create a `.env` file with required environment variables.
2. Install dependencies:
   ```bash
   # Option 1: Using requirements.txt
   pip install -r requirements.txt

   # Option 2: Using pyproject.toml (without installing project as package)
   pip install -e . --no-root
   ```
3. Run the app:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

## Deployment

- `Procfile` for Heroku / similar platforms
- `render.yaml` for Render deployments
- `wsgi.py` for PythonAnywhere

## Notes

- Uses SQLite locally (`jan_setu.db`)
- Supports Firebase Firestore if configured
- Integrates Twilio, OpenAI, and email notifications
