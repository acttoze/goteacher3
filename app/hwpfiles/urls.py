"""config/urls.py 에서 path("s/<str:space_token>/", include("hwpfiles.urls")) 로 연결한다.
space_token은 SpaceMiddleware가 빼서 request.space 로 바꾼다(회원만, 다른 공간 404)."""
from django.urls import path

from . import views

urlpatterns = [
    path("hwp/upload", views.upload),
    path("hwp/<str:key>/status", views.status),
    path("hwp/<str:key>/", views.preview),
    path("hwp/<str:key>/hwpx", views.hwpx),
    path("hwp/<str:key>/pick", views.pick),
    path("c/<str:key>/hwp", views.result),  # F1 한글 파일로 받기
    path("hwp/out/<str:key>/<str:which>", views.result_file),
]
