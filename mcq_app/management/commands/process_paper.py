"""Process a paper synchronously from the command line (handy for large PDFs and debugging).

    python manage.py process_paper 3
    python manage.py process_paper 3 --reuse-ocr
"""

from django.core.management.base import BaseCommand, CommandError

from mcq_app.models import QuestionPaper
from mcq_app.services.parser_service import ParserService


class Command(BaseCommand):
    help = "Run OCR + LLM extraction for a paper synchronously."

    def add_arguments(self, parser):
        parser.add_argument("paper_id", type=int)
        parser.add_argument("--reuse-ocr", action="store_true", help="Skip OCR, reuse stored text")
        parser.add_argument("--force", action="store_true", help="Run even if marked Processing")

    def handle(self, paper_id, reuse_ocr, force, **options):
        if not QuestionPaper.objects.filter(pk=paper_id).exists():
            raise CommandError(f"Paper {paper_id} does not exist")
        if force:
            QuestionPaper.objects.filter(pk=paper_id).update(status=QuestionPaper.Status.PENDING)
        if not ParserService.claim(paper_id):
            raise CommandError("Paper is already processing (use --force to override)")
        ok = ParserService.process_paper(paper_id, reuse_ocr=reuse_ocr)
        paper = QuestionPaper.objects.get(pk=paper_id)
        if ok:
            self.stdout.write(self.style.SUCCESS(paper.progress_message))
        else:
            raise CommandError(paper.error_message)
