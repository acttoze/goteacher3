"""config/urls.py 에서 path("s/<str:space_token>/", include("notices.urls")) 로 연결한다."""
from django.urls import path

from . import views

urlpatterns = [
    path("board", views.index),
    path("board/new", views.new),
    path("board/<str:key>/", views.detail),
    path("board/<str:key>/seen", views.seen),
    path("board/<str:key>/ack", views.ack),
    path("board/<str:key>/v/<int:v>/erase", views.erase),
    path("board/<str:key>/edit", views.edit),
    path("board/<str:key>/delete", views.delete),
    path("board/<str:key>/f/<str:file_key>", views.download),
]
