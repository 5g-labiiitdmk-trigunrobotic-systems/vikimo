from django.db.models import Prefetch
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core import order_services
from core.models import Order, OrderItem
from core.order_serializers import (
    OrderCreateSerializer,
    OrderResponseSerializer,
    OrderUpdateSerializer,
)
from core.order_services import (  # noqa: F401  (re-exported for callers)
    TAX_RATE,
    ServiceError,
    generate_order_number,
    money,
)
from core.views import error_response


class OrderPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def order_queryset():
    return Order.objects.select_related("dealer").prefetch_related(
        Prefetch(
            "items",
            queryset=OrderItem.objects.select_related("product").order_by(
                "id"
            ),
        )
    )


def service_error_response(exc):
    """Contract error response; details are kept as structured data."""
    return Response(
        {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details or [],
            }
        },
        status=exc.http_status,
    )


def load_order(order_number):
    """Fetch one order with its items, re-pricing a DRAFT if needed."""
    order = order_queryset().filter(order_number=order_number).first()
    if order is not None and order_services.refresh_draft_prices(order):
        order = order_queryset().get(pk=order.pk)
    return order


def body_or_error(request):
    if not isinstance(request.data, dict):
        return None, error_response(
            "VALIDATION_ERROR",
            "Request body must be a JSON object.",
            status.HTTP_400_BAD_REQUEST,
        )
    return request.data, None


class OrderListCreateView(APIView):
    """GET /api/orders/ and POST /api/orders/"""

    def get(self, request):
        orders = order_queryset().order_by("-created_at", "-id")

        dealer_id = request.query_params.get("dealer_id")
        order_status = request.query_params.get("status")

        if dealer_id:
            if not dealer_id.isdigit() or int(dealer_id) > 2**63 - 1:
                return error_response(
                    "VALIDATION_ERROR",
                    "dealer_id must be an integer.",
                    status.HTTP_400_BAD_REQUEST,
                )
            orders = orders.filter(dealer_id=int(dealer_id))

        if order_status:
            orders = orders.filter(status__iexact=order_status)

        paginator = OrderPagination()
        page = paginator.paginate_queryset(orders, request)

        # DRAFT orders show current catalogue prices.
        if any(order_services.refresh_draft_prices(o) for o in page):
            by_id = {o.pk: o for o in order_queryset().filter(
                pk__in=[o.pk for o in page])}
            page = [by_id[o.pk] for o in page]

        serializer = OrderResponseSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        body, error = body_or_error(request)
        if error:
            return error

        serializer = OrderCreateSerializer(data=body)
        if not serializer.is_valid():
            return error_response(
                "VALIDATION_ERROR",
                "Invalid order data.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )

        key = request.headers.get("Idempotency-Key")
        if key is not None:
            key = key.strip()
            if not key or len(key) > 255:
                return error_response(
                    "VALIDATION_ERROR",
                    "Idempotency-Key must be 1 to 255 characters.",
                    status.HTTP_400_BAD_REQUEST,
                )

        validated = serializer.validated_data
        try:
            order, created = order_services.create_order(
                validated["dealer"],
                validated["items"],
                idempotency_key=key,
                body=body,
            )
        except ServiceError as exc:
            return service_error_response(exc)

        return Response(
            OrderResponseSerializer(load_order(order.order_number)).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class OrderDetailView(APIView):
    """GET and PUT /api/orders/{order_number}/"""

    def get(self, request, order_number):
        order = load_order(order_number)
        if order is None:
            return service_error_response(
                ServiceError("NOT_FOUND", "Order not found.", 404)
            )
        return Response(OrderResponseSerializer(order).data)

    def put(self, request, order_number):
        body, error = body_or_error(request)
        if error:
            return error

        existing = Order.objects.filter(order_number=order_number).first()
        if existing is None:
            return service_error_response(
                ServiceError("NOT_FOUND", "Order not found.", 404)
            )
        if existing.status != Order.Status.DRAFT:
            return service_error_response(ServiceError(
                "ORDER_LOCKED",
                f"Only DRAFT orders can be edited; this order is "
                f"{existing.status}.",
                409,
            ))

        serializer = OrderUpdateSerializer(data=body)
        if not serializer.is_valid():
            return error_response(
                "VALIDATION_ERROR",
                "Invalid order data.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )

        try:
            order_services.replace_items(
                order_number, serializer.validated_data["items"]
            )
        except ServiceError as exc:
            return service_error_response(exc)

        return Response(OrderResponseSerializer(load_order(order_number)).data)


class OrderActionView(APIView):
    """POST /api/orders/{order_number}/{confirm|dispatch|deliver|cancel}/"""

    action = None

    def post(self, request, order_number):
        try:
            getattr(order_services, self.action)(order_number)
        except ServiceError as exc:
            return service_error_response(exc)
        return Response(OrderResponseSerializer(load_order(order_number)).data)


class OrderConfirmView(OrderActionView):
    action = "confirm"


class OrderDispatchView(OrderActionView):
    action = "dispatch"


class OrderDeliverView(OrderActionView):
    action = "deliver"


class OrderCancelView(OrderActionView):
    action = "cancel"
