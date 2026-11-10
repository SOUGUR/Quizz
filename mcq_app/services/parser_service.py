"""
Pipeline orchestrator: OCR -> chunking -> LLM -> validation -> persistence.

Each stage lives in its own module; this file only wires them together and
manages the paper's status (Pending -> Processing -> Completed / Failed).
"""

import logging

from django.conf import settings
from django.utils import timezone

from ..models import QuestionPaper
from .chunking import chunk_text, normalize_layout, strip_repeated_lines
from .llm_service import LLMConfigError, LLMError, get_llm_provider
from .ocr_service import OCRError, OCRService
from .persistence import replace_questions
from .validation import validate_and_dedupe

logger = logging.getLogger(__name__)

PAGE_SEPARATOR = "\n\f\n"  # form feed: lets stored OCR text be split back into pages


class ParserService:
    @staticmethod
    def claim(paper_id):
        """
        Atomically move a paper to PROCESSING. Returns False if it is already
        being processed - a single UPDATE ... WHERE makes this race-free even
        if the button is double-clicked.
        """
        updated = (
            QuestionPaper.objects.filter(pk=paper_id)
            .exclude(status=QuestionPaper.Status.PROCESSING)
            .update(
                status=QuestionPaper.Status.PROCESSING,
                progress_message="Queued",
                error_message="",
                started_at=timezone.now(),
                finished_at=None,
            )
        )
        return updated == 1

    @staticmethod
    def _set(paper_id, **fields):
        QuestionPaper.objects.filter(pk=paper_id).update(**fields)

    @classmethod
    def process_paper(cls, paper_id, reuse_ocr=False):
        """Run the full pipeline for an already-claimed paper. Never raises."""
        paper = QuestionPaper.objects.get(pk=paper_id)
        progress = lambda msg: cls._set(paper_id, progress_message=msg[:255])  # noqa: E731
        logger.info("Processing paper %s (%s)", paper.pk, paper.title)

        try:
            llm = get_llm_provider()
            llm.check()  # fail before spending time on OCR
            cls._set(paper_id, llm_provider=llm.label)

            # 1. OCR / text layer
            if reuse_ocr and paper.ocr_text.strip():
                page_texts = paper.ocr_text.split(PAGE_SEPARATOR)
                progress("Re-using stored OCR text")
            else:
                pages = OCRService(progress=progress).extract_pages(paper.upload_file.path)
                page_texts = [p.text for p in pages]
                failed = [p.page_number for p in pages if p.source == "failed"]
                if failed:
                    logger.warning("Paper %s: OCR failed on pages %s", paper.pk, failed)
                cls._set(
                    paper_id,
                    ocr_text=PAGE_SEPARATOR.join(page_texts),
                    page_count=len(pages),
                )

            # 2. Chunk. Pages are joined so a question spanning a page break
            # stays in one block. Running headers/footers are removed first.
            page_texts = strip_repeated_lines(page_texts)
            full_text = normalize_layout("\n".join(t for t in page_texts if t.strip()))
            chunks = chunk_text(
                full_text,
                max_chars=settings.MCQ_LLM["CHUNK_MAX_CHARS"],
                overlap=settings.MCQ_LLM["CHUNK_OVERLAP_QUESTIONS"],
                max_blocks=settings.MCQ_LLM["QUESTIONS_PER_CHUNK"] or None,
            )
            logger.info("Paper %s: %s chars -> %s chunks", paper.pk, len(full_text), len(chunks))

            # 3. LLM extraction, chunk by chunk
            collected, chunk_errors = [], []
            for index, chunk in enumerate(chunks, start=1):
                progress(f"Extracting MCQs with {llm.label}: chunk {index}/{len(chunks)}")
                try:
                    items = llm.extract(chunk)
                except LLMConfigError:
                    raise
                except LLMError as exc:
                    logger.warning("Paper %s chunk %s failed: %s", paper.pk, index, exc)
                    chunk_errors.append(index)
                    continue
                logger.info("Paper %s chunk %s: %s raw items", paper.pk, index, len(items))
                collected.extend((item, chunk) for item in items)

            if chunks and len(chunk_errors) == len(chunks):
                raise LLMError("The language model failed on every part of the paper.")

            # 4. Validate + de-duplicate
            report = validate_and_dedupe(collected)
            for reason, raw in report.rejected:
                logger.info("Rejected item (%s): %s", reason, str(raw)[:200])

            if not report.accepted:
                raise LLMError(
                    "No complete 4-option MCQs were found. The scan may be unclear, or the "
                    "paper may not contain multiple-choice questions."
                )

            # 5. Persist
            saved = replace_questions(paper, report.accepted)
            summary = f"Extracted {saved} MCQs"
            details = []
            if report.duplicates:
                details.append(f"{report.duplicates} duplicates merged")
            if report.rejected:
                details.append(f"{len(report.rejected)} incomplete items skipped")
            if chunk_errors:
                details.append(f"{len(chunk_errors)} text chunks failed")
            if details:
                summary += " (" + ", ".join(details) + ")"

            cls._set(
                paper_id,
                status=QuestionPaper.Status.COMPLETED,
                progress_message=summary[:255],
                finished_at=timezone.now(),
            )
            logger.info("Paper %s completed: %s", paper.pk, summary)
            return True

        except (OCRError, LLMError) as exc:
            message = str(exc)
            logger.error("Paper %s failed: %s", paper.pk, message)
        except Exception:
            logger.exception("Paper %s failed with an unexpected error", paper.pk)
            message = "An unexpected error occurred. See logs/mcq_app.log for details."

        cls._set(
            paper_id,
            status=QuestionPaper.Status.FAILED,
            error_message=message,
            progress_message="",
            finished_at=timezone.now(),
        )
        return False
