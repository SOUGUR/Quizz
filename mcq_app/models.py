from django.db import models


class QuestionPaper(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=255)
    upload_file = models.FileField(upload_to="uploads/")
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # Short, human-readable progress line shown in the UI while processing.
    progress_message = models.CharField(max_length=255, blank=True)
    # User-facing failure reason (no stack traces / secrets; those go to logs).
    error_message = models.TextField(blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    # Raw OCR/text-layer output, kept so extraction can be debugged or re-run
    # through the LLM without paying for OCR again.
    ocr_text = models.TextField(blank=True)
    llm_provider = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_busy(self):
        return self.status == self.Status.PROCESSING


class Question(models.Model):
    question_paper = models.ForeignKey(
        QuestionPaper, related_name="questions", on_delete=models.CASCADE
    )
    question_number = models.CharField(max_length=50)
    question_text = models.TextField()
    # Position in the paper; keeps display order stable even when numbering
    # is missing or irregular.
    order = models.PositiveIntegerField(default=0)
    # Normalized hash of question + options, used to detect duplicates.
    fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["question_paper", "fingerprint"],
                condition=~models.Q(fingerprint=""),
                name="unique_question_per_paper",
            )
        ]

    def __str__(self):
        return f"{self.question_number}: {self.question_text[:50]}..."


class Option(models.Model):
    question = models.ForeignKey(Question, related_name="options", on_delete=models.CASCADE)
    option_label = models.CharField(max_length=1)  # A, B, C, D
    text = models.TextField()

    class Meta:
        ordering = ["option_label"]
        constraints = [
            models.UniqueConstraint(
                fields=["question", "option_label"], name="unique_option_label"
            )
        ]

    def __str__(self):
        return f"{self.option_label}: {self.text[:50]}..."
