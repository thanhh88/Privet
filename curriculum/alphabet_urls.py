from django.urls import path
from . import alphabet_views

app_name = "alphabet"

urlpatterns = [
    path("", alphabet_views.overview, name="overview"),
    path("table/", alphabet_views.table, name="table"),
    path("lessons/<slug:lesson_id>/", alphabet_views.lesson, name="lesson"),
    path("letters/<slug:letter_id>/", alphabet_views.letter, name="letter"),
]
