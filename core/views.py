
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse

from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product
from core.serializers import ProductSerializer


def health(request):
    """GET /api/health/"""
    return JsonResponse({"status": "ok"})



def error_response(code, message, http_status, details=None):
    """Return errors in the required API contract format."""

    def flatten_errors(value, prefix=""):
        result = []

        if isinstance(value, dict):
            for key, nested_value in value.items():
                field = f"{prefix}.{key}" if prefix else key
                result.extend(flatten_errors(nested_value, field))

        elif isinstance(value, (list, tuple)):
            for item in value:
                result.extend(flatten_errors(item, prefix))

        else:
            result.append(f"{prefix}: {value}" if prefix else str(value))

        return result

    if details is None:
        formatted_details = []
    elif isinstance(details, (dict, list, tuple)):
        formatted_details = flatten_errors(details)
    else:
        formatted_details = [str(details)]

    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": formatted_details,
            }
        },
        status=http_status,
    )



class ProductPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class ProductListCreateView(APIView):
    """GET /api/products/ and POST /api/products/"""

    def get(self, request):
        products = Product.objects.all()

        search = request.query_params.get("search", "").strip()
        category = request.query_params.get("category", "").strip()
        vehicle = request.query_params.get("vehicle", "").strip()

        if search:
            products = products.filter(
                Q(sku__icontains=search)
                | Q(name__icontains=search)
                | Q(brand__icontains=search)
            )

        if category:
            products = products.filter(category__iexact=category)

        if vehicle:
            products = products.filter(
                vehicle_fitment__icontains=vehicle
            )

        paginator = ProductPagination()
        page = paginator.paginate_queryset(products, request)
        serializer = ProductSerializer(page, many=True)

        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        if not isinstance(request.data, dict):
            return error_response(
                "VALIDATION_ERROR",
                "Request body must be a JSON object.",
                status.HTTP_400_BAD_REQUEST,
            )

        sku = request.data.get("sku")

        # Return 409 before serializer validation for an existing SKU.
        if isinstance(sku, str):
            sku = sku.strip()

            if Product.objects.filter(sku=sku).exists():
                return error_response(
                    "DUPLICATE",
                    "A product with this SKU already exists.",
                    status.HTTP_409_CONFLICT,
                )

        serializer = ProductSerializer(data=request.data)

        if not serializer.is_valid():
            return error_response(
                "VALIDATION_ERROR",
                "Invalid product data.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )

        try:
            with transaction.atomic():
                product = serializer.save()
        except IntegrityError:
            # Handles a duplicate created concurrently.
            return error_response(
                "DUPLICATE",
                "A product with this SKU already exists.",
                status.HTTP_409_CONFLICT,
            )

        return Response(
            ProductSerializer(product).data,
            status=status.HTTP_201_CREATED,
        )


class ProductDetailView(APIView):
    """GET /api/products/{sku}/ and PATCH /api/products/{sku}/"""

    def get_object(self, sku):
        return Product.objects.filter(sku=sku).first()

    def get(self, request, sku):
        product = self.get_object(sku)

        if product is None:
            return error_response(
                "NOT_FOUND",
                "Product not found.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response(ProductSerializer(product).data)

    def patch(self, request, sku):
        product = self.get_object(sku)

        if product is None:
            return error_response(
                "NOT_FOUND",
                "Product not found.",
                status.HTTP_404_NOT_FOUND,
            )

        if not isinstance(request.data, dict):
            return error_response(
                "VALIDATION_ERROR",
                "Request body must be a JSON object.",
                status.HTTP_400_BAD_REQUEST,
            )

        requested_sku = request.data.get("sku")

        if (
            requested_sku is not None
            and str(requested_sku).strip() != sku
        ):
            return error_response(
                "VALIDATION_ERROR",
                "SKU cannot be changed.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = ProductSerializer(
            product,
            data=request.data,
            partial=True,
        )

        if not serializer.is_valid():
            return error_response(
                "VALIDATION_ERROR",
                "Invalid product data.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )

        product = serializer.save()
        return Response(ProductSerializer(product).data)
