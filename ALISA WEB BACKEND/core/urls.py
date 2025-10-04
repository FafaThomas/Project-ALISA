from django.urls import path
from .views import chat, history

urlpatterns = [
    path("chat/", chat, name="chat"),
    path("history/", history, name="history"),
]
