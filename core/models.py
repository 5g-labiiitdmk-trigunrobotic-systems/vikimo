from django.db import models


class Product(models.Model):
    sku = models.CharField(max_length=30, primary_key=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100)
    brand = models.CharField(max_length=100)
    vehicle_fitment = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["sku"]

    def __str__(self):
        return f"{self.sku} - {self.name}"


class Dealer(models.Model):
    dealer_code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=100, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    gstin = models.CharField(max_length=30, blank=True, default="")
    credit_limit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    class Meta:
        ordering = ["dealer_code"]

    def __str__(self):
        return f"{self.dealer_code} - {self.name}"


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        CONFIRMED = "CONFIRMED", "Confirmed"
        DISPATCHED = "DISPATCHED", "Dispatched"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    order_number = models.CharField(max_length=30, unique=True)
    dealer = models.ForeignKey(
        Dealer,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    subtotal = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    tax_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    grand_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_subtotal = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    line_tax = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    line_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product"],
                name="unique_product_per_order",
            ),
        ]

    def __str__(self):
        return f"{self.order.order_number}: {self.product.sku}"


class AssistantSession(models.Model):
    session_id = models.CharField(max_length=100, unique=True)
    dealer = models.ForeignKey(
        Dealer,
        on_delete=models.CASCADE,
        related_name="assistant_sessions",
    )
    history = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.session_id


class OrderDayCounter(models.Model):
    """Last order sequence number issued for one Asia/Kolkata calendar day."""

    date = models.DateField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.date}: {self.last_number}"


class IdempotencyRecord(models.Model):
    """Remembers which order an Idempotency-Key created, and for which body."""

    key = models.CharField(max_length=255, unique=True)
    request_hash = models.CharField(max_length=64)
    order = models.ForeignKey(
        Order,
        null=True,
        on_delete=models.CASCADE,
        related_name="idempotency_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.key
