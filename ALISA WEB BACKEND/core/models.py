from django.db import models

# Create your models here.
class ChatLog(models.Model):
    user = models.CharField(max_length=255, default="Thomas")
    agent = models.CharField(max_length=255)
    message = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
