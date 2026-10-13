
from rest_framework import serializers

from core.models import Dealer, Order, OrderItem, Product


class OrderItemInputSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=30)
    quantity = serializers.IntegerField(min_value=1, max_value=1_000_000)

    def validate_sku(self, value):
        return value.strip()


class OrderItemsMixin(serializers.Serializer):
    items = OrderItemInputSerializer(many=True, allow_empty=False)

    def validate_items(self, items):
        seen_skus = set()

        for item in items:
            sku = item["sku"]

            if sku in seen_skus:
                raise serializers.ValidationError(
                    f"Duplicate SKU in order: {sku}"
                )

            seen_skus.add(sku)

        existing = set(
            Product.objects.filter(sku__in=seen_skus).values_list(
                "sku", flat=True
            )
        )
        for item in items:
            if item["sku"] not in existing:
                raise serializers.ValidationError(
                    f"Product not found: {item['sku']}"
                )

        return items


class OrderCreateSerializer(OrderItemsMixin):
    dealer_id = serializers.PrimaryKeyRelatedField(
        queryset=Dealer.objects.all(),
        source="dealer",
    )


class OrderUpdateSerializer(OrderItemsMixin):
    pass


class OrderItemResponseSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            "sku",
            "name",
            "quantity",
            "unit_price",
            "line_subtotal",
            "line_tax",
            "line_total",
        ]


class OrderResponseSerializer(serializers.ModelSerializer):
    dealer_id = serializers.IntegerField(
        source="dealer.id",
        read_only=True,
    )
    items = OrderItemResponseSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "order_number",
            "dealer_id",
            "status",
            "items",
            "subtotal",
            "tax_total",
            "grand_total",
            "created_at",
            "updated_at",
        ]
