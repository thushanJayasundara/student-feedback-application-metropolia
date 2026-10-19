# ocr

React + FastAPI app that OCRs an uploaded question/answer image and grades
it with a vision LLM. See [DOCUMENTATION.md](DOCUMENTATION.md) for full
architecture, data flow, and API details.

## Backend

On Windows, use the included Python 3.12 virtual environment. PaddlePaddle
does not currently publish a compatible wheel for Python 3.14.

```bash
cd backend
# Windows PowerShell
..\backend\venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

If the virtual environment has not been created yet, create it with a
supported Python 3.12 installation, then install the dependencies:

```powershell
py -3.12 -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Runs on http://localhost:8000

## Frontend

```bash
cd frontend
npm run dev
```

Runs on http://localhost:5173, proxies `/api` to the backend.
