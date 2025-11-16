from django.urls import path
from . import views

urlpatterns = [
    path('create-classroom/', views.create_classroom, name='create_classroom'),
]
