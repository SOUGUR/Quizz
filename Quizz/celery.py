"""Celery application (only used when TASK_BACKEND=celery).

Start a worker with:
    celery -A Quizz worker -l info            # Linux/macOS
    celery -A Quizz worker -l info --pool=solo # Windows
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Quizz.settings")

app = Celery("Quizz")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
