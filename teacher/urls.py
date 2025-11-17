from django.urls import path
from . import views

urlpatterns = [
    path('join-class/', views.join_class, name='join_class'),
    path('approve-class/', views.manage_enrollment_requests, name='approve_class'),
]
