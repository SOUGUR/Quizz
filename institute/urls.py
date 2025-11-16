from django.urls import path
from . import views

urlpatterns = [
    path('create-profile/', views.create_profile_view, name='create_profile'),
]
