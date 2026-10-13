
from django.db import IntegrityError, transaction

from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Dealer
from core.serializers import DealerSerializer
from core.views import error_response


class DealerPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class DealerListCreateView(APIView):
    """GET /api/dealers/ and POST /api/dealers/"""

    def get(self, request):
        dealers = Dealer.objects.all()

        paginator = DealerPagination()
        page = paginator.paginate_queryset(dealers, request)

        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = DealerSerializer(data=request.data)

        if not serializer.is_valid():
            errors = serializer.errors
            duplicate_code = (
                "dealer_code" in errors
                and Dealer.objects.filter(
                    dealer_code=str(
                        request.data.get("dealer_code", "")
                    ).strip()
                ).exists()
            )

            if duplicate_code:
                return error_response(
                    "DUPLICATE",
                    "A dealer with this code already exists.",
                    status.HTTP_409_CONFLICT,
                )

            return error_response(
                "VALIDATION_ERROR",
                "Invalid dealer data.",
                status.HTTP_400_BAD_REQUEST,
                errors,
            )

        try:
            with transaction.atomic():
                dealer = serializer.save()
        except IntegrityError:
            return error_response(
                "DUPLICATE",
                "A dealer with this code already exists.",
                status.HTTP_409_CONFLICT,
            )

        return Response(
            DealerSerializer(dealer).data,
            status=status.HTTP_201_CREATED,
        )


class DealerDetailView(APIView):
    """GET /api/dealers/{id}/"""

    def get(self, request, pk):
        dealer = Dealer.objects.filter(pk=pk).first()

        if dealer is None:
            return error_response(
                "NOT_FOUND",
                "Dealer not found.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response(DealerSerializer(dealer).data)
