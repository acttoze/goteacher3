"""config/urls.py 에서 path("s/<str:space_token>/", include("meals.urls")) 로 연결한다."""
from django.urls import path

from . import views

urlpatterns = [
    path("meals", views.index),
    path("meals/screens", views.screens),
    path("meals/tv/reissue", views.reissue_tv),
    path("meals/<str:ymd>/photo", views.photo_file),
    path("meals/<str:ymd>/upload", views.upload),
    path("meals/<str:ymd>/delete", views.delete),
    path("school/members/<str:key>/meal", views.set_writer),
]
