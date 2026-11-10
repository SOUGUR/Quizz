try:  # Celery is optional; only needed when TASK_BACKEND=celery
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except ImportError:
    pass
