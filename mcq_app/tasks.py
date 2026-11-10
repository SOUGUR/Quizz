"""
Background execution of the extraction pipeline.

Processing a large PDF takes minutes (OCR + LLM per chunk), far longer than
an HTTP request should block. The view therefore only *claims* the paper and
hands the work off here; the page then polls the paper's status.

TASK_BACKEND=thread (default)
    A daemon thread inside the Django process. Zero infrastructure and works
    on Windows. Downside: work is lost if the server restarts (the paper is
    then detected as "stale" and can be re-run).
TASK_BACKEND=celery
    Celery worker + Redis broker. Survives restarts, scales to many workers.
    Recommended on Linux/production. (Celery no longer officially supports
    Windows; use `--pool=solo` there if you must.)
"""

import logging
import threading

from django.conf import settings
from django.db import close_old_connections

from .services.parser_service import ParserService

logger = logging.getLogger(__name__)


def _run(paper_id, reuse_ocr):
    try:
        ParserService.process_paper(paper_id, reuse_ocr=reuse_ocr)
    finally:
        # Each thread gets its own DB connection; close it when done.
        close_old_connections()


try:
    from celery import shared_task

    @shared_task(name="mcq_app.process_paper")
    def process_paper_task(paper_id, reuse_ocr=False):
        _run(paper_id, reuse_ocr)

except ImportError:  # celery is optional
    process_paper_task = None


def dispatch_processing(paper_id, reuse_ocr=False):
    """Queue processing of an already-claimed paper."""
    backend = settings.TASK_BACKEND
    if backend == "celery":
        if process_paper_task is None:
            raise RuntimeError("TASK_BACKEND=celery but celery is not installed")
        process_paper_task.delay(paper_id, reuse_ocr)
    elif backend == "sync":  # used by tests and the management command
        _run(paper_id, reuse_ocr)
    else:
        threading.Thread(
            target=_run, args=(paper_id, reuse_ocr), name=f"mcq-paper-{paper_id}", daemon=True
        ).start()
    logger.info("Dispatched paper %s via %s backend", paper_id, backend)
