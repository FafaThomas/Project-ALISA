from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import ChatLog
from .serializers import ChatLogSerializer
import requests

AI_CORE_URL = "http://127.0.0.1:5000/api/query"


@api_view(["POST"])
def chat(request):
    user_input = request.data.get("message", "")
    try:
        r = requests.post(AI_CORE_URL, json={"message": user_input}, timeout=120)
        data = r.json()

        # Save interaction
        ChatLog.objects.create(
            message=user_input,
            agent=data.get("agent", "Unknown"),
            reply=data.get("reply", "")
        )

        return Response(data)
    except Exception as e:
        return Response({"error": str(e)}, status=500)


@api_view(["GET"])
def history(request):
    logs = ChatLog.objects.order_by("-created_at")[:50]  # last 50 messages
    serializer = ChatLogSerializer(logs, many=True)
    return Response(serializer.data)
