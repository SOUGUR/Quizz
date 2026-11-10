import io

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

SAMPLE_TEXT = """GATE 2025 Computer Science
General Instructions: answer all questions.

Q.1 What is the time complexity of binary search on n elements?
(A) O(n)
(B) O(log n)
(C) O(n log n)
(D) O(1)

Q.2 Which data structure uses LIFO order?
(A) Queue
(B) Stack
(C) Heap
(D) Graph
"""

VALID_LLM_RESULT = [
    {
        "question_number": "Q.1",
        "question_text": "What is the time complexity of binary search on n elements?",
        "options": {"A": "O(n)", "B": "O(log n)", "C": "O(n log n)", "D": "O(1)"},
    },
    {
        "question_number": "Q.2",
        "question_text": "Which data structure uses LIFO order?",
        "options": {"A": "Queue", "B": "Stack", "C": "Heap", "D": "Graph"},
    },
]


def make_text_pdf(text=SAMPLE_TEXT, pages=1):
    """A digital PDF with a real text layer."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((50, 60), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def make_image(text="Q.1 Sample scanned question", fmt="PNG"):
    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((20, 80), text, fill="black", font=ImageFont.load_default())
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def make_scanned_pdf():
    """A PDF whose only content is an image (no text layer) - like a scan."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=make_image())
    data = doc.tobytes()
    doc.close()
    return data


class FakeOCRProvider:
    name = "fake"

    def __init__(self, text="Q.1 recognised text\n(A) a\n(B) b\n(C) c\n(D) d"):
        self.text = text
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return self.text


class FakeLLM:
    """Stands in for an LLM provider in pipeline tests."""

    label = "fake:llm"

    def __init__(self, results=None, error=None):
        self.results = results if results is not None else VALID_LLM_RESULT
        self.error = error
        self.calls = 0

    def check(self):
        pass

    def extract(self, chunk):
        self.calls += 1
        if self.error:
            raise self.error
        return self.results
