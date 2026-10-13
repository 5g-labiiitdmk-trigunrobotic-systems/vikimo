
import logging

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AssistantSession, Dealer
from core.assistant_service import run_assistant, sanitize_history
from core.views import error_response

logger = logging.getLogger(__name__)


class AssistantChatView(APIView):
    """POST /api/assistant/chat/"""

    def post(self, request):
        try:
            data = request.data
        except ParseError:
            return error_response(
                "VALIDATION_ERROR",
                "Request body must be valid JSON.",
                status.HTTP_400_BAD_REQUEST,
            )
        except UnsupportedMediaType:
            return error_response(
                "VALIDATION_ERROR",
                "Request content type must be JSON.",
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        if not isinstance(data, dict):
            return error_response(
                "VALIDATION_ERROR",
                "Request body must be a JSON object.",
                status.HTTP_400_BAD_REQUEST,
            )

        unexpected = set(data) - {"session_id", "dealer_id", "message"}
        if unexpected:
            return error_response(
                "VALIDATION_ERROR",
                "Request contains unsupported fields.",
                status.HTTP_400_BAD_REQUEST,
                sorted(unexpected),
            )

        session_id = data.get("session_id")
        dealer_id = data.get("dealer_id")
        message = data.get("message")

        if not isinstance(session_id, str) or not session_id.strip():
            return error_response(
                "VALIDATION_ERROR",
                "session_id is required.",
                400,
            )

        if len(session_id.strip()) > 100:
            return error_response(
                "VALIDATION_ERROR",
                "session_id must be at most 100 characters.",
                400,
            )

        if type(dealer_id) is not int or dealer_id <= 0:
            return error_response(
                "VALIDATION_ERROR",
                "A valid dealer_id is required.",
                400,
            )

        if not isinstance(message, str) or not message.strip():
            return error_response(
                "VALIDATION_ERROR",
                "message is required.",
                400,
            )

        dealer = Dealer.objects.filter(pk=dealer_id).first()

        if dealer is None:
            return error_response(
                "NOT_FOUND",
                "Dealer not found.",
                404,
            )

        session_id = session_id.strip()
        message = message.strip()

        try:
            with transaction.atomic():
                session, _ = AssistantSession.objects.get_or_create(
                    session_id=session_id,
                    defaults={
                        "dealer": dealer,
                        "history": [],
                    },
                )
                session = AssistantSession.objects.select_for_update().get(
                    pk=session.pk
                )

                # Prevent a session from being reused by another dealer.
                if session.dealer_id != dealer.id:
                    return error_response(
                        "VALIDATION_ERROR",
                        "This session belongs to another dealer.",
                        400,
                    )

                history = sanitize_history(session.history)

                result = run_assistant(
                    message=message,
                    dealer_id=dealer.id,
                    history=history,
                )

                reply = result["reply"]

                persisted_history = result.get("history")
                if not isinstance(persisted_history, list):
                    persisted_history = history + [
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": reply},
                    ]
                session.history = sanitize_history(persisted_history)

                session.save(update_fields=["history", "updated_at"])

        except Exception:
            logger.exception("Dealer assistant request failed.")
            return error_response(
                "INTERNAL_ERROR",
                "The assistant could not process your request.",
                500,
            )

        return Response(
            {
                "reply": result["reply"],
                "cited_skus": result["cited_skus"],
                "tool_calls": result["tool_calls"],
                "actions": result["actions"],
            },
            status=status.HTTP_200_OK,
        )
