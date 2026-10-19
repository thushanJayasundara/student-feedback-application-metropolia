# OCR + Answer Grading — Implementation Documentation

## Overview

A minimal full-stack app: the user uploads a photo containing a question and
its handwritten/printed answer. The backend extracts the text with PaddleOCR,
sends the image to a vision-capable LLM (via OpenRouter) to identify the
question/answer and judge correctness, and persists everything to MongoDB.
The frontend is a single-page React app for uploading and viewing results.

```
┌────────────┐   POST /api/upload   ┌──────────────────────────────────┐
│  React UI  │ ───────────────────▶ │            FastAPI               │
│ (Vite dev  │                      │  1. PaddleOCR  → extracted text  │
│  server,   │                      │  2. OpenRouter → question/answer │
│  :5173)    │ ◀─────────────────── │     grading (vision LLM)         │
└────────────┘   {text, grading}    │  3. MongoDB    → persist result  │
                                     └──────────────────────────────────┘
```

## Repository layout

```
ocr/
├── backend/
│   ├── main.py           FastAPI app, routes, orchestration
│   ├── db.py              MongoDB (Motor) connection
│   ├── feedback.py        OpenRouter vision-LLM grading
│   ├── requirements.txt
│   ├── .env               MONGODB_URI, OPENROUTER_API_KEY (gitignored)
│   └── venv/               Python virtualenv (gitignored)
├── frontend/
│   ├── src/
│   │   ├── App.jsx        Upload UI + result display
│   │   └── main.jsx        React entrypoint
│   └── vite.config.js      Dev server, proxies /api → :8000
├── .gitignore
└── README.md               Quick-start run commands
```

## Backend

### Stack

- **FastAPI** — HTTP API, CORS restricted to `http://localhost:5173`.
- **PaddleOCR** (`paddleocr`, `paddlepaddle`) — local, offline text extraction.
- **Motor** — async MongoDB driver.
- **httpx** — async HTTP client used to call OpenRouter.
- **python-dotenv** — loads `backend/.env` into the process environment.

### Environment variables (`backend/.env`)

| Variable             | Purpose                                                        |
|-----------------------|------------------------------------------------------------------|
| `MONGODB_URI`         | `mongodb://localhost:27017/new` — the database name is `new`.  |
| `OPENROUTER_API_KEY`  | Auth for the OpenRouter chat completions API.                  |

### `db.py` — MongoDB connection

```python
client = AsyncIOMotorClient(MONGODB_URI)
db = client.get_default_database()      # resolves to "new" from the URI path
ocr_results = db["ocr_results"]         # single collection for this feature
```

Each document stored:

```json
{
  "_id": ObjectId,
  "filename": "photo.png",
  "text": "<raw OCR text, newline-joined>",
  "grading": {
    "question": "What is 2 + 2?",
    "answer": "4",
    "is_correct": true,
    "feedback": "Correct answer."
  },
  "created_at": "<UTC datetime>"
}
```

### `feedback.py` — vision LLM grading

Calls OpenRouter's chat completions endpoint with the uploaded image
(base64 data URL) and a system prompt instructing the model to:

1. Read the image and identify the question and the answer written.
2. Judge whether the answer is correct.
3. Respond with **only** a JSON object: `{question, answer, is_correct, feedback}`.
4. If it can't find a question/answer, return `null`s, `is_correct: false`,
   and explain what's missing in `feedback`.

Model used: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`.

> **Why this model:** the original ask was Qwen2.5-VL, but OpenRouter has no
> free tier for it (paid, ~$0.25/M input tokens — trivial cost, but requires
> credits). Free vision-capable alternatives were tried; Google's Gemma
> free-tier models were intermittently rate-limited upstream during testing.
> This Nvidia Nemotron free model handled image input reliably and returns
> `content` (the answer) separately from `reasoning` (its chain-of-thought),
> which parses cleanly. Swap `MODEL` in `feedback.py` if you want to switch
> providers later (e.g. once you add OpenRouter credits for Qwen2.5-VL).

The model's raw response is stripped of any ```` ```json ```` fences and
`json.loads`'d. If parsing fails, a fallback dict is returned with
`is_correct: false` and the raw text in `feedback` so the request doesn't
hard-fail.

### `main.py` — API routes

#### `GET /api/health`

Returns `{"status": "ok"}`. Used by the frontend on load to show backend
connectivity.

#### `POST /api/upload`

Accepts a `multipart/form-data` file upload (field name `file`).

Flow:
1. Read the uploaded bytes.
2. Write them to a temp file (suffix matched to the original filename) so
   PaddleOCR can read from a path.
3. Run `ocr_engine.predict(tmp.name)` → collect `rec_texts` from each
   result's `res.json["res"]["rec_texts"]`, join with newlines.
4. Call `grade_image(contents, content_type)` — sends the *original* image
   bytes (not OCR text) to the vision LLM, so grading isn't limited by OCR
   accuracy.
5. Insert `{filename, text, grading, created_at}` into `ocr_results`.
6. Return `{id, filename, text, grading}`.

The PaddleOCR engine (`ocr_engine`) is instantiated once at module import
time — model weights are cached locally under `~/.paddlex/official_models/`
after the first run, but that first run downloads them, so the very first
request after a fresh clone will be slow.

## Frontend

### Stack

- **React + Vite**, minimal template (logos/boilerplate content stripped).
- No routing, no state management library — a single `App.jsx` component.

### `vite.config.js`

Proxies any `/api/*` request from the dev server (`:5173`) to
`http://localhost:8000`, so the frontend can call relative paths like
`fetch('/api/upload')` without hardcoding a backend origin or hitting CORS
in dev.

### `App.jsx` behavior

- On mount: `GET /api/health` → shows "Backend status: ok/unreachable".
- File input + Upload button: button disabled until a file is selected.
- On upload: `POST /api/upload` with `FormData`, shows `uploading...`, then
  either an error state or the result.
- Result rendering: shows the resolved `Correct`/`Incorrect` heading, the
  detected question, the detected answer, and the model's feedback text.

## Running locally

**Backend** (from `backend/`):
```bash
source venv/bin/activate
uvicorn main:app --reload
```
Runs on `http://localhost:8000`. Requires MongoDB reachable at the URI in
`.env` (verified locally at `localhost:27017`).

**Frontend** (from `frontend/`):
```bash
npm run dev
```
Runs on `http://localhost:5173` (or next free port if occupied).

## Known limitations / things to revisit

- **No auth** — the API and DB have no access control; fine for local dev,
  not for any shared deployment.
- **Single collection, no indexes** — `ocr_results` has no index on
  `created_at` or `filename`; fine at current scale.
- **Free-tier LLM reliability** — the OpenRouter free model pool can be
  rate-limited upstream (observed with Gemma during development). No retry
  logic is implemented; a failed grading call currently surfaces as a 500
  from `/api/upload` (via `response.raise_for_status()` in `feedback.py`).
- **PaddleOCR text vs. LLM grading are independent** — OCR output is stored
  for reference/search, but grading is done directly from the image, so the
  two can disagree if OCR misreads something the LLM reads correctly (or
  vice versa).
- **No file size/type validation** on `/api/upload` — any file is accepted
  and passed to both PaddleOCR and the vision model as-is.
