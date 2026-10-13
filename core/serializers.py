
from decimal import Decimal

from rest_framework import serializers

from core.models import Product


class ProductSerializer(serializers.ModelSerializer):
    """Serialize product details and validate product input."""

    price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "stock",
            "description",
        ]
        read_only_fields = ["stock"]

    def validate_sku(self, value):
        value = value.strip()

        # A product's SKU cannot be changed after creation.
        if self.instance and value != self.instance.sku:
            raise serializers.ValidationError(
                "SKU cannot be changed."
            )

        return value

    def validate(self, attrs):
        # Stock is managed through the inventory endpoint.
        if not self.instance and "stock" in self.initial_data:
            raise serializers.ValidationError({
                "stock": "Stock must be managed through the inventory API."
            })

        return attrs


from core.models import Dealer


class DealerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]
        read_only_fields = ["id"]

    def validate_dealer_code(self, value):
        value = value.strip()

        queryset = Dealer.objects.filter(dealer_code=value)

        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                "A dealer with this code already exists."
            )

        return value
