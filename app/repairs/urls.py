"""config/urls.py 에서 path("s/<str:space_token>/", include("repairs.urls")) 로 연결한다."""
from django.urls import path

from . import views

urlpatterns = [
    path("repairs", views.index),
    path("repairs/new", views.new),
    path("repairs/<str:key>", views.detail),
    path("repairs/<str:key>/state", views.state),
    path("repairs/<str:key>/edit", views.edit),
    path("repairs/<str:key>/delete", views.delete),
    path("repairs/<str:key>/photo", views.photo_file),
    path("repairs/<str:key>/photo/delete", views.delete_photo),
    path("school/members/<str:key>/repair", views.set_handler),
]
