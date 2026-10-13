"""Order business rules: totals, status flow, stock, price lock, idempotency."""
import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    IdempotencyRecord,
    Order,
    OrderDayCounter,
    OrderItem,
    Product,
)

TAX_RATE = Decimal("0.18")


class ServiceError(Exception):
    """A business-rule failure that maps to one contract error response."""

    def __init__(self, code, message, http_status, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details


def money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def line_amounts(unit_price, quantity):
    """Exact per-line money; tax is rounded per line, ROUND_HALF_UP."""
    subtotal = money(unit_price * quantity)
    tax = money(subtotal * TAX_RATE)
    return {
        "unit_price": unit_price,
        "line_subtotal": subtotal,
        "line_tax": tax,
        "line_total": subtotal + tax,
    }


def _set_totals(order, items):
    order.subtotal = sum((i.line_subtotal for i in items), Decimal("0.00"))
    order.tax_total = sum((i.line_tax for i in items), Decimal("0.00"))
    order.grand_total = order.subtotal + order.tax_total


def _price_items(order, items, products):
    """Set every item's price fields from `products` and refresh the totals."""
    for item in items:
        for field, value in line_amounts(
            products[item.product_id].price, item.quantity
        ).items():
            setattr(item, field, value)
        item.save(
            update_fields=[
                "unit_price", "line_subtotal", "line_tax", "line_total",
            ]
        )
    _set_totals(order, items)


def _not_found():
    return ServiceError("NOT_FOUND", "Order not found.", 404)


def _lock_order(order_number):
    order = (
        Order.objects.select_for_update()
        .filter(order_number=order_number)
        .first()
    )
    if order is None:
        raise _not_found()
    return order


def _lock_products(skus):
    """Lock product rows in SKU order, so concurrent orders cannot deadlock."""
    return {
        p.sku: p
        for p in Product.objects.select_for_update()
        .filter(sku__in=skus)
        .order_by("sku")
    }


def _invalid_transition(order, action):
    return ServiceError(
        "INVALID_TRANSITION",
        f"Cannot {action} an order that is {order.status}.",
        409,
    )


def request_hash(body):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _new_order(dealer, items):
    """Create a DRAFT with current prices. Call inside transaction.atomic()."""
    skus = [i["sku"] for i in items]
    products = {p.sku: p for p in Product.objects.filter(sku__in=skus)}
    order = Order.objects.create(
        order_number=generate_order_number(),
        dealer=dealer,
        status=Order.Status.DRAFT,
    )
    rows = [
        OrderItem(
            order=order,
            product=products[i["sku"]],
            quantity=i["quantity"],
            **line_amounts(products[i["sku"]].price, i["quantity"]),
        )
        for i in items
    ]
    OrderItem.objects.bulk_create(rows)
    _set_totals(order, rows)
    order.save(update_fields=["subtotal", "tax_total", "grand_total"])
    return order


def create_order(dealer, items, idempotency_key=None, body=None):
    """Create a DRAFT order. Returns (order, created).

    With an Idempotency-Key the key row is inserted first. A concurrent
    duplicate blocks on that unique insert until the first request commits,
    then finds the key and returns the original order instead of a new one.
    """
    with transaction.atomic():
        if idempotency_key is None:
            return _new_order(dealer, items), True

        digest = request_hash(body)
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    key=idempotency_key, request_hash=digest
                )
        except IntegrityError:
            record = IdempotencyRecord.objects.get(key=idempotency_key)
            if record.request_hash != digest:
                raise ServiceError(
                    "IDEMPOTENCY_CONFLICT",
                    "This Idempotency-Key was already used with a "
                    "different request body.",
                    409,
                )
            return record.order, False

        order = _new_order(dealer, items)
        record.order = order
        record.save(update_fields=["order"])
        return order, True


def replace_items(order_number, items):
    with transaction.atomic():
        order = _lock_order(order_number)
        if order.status != Order.Status.DRAFT:
            raise ServiceError(
                "ORDER_LOCKED",
                f"Only DRAFT orders can be edited; this order is "
                f"{order.status}.",
                409,
            )
        products = {
            p.sku: p
            for p in Product.objects.filter(sku__in=[i["sku"] for i in items])
        }
        order.items.all().delete()
        rows = [
            OrderItem(
                order=order,
                product=products[i["sku"]],
                quantity=i["quantity"],
                **line_amounts(products[i["sku"]].price, i["quantity"]),
            )
            for i in items
        ]
        OrderItem.objects.bulk_create(rows)
        _set_totals(order, rows)
        order.save()  # also bumps updated_at
        return order


def confirm(order_number):
    """DRAFT -> CONFIRMED: check every line, deduct stock, lock prices."""
    with transaction.atomic():
        order = _lock_order(order_number)
        if order.status != Order.Status.DRAFT:
            raise _invalid_transition(order, "confirm")

        items = list(order.items.all())
        products = _lock_products([i.product_id for i in items])

        short = [
            {
                "sku": i.product_id,
                "requested": i.quantity,
                "available": products[i.product_id].stock,
            }
            for i in items
            if i.quantity > products[i.product_id].stock
        ]
        if short:
            message = " ".join(
                f"Insufficient stock for {products[s['sku']].name}. "
                f"Available: {s['available']}, Requested: {s['requested']}"
                for s in short
            )
            raise ServiceError("INSUFFICIENT_STOCK", message, 409, short)

        for item in items:
            product = products[item.product_id]
            product.stock -= item.quantity
            product.save(update_fields=["stock"])
        _price_items(order, items, products)  # the price is locked from here
        order.status = Order.Status.CONFIRMED
        order.save()
        return order


def _simple_transition(order_number, from_status, to_status, action):
    with transaction.atomic():
        order = _lock_order(order_number)
        if order.status != from_status:
            raise _invalid_transition(order, action)
        order.status = to_status
        order.save()
        return order


def dispatch(order_number):
    return _simple_transition(
        order_number, Order.Status.CONFIRMED, Order.Status.DISPATCHED,
        "dispatch",
    )


def deliver(order_number):
    return _simple_transition(
        order_number, Order.Status.DISPATCHED, Order.Status.DELIVERED,
        "deliver",
    )


def cancel(order_number):
    """DRAFT or CONFIRMED -> CANCELLED. Confirmed stock returns exactly once.

    The status check runs under the order row lock, so a second cancel sees
    CANCELLED and is rejected before it can restore anything again.
    """
    with transaction.atomic():
        order = _lock_order(order_number)
        if order.status not in (Order.Status.DRAFT, Order.Status.CONFIRMED):
            raise _invalid_transition(order, "cancel")

        if order.status == Order.Status.CONFIRMED:
            items = list(order.items.all())
            products = _lock_products([i.product_id for i in items])
            for item in items:
                product = products[item.product_id]
                product.stock += item.quantity
                product.save(update_fields=["stock"])

        order.status = Order.Status.CANCELLED
        order.save()
        return order


def refresh_draft_prices(order):
    """A DRAFT follows the current catalogue price. Returns True if changed."""
    if order.status != Order.Status.DRAFT:
        return False
    if all(i.unit_price == i.product.price for i in order.items.all()):
        return False
    with transaction.atomic():
        locked = _lock_order(order.order_number)
        if locked.status != Order.Status.DRAFT:
            return False
        items = list(locked.items.all())
        products = {
            p.sku: p
            for p in Product.objects.filter(sku__in=[i.product_id for i in items])
        }
        _price_items(locked, items, products)
        locked.save()
    return True
