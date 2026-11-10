# Quizz – Nexus Platform

A Django project (`Quizz/`) for institutes, teachers and students:

| App | Purpose | URL |
| --- | --- | --- |
| `institute` | institutes and user profiles/roles | `/institute/` |
| `classroom` | classes and enrollments | `/classroom/` |
| `teacher` | join/approve class requests | `/teacher/` |
| `tests` | papers, questions, attempts, answers | – |
| `mcq_app` | **MCQ Extractor** (below) | `/mcq/` |

# MCQ Extractor

A Django app that extracts multiple-choice questions (MCQs) from exam papers
(PDFs or images). It reads the text (PDF text layer or OCR), asks an LLM to
structure it into questions with exactly four options (A–D), validates the
result, and stores it in a database.

## How it works

```
Upload ─▶ validate file ─▶ background job
                              │
     ┌────────────────────────┘
     ▼
 1. Text      PDF page has a text layer? use it (exact, instant)
              otherwise render page (PyMuPDF) ─▶ OCR (local PaddleOCR / NVIDIA API)
 2. Clean     drop running headers/footers, join "(A)\nvalue" lines, map
              math-italic letters (𝑡𝑒𝑎𝑚 -> team) while keeping x², √, − intact
 3. Chunk     split at question numbers (Q.1, 2., Question 3 ...); never cut
              inside a question. LLM_QUESTIONS_PER_CHUNK questions per call
              (1 for local models). Unnumbered text -> overlapping windows
 4. LLM       schema-constrained JSON (Ollama or Gemini), retries w/ backoff
 5. Validate  exactly 4 non-empty options, grounded in source text (anti-
              hallucination), duplicates merged, printed numbering restored
 6. Save      atomic replace of the paper's questions (re-processing never
              duplicates)
```

Status of each paper: **Pending → Processing → Completed / Failed**. The
detail page polls for progress, so large PDFs never hit a request timeout.

| Module | Responsibility |
| --- | --- |
| `mcq_app/forms.py` | upload validation (extension, size, magic bytes, page count, corruption) |
| `mcq_app/services/ocr_service.py` | PDF text layer, page rendering, OCR providers |
| `mcq_app/services/chunking.py` | question-aware, overlapping chunking |
| `mcq_app/services/llm_service.py` | LLM providers, prompt, JSON schema, retries |
| `mcq_app/services/validation.py` | normalization, 4-option check, grounding, de-duplication |
| `mcq_app/services/persistence.py` | atomic DB writes |
| `mcq_app/services/parser_service.py` | pipeline orchestration + status tracking |
| `mcq_app/tasks.py` | background execution (thread or Celery) |

## Choosing providers

**LLM (`LLM_PROVIDER`)**

| Option | When to use |
| --- | --- |
| `ollama` + `qwen2.5:3b` (default) | Free, private, offline. ~2 GB RAM, runs on CPU-only laptops (tested on an i5-1135G7 / 8 GB). Good at JSON; may miss questions in very noisy OCR. |
| `ollama` + `qwen2.5:7b` / `llama3.1:8b` | Better accuracy if you have ≥16 GB RAM or a GPU. Much slower on CPU. |
| `gemini` + `gemini-2.5-flash` | Best accuracy and speed; free tier available. Paper text is sent to Google. |

**OCR (`OCR_PROVIDER`)** — only used for pages without a text layer.

| Option | Notes |
| --- | --- |
| `paddle_local` (default) | PaddleOCR PP-OCRv5 mobile models on CPU. Models download once on first use. |
| `nvidia` | NVIDIA-hosted PaddleOCR. Needs `NVIDIA_OCR_API_KEY`; page images are auto-compressed to the API's size limit. |

## Setup

Requires Python 3.12+ and (for the default LLM) [Ollama](https://ollama.com).
No Poppler or Tesseract installation is needed.

```bash
# 1. virtual environment
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux/macOS

# 2. dependencies
pip install -r requirements.txt
# optional: pip install -r requirements-optional.txt   (Celery/Redis, Poppler renderer)

# 3. configuration
copy .env.example .env           # Windows   (cp on Linux/macOS)
#   then edit .env: set SECRET_KEY, choose providers, add API keys if using cloud ones

# 4. local LLM (if LLM_PROVIDER=ollama)
ollama pull qwen2.5:3b

# 5. database
python manage.py migrate
python manage.py createsuperuser   # optional, for /admin/

# 6. run
python manage.py runserver
```

Open http://127.0.0.1:8000/mcq/ (or "MCQ Extractor" in the top bar) → **Upload Paper** → the paper is processed in
the background; its page shows live progress and then the questions.

### Command line

```bash
python manage.py process_paper <id>              # synchronous run, prints result
python manage.py process_paper <id> --reuse-ocr  # re-run only the LLM step
python manage.py test mcq_app                    # run the test-suite
```

### Production / Celery

`TASK_BACKEND=thread` (default) runs jobs in a background thread of the web
process: no extra infrastructure, but a server restart interrupts running
jobs (they are marked *Failed – interrupted* after
`PROCESSING_STALE_MINUTES` and can be retried).

For production use Celery with Redis:

```bash
pip install -r requirements-optional.txt
# .env: TASK_BACKEND=celery, CELERY_BROKER_URL=redis://127.0.0.1:6379/0
celery -A Quizz worker -l info              # Linux
celery -A Quizz worker -l info --pool=solo  # Windows (not officially supported)
```

Also set `DEBUG=False`, a strong `SECRET_KEY`, `ALLOWED_HOSTS`, and serve
`media/` and static files from a real web server.

## Security notes

* All secrets come from `.env`, which is git-ignored. `.env.example` lists every variable.
* API keys are never logged; auth failures are reported without the key.
* Uploads are checked by extension, size (`MAX_UPLOAD_SIZE_MB`), real file
  signature (magic bytes), page count (`MAX_PDF_PAGES`) and are opened to
  reject corrupt/encrypted files.
* State-changing actions are POST + CSRF protected.
* With `DEBUG=False`: secure cookies, HSTS, SSL redirect, and a required `SECRET_KEY`.

## Measured results

GATE CS 2025 paper (64 pages, 65 questions: 43 with options A–D, 22
numerical-answer), Intel i5-1135G7, 8 GB RAM, no GPU, `qwen2.5:3b`:

| Setting | Recall (of 43) | Precision | Time |
| --- | --- | --- | --- |
| ~7 questions per call (3500 chars) | 56% | 100% | 9 min |
| 2 questions per call (sample of 16) | 88% | 100% | – |
| **1 question per call (default)** | **91% (39/43)** | **100%** | 10 min |

Every extracted question/option matched the PDF text exactly; no numerical
questions were wrongly turned into MCQs. Of the 4 misses, 2 (Q.9, Q.36)
have *picture-only* options and are correctly skipped (nothing to extract),
Q.47 (math-italic letters) is fixed by the math-letter normalization, and
Q.41 (dense radicals) is still missed by the 3B model - use Gemini or a
larger model for math-heavy papers.

A 3-page image-only (scanned) PDF via local PaddleOCR: 3/3 questions,
word-for-word, 3 minutes.

## Limitations

* The LLM does not answer questions; only question text and options are stored.
* Questions with diagrams keep only their text; questions whose options are
  pictures are skipped (no option text exists).
* Windows + paddlepaddle 3.3: the oneDNN backend crashes, so it is disabled
  by default (`PADDLE_ENABLE_MKLDNN=false`); OCR is ~10 s/page on CPU.
* Numerical-answer questions (no options) are intentionally skipped.
* A small local model on poor-quality scans can miss questions; switch to
  Gemini or a larger local model for best accuracy.
