import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import UploadPaperForm
from .models import QuestionPaper
from .services.parser_service import ParserService
from .tasks import dispatch_processing

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=getattr(settings, "PROCESSING_STALE_MINUTES", 120))


def _recover_if_stale(paper):
    """A PROCESSING paper whose worker died (e.g. server restart) is reset."""
    if paper.is_busy and paper.started_at and timezone.now() - paper.started_at > STALE_AFTER:
        QuestionPaper.objects.filter(pk=paper.pk, status=QuestionPaper.Status.PROCESSING).update(
            status=QuestionPaper.Status.FAILED,
            error_message="Processing was interrupted (the server may have restarted). Please retry.",
            progress_message="",
        )
        paper.refresh_from_db()


def paper_list(request):
    papers = QuestionPaper.objects.annotate(question_count=Count("questions"))
    return render(request, "mcq_app/paper_list.html", {"papers": papers})


def upload_paper(request):
    if request.method == "POST":
        form = UploadPaperForm(request.POST, request.FILES)
        if form.is_valid():
            paper = form.save()
            logger.info("Uploaded paper %s (%s)", paper.pk, paper.upload_file.name)
            if request.POST.get("process_now"):
                return _start(request, paper, reuse_ocr=False)
            messages.success(request, f"Paper '{paper.title}' uploaded. You can now process it.")
            return redirect("paper_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = UploadPaperForm()
    return render(
        request,
        "mcq_app/upload_paper.html",
        {"form": form, "max_mb": settings.MAX_UPLOAD_SIZE_MB},
    )


def _start(request, paper, reuse_ocr):
    if not ParserService.claim(paper.pk):
        messages.info(request, "This paper is already being processed.")
        return redirect("paper_detail", paper_id=paper.pk)
    try:
        dispatch_processing(paper.pk, reuse_ocr=reuse_ocr)
    except Exception:
        logger.exception("Could not dispatch paper %s", paper.pk)
        QuestionPaper.objects.filter(pk=paper.pk).update(
            status=QuestionPaper.Status.FAILED,
            error_message="Could not start background processing. Is the task worker running?",
            progress_message="",
        )
        messages.error(request, "Could not start processing. Please try again.")
        return redirect("paper_detail", paper_id=paper.pk)
    messages.success(request, "Processing started. This page updates automatically.")
    return redirect("paper_detail", paper_id=paper.pk)


@require_POST
def process_paper(request, paper_id):
    """Start (or restart) extraction. POST-only: it changes state."""
    paper = get_object_or_404(QuestionPaper, id=paper_id)
    _recover_if_stale(paper)
    reuse_ocr = request.POST.get("reuse_ocr") == "1"
    return _start(request, paper, reuse_ocr=reuse_ocr)


def paper_detail(request, paper_id):
    paper = get_object_or_404(QuestionPaper, id=paper_id)
    _recover_if_stale(paper)
    questions = paper.questions.prefetch_related("options")
    return render(
        request,
        "mcq_app/paper_detail.html",
        {"paper": paper, "questions": questions, "question_count": questions.count()},
    )


@require_GET
def paper_status(request, paper_id):
    """Lightweight JSON endpoint polled by the detail page."""
    paper = get_object_or_404(QuestionPaper, id=paper_id)
    _recover_if_stale(paper)
    return JsonResponse(
        {
            "status": paper.status,
            "status_display": paper.get_status_display(),
            "progress": paper.progress_message,
            "error": paper.error_message,
            "question_count": paper.questions.count(),
        }
    )
