from django.urls import path

from . import views

urlpatterns = [
    path("today", views.index),
    path("today/parse", views.preview),
    path("today/events", views.add),
    path("today/e/<str:key>", views.edit),
    path("today/e/<str:key>/delete", views.delete),
]
