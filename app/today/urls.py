from django.urls import path

from . import more, print_view, schedule, views, week_paste

urlpatterns = [
    path("today", views.index),
    path("schedule", schedule.index),  # R1 일정(주간)
    path("schedule/print", print_view.sheet),  # H6 인쇄(학교 공간)
    path("schedule/paste", week_paste.page),  # H6 주간계획 표 붙여넣기(학교 공간)
    path("schedule/paste/preview", week_paste.preview),
    path("today/parse", views.preview),
    path("today/events", views.add),
    path("today/e/<str:key>", views.edit),
    path("today/e/<str:key>/delete", views.delete),
    path("today/e/<str:key>/more", more.more),  # P2 본문·첨부·댓글
    path("today/e/<str:key>/body", more.body),
    path("today/e/<str:key>/files", more.files),  # 업로드: 앞단 큰 본문 라우터(config.bodylimit·ops/compose.yml)
    path("today/e/<str:key>/f/<str:file_key>", more.download),
    path("today/e/<str:key>/f/<str:file_key>/delete", more.file_delete),
    path("today/e/<str:key>/comments", more.comment),
    path("today/e/<str:key>/c/<str:comment_key>/delete", more.comment_delete),
    path("today/duty", schedule.duty_add),  # 일정 학교 탭 복무 칸(QC 2026-11-06)
    path("today/duty/<str:key>/delete", schedule.duty_delete),
]
