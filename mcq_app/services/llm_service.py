"""
LLM-based MCQ extraction with pluggable providers (LLM_PROVIDER in .env).

* ollama  - local inference (private, free, offline). Default model
            qwen2.5:3b: fits in ~2 GB RAM on a CPU-only laptop and follows
            JSON schemas well.
* gemini  - Google Gemini (cloud). Best accuracy on messy OCR text; needs a
            GEMINI_API_KEY and sends the paper text to Google.

Both providers use *schema-constrained* JSON output, so the model is forced
to emit the structure we expect instead of free text we must scrape.
"""

import json
import logging
import re
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised with a user-safe message."""


class LLMConfigError(LLMError):
    """Configuration/auth problems - retrying will not help."""


MCQ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_number": {"type": "string"},
                    "question_text": {"type": "string"},
                    "options": {
                        "type": "object",
                        "properties": {
                            "A": {"type": "string"},
                            "B": {"type": "string"},
                            "C": {"type": "string"},
                            "D": {"type": "string"},
                        },
                        "required": ["A", "B", "C", "D"],
                    },
                },
                "required": ["question_number", "question_text", "options"],
            },
        }
    },
    "required": ["questions"],
}

SYSTEM_PROMPT = """You extract multiple-choice questions (MCQs) from OCR text of exam papers.

Rules:
1. Extract ONLY questions that have exactly four options labelled A, B, C, D
   (also written (a)-(d), a)-d), or 1-4 in place of A-D).
2. Copy text faithfully. Do NOT invent, complete, guess, or answer anything.
   If a question or any of its four options is missing or cut off, SKIP it.
3. Keep the question number exactly as printed (e.g. "Q.12", "12").
4. Preserve mathematical expressions, symbols, numbers, units and code
   exactly. Fix only obvious OCR line breaks inside a sentence.
5. Ignore instructions, headers, footers, page numbers, section titles,
   answer keys, and numerical-answer questions without options.
6. Put each option's text without its label (write "42", not "(A) 42").

Return JSON: {"questions": [{"question_number": "...", "question_text": "...",
"options": {"A": "...", "B": "...", "C": "...", "D": "..."}}]}.
Return {"questions": []} if there are no complete MCQs."""


def build_user_prompt(chunk):
    return f"OCR TEXT:\n<<<\n{chunk}\n>>>\n\nExtract the MCQs as JSON."


def parse_json_response(text):
    """Parse model output into a dict, tolerating code fences / chatter."""
    if not text or not text.strip():
        raise ValueError("empty response")
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])

    if isinstance(data, list):
        data = {"questions": data}
    if not isinstance(data, dict) or not isinstance(data.get("questions", []), list):
        raise ValueError("response does not contain a 'questions' list")
    data.setdefault("questions", [])
    return data


class BaseLLMProvider:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    @property
    def label(self):
        return self.name

    def check(self):
        """Fail fast (before OCR) when the provider is not usable."""

    def generate(self, chunk):
        raise NotImplementedError

    def extract(self, chunk):
        retries = self.cfg["RETRIES"]
        last_error = None
        for attempt in range(1, retries + 2):
            try:
                return parse_json_response(self.generate(chunk))["questions"]
            except LLMConfigError:
                raise
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = f"invalid JSON ({exc})"
            except requests.Timeout:
                last_error = "request timed out"
            except requests.RequestException as exc:
                last_error = f"network error ({exc.__class__.__name__})"
            except LLMError as exc:
                last_error = str(exc)
            logger.warning("%s attempt %s/%s failed: %s", self.label, attempt, retries + 1, last_error)
            if attempt <= retries:
                time.sleep(min(2 ** attempt, 20))
        raise LLMError(f"{self.label} failed after {retries + 1} attempts: {last_error}")


class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    @property
    def label(self):
        return f"ollama:{self.cfg['OLLAMA_MODEL']}"

    def check(self):
        base, model = self.cfg["OLLAMA_BASE_URL"].rstrip("/"), self.cfg["OLLAMA_MODEL"]
        try:
            resp = requests.get(f"{base}/api/tags", timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            raise LLMConfigError(
                f"Cannot reach Ollama at {base}. Start it with `ollama serve` "
                "or set LLM_PROVIDER=gemini."
            )
        names = {m.get("name") for m in resp.json().get("models", [])}
        if model not in names and f"{model}:latest" not in names:
            raise LLMConfigError(f"Ollama model '{model}' is not installed. Run `ollama pull {model}`.")

    def generate(self, chunk):
        base = self.cfg["OLLAMA_BASE_URL"].rstrip("/")
        resp = requests.post(
            f"{base}/api/chat",
            json={
                "model": self.cfg["OLLAMA_MODEL"],
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(chunk)},
                ],
                "format": MCQ_SCHEMA,
                "stream": False,
                "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": self.cfg["OLLAMA_NUM_CTX"]},
            },
            timeout=self.cfg["TIMEOUT"],
        )
        if resp.status_code == 404:
            raise LLMConfigError(f"Ollama model '{self.cfg['OLLAMA_MODEL']}' not found.")
        if resp.status_code != 200:
            raise LLMError(f"Ollama returned HTTP {resp.status_code}")
        return resp.json().get("message", {}).get("content", "")


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client = None

    @property
    def label(self):
        return f"gemini:{self.cfg['GEMINI_MODEL']}"

    def check(self):
        if not self.cfg["GEMINI_API_KEY"]:
            raise LLMConfigError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.")

    @property
    def client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.cfg["GEMINI_API_KEY"])
        return self._client

    def generate(self, chunk):
        from google.genai import errors, types

        try:
            response = self.client.models.generate_content(
                model=self.cfg["GEMINI_MODEL"],
                contents=build_user_prompt(chunk),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    response_mime_type="application/json",
                    response_json_schema=MCQ_SCHEMA,
                ),
            )
        except errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code in (400, 401, 403) and "API key" in str(exc):
                raise LLMConfigError("Gemini rejected the API key. Check GEMINI_API_KEY.")
            if code == 429:
                raise LLMError("Gemini rate limit reached")
            raise LLMError(f"Gemini client error ({code})")
        except errors.ServerError as exc:
            raise LLMError(f"Gemini server error ({getattr(exc, 'code', '?')})")
        return response.text or ""


PROVIDERS = {"ollama": OllamaProvider, "gemini": GeminiProvider}


def get_llm_provider():
    cfg = settings.MCQ_LLM
    cls = PROVIDERS.get(cfg["PROVIDER"])
    if cls is None:
        raise LLMConfigError(
            f"Unknown LLM_PROVIDER '{cfg['PROVIDER']}'. Use one of: {', '.join(PROVIDERS)}."
        )
    return cls(cfg)
