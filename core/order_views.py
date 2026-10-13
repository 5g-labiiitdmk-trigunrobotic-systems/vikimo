
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from django.db.models import Prefetch

from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Order, OrderDayCounter, OrderItem, Product
from core.order_serializers import (
    OrderCreateSerializer,
    OrderResponseSerializer,
)
from core.views import error_response


TAX_RATE = Decimal("0.18")


def money(value):
    return value.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def generate_order_number():
    """Return the next ORD-YYYYMMDD-NNNN number for today (Asia/Kolkata).

    Must run inside transaction.atomic(): the counter row is locked until the
    caller commits, so concurrent creators get different, gap-free numbers.
    """
    today = timezone.localdate()
    OrderDayCounter.objects.get_or_create(date=today)
    counter = OrderDayCounter.objects.select_for_update().get(date=today)
    counter.last_number += 1
    counter.save(update_fields=["last_number"])
    return f"ORD-{today:%Y%m%d}-{counter.last_number:04d}"


class OrderPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class OrderListCreateView(APIView):
    """GET /api/orders/ and POST /api/orders/"""

    def get(self, request):
        orders = Order.objects.select_related("dealer").prefetch_related(
            Prefetch(
                "items",
                queryset=OrderItem.objects.select_related("product"),
            )
        )

        dealer_id = request.query_params.get("dealer_id")
        order_status = request.query_params.get("status")

        if dealer_id:
            if not dealer_id.isdigit():
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

        serializer = OrderResponseSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)

        if not serializer.is_valid():
            return error_response(
                "VALIDATION_ERROR",
                "Invalid order data.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )

        validated = serializer.validated_data
        dealer = validated["dealer"]
        requested_items = validated["items"]

        try:
            with transaction.atomic():
                subtotal = Decimal("0.00")
                tax_total = Decimal("0.00")
                order_lines = []

                # Read current product prices from the database.
                for item in requested_items:
                    product = Product.objects.get(sku=item["sku"])
                    quantity = item["quantity"]
                    unit_price = product.price

                    line_subtotal = money(unit_price * quantity)
                    line_tax = money(line_subtotal * TAX_RATE)
                    line_total = line_subtotal + line_tax

                    subtotal += line_subtotal
                    tax_total += line_tax

                    order_lines.append(
                        {
                            "product": product,
                            "quantity": quantity,
                            "unit_price": unit_price,
                            "line_subtotal": line_subtotal,
                            "line_tax": line_tax,
                            "line_total": line_total,
                        }
                    )

                order = Order.objects.create(
                    order_number=generate_order_number(),
                    dealer=dealer,
                    status=Order.Status.DRAFT,
                    subtotal=money(subtotal),
                    tax_total=money(tax_total),
                    grand_total=money(subtotal + tax_total),
                )

                OrderItem.objects.bulk_create(
                    [
                        OrderItem(order=order, **line)
                        for line in order_lines
                    ]
                )

        except Product.DoesNotExist:
            return error_response(
                "NOT_FOUND",
                "A requested product was not found.",
                status.HTTP_404_NOT_FOUND,
            )

        order = Order.objects.select_related("dealer").prefetch_related(
            Prefetch(
                "items",
                queryset=OrderItem.objects.select_related("product"),
            )
        ).get(pk=order.pk)

        return Response(
            OrderResponseSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )


class OrderDetailView(APIView):
    """GET /api/orders/{order_number}/"""

    def get(self, request, order_number):
        order = (
            Order.objects.select_related("dealer")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=OrderItem.objects.select_related("product"),
                )
            )
            .filter(order_number=order_number)
            .first()
        )

        if order is None:
            return error_response(
                "NOT_FOUND",
                "Order not found.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response(OrderResponseSerializer(order).data)
