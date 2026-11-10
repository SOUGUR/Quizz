from django.urls import path

from . import views

urlpatterns = [
    path("", views.paper_list, name="paper_list"),
    path("upload/", views.upload_paper, name="upload_paper"),
    path("process/<int:paper_id>/", views.process_paper, name="process_paper"),
    path("paper/<int:paper_id>/", views.paper_detail, name="paper_detail"),
    path("paper/<int:paper_id>/status/", views.paper_status, name="paper_status"),
]
