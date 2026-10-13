
from django.db import transaction

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product
from core.views import error_response


class InventoryUpdateView(APIView):
    """PUT /api/inventory/{sku}/"""

    def put(self, request, sku):
        product = Product.objects.filter(sku=sku).first()

        if product is None:
            return error_response(
                "NOT_FOUND",
                "Product not found.",
                status.HTTP_404_NOT_FOUND,
            )

        quantity = request.data.get("quantity")

        # Accept integers and digit-only strings, but not booleans,
        # negative numbers, floats, or other values.
        if isinstance(quantity, bool):
            valid_quantity = False
        elif isinstance(quantity, int):
            valid_quantity = quantity >= 0
        elif isinstance(quantity, str):
            valid_quantity = quantity.isdigit()
        else:
            valid_quantity = False

        if not valid_quantity:
            return error_response(
                "VALIDATION_ERROR",
                "Quantity must be a non-negative integer.",
                status.HTTP_400_BAD_REQUEST,
            )

        quantity = int(quantity)

        with transaction.atomic():
            product.stock = quantity
            product.save(update_fields=["stock"])

        return Response(
            {
                "sku": product.sku,
                "quantity": product.stock,
            },
            status=status.HTTP_200_OK,
        )
