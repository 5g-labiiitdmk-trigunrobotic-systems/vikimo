import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q

from core.models import Order, OrderItem, Product
from core.order_serializers import OrderCreateSerializer
from core.order_views import generate_order_number

logger = logging.getLogger(__name__)


def search_parts(query, category=None, vehicle=None, limit=10):
    """
    Search catalogue products.

    Priority:
    1. Exact SKU match.
    2. Specific part-name match.
    3. General search across name, SKU, brand, and category.

    Explicit category and vehicle filters are preserved.
    Results are limited to a maximum of 10 products.
    """

    query = (query or "").strip()
    limit = max(1, min(int(limit), 10))

    products = Product.objects.all()

    if category:
        products = products.filter(
            category__iexact=category.strip()
        )

    if vehicle:
        products = products.filter(
            vehicle_fitment__icontains=vehicle.strip()
        )

    if not query:
        matches = list(
            products.order_by("sku")[:limit]
        )

    else:
        # An exact SKU should return only that product.
        exact_product = products.filter(
            sku__iexact=query
        ).first()

        if exact_product:
            matches = [exact_product]

        else:
            words = query.lower().split()

            # Map common part-name variations to a searchable term.
            part_types = {
                "pad": "pad",
                "pads": "pad",
                "shoe": "shoe",
                "shoes": "shoe",
                "lever": "lever",
                "levers": "lever",
                "rotor": "rotor",
                "rotors": "rotor",
                "fluid": "fluid",
            }

            requested_type = next(
                (
                    part_types[word]
                    for word in words
                    if word in part_types
                ),
                None,
            )

            if requested_type:
                # Match the requested part type in the product name.
                name_query = Q(
                    name__icontains=requested_type
                )

                # Important correction:
                # "brake pads" must not return "Tank Pad" products.
                if "brake" in words:
                    name_query &= Q(
                        name__icontains="brake"
                    )

                # If the query contains another descriptive word,
                # require that word in the product name too.
                descriptive_words = [
                    word
                    for word in words
                    if word not in {
                        "brake",
                        "pad",
                        "pads",
                        "shoe",
                        "shoes",
                        "lever",
                        "levers",
                        "rotor",
                        "rotors",
                        "fluid",
                        "the",
                        "a",
                        "an",
                        "for",
                    }
                ]

                for word in descriptive_words:
                    name_query &= Q(
                        name__icontains=word
                    )

                matches = list(
                    products.filter(name_query)
                    .distinct()
                    .order_by("sku")
                )

            else:
                # General search: each query word must match at least
                # one of the supported product fields.
                word_query = Q()

                for word in words:
                    word_query &= (
                        Q(name__icontains=word)
                        | Q(sku__icontains=word)
                        | Q(brand__icontains=word)
                        | Q(category__icontains=word)
                    )

                matches = list(
                    products.filter(word_query)
                    .distinct()
                    .order_by("sku")
                )

            matches = matches[:limit]

    return {
        "results": [
            {
                "sku": product.sku,
                "name": product.name,
                "category": product.category,
                "brand": product.brand,
                "vehicle_fitment": product.vehicle_fitment,
                "price": f"{product.price:.2f}",
                "stock": product.stock,
            }
            for product in matches
        ]
    }


def check_stock(sku):
    """
    Return the current database price and stock for a SKU.
    Does not modify inventory.
    """

    sku = (sku or "").strip()

    product = Product.objects.filter(
        sku__iexact=sku
    ).first()

    if product is None:
        return {
            "error": "Product not found.",
            "sku": sku,
        }

    return {
        "sku": product.sku,
        "name": product.name,
        "price": f"{product.price:.2f}",
        "stock": product.stock,
        "available": product.stock > 0,
    }


def create_draft_order(dealer_id, items):
    """
    Validate and create a DRAFT order using current catalogue prices.

    - Does not confirm or submit the order.
    - Does not deduct stock.
    - Calculates 18% tax using Decimal arithmetic.
    - Uses current database prices, not prices supplied by the LLM.
    """

    serializer = OrderCreateSerializer(
        data={
            "dealer_id": dealer_id,
            "items": items,
        }
    )

    if not serializer.is_valid():
        return {
            "error": "Order validation failed.",
            "details": serializer.errors,
        }

    validated = serializer.validated_data
    dealer = validated["dealer"]
    requested_items = validated["items"]

    tax_rate = Decimal("0.18")
    money_quantum = Decimal("0.01")

    def money(value):
        return value.quantize(
            money_quantum,
            rounding=ROUND_HALF_UP,
        )

    try:
        with transaction.atomic():
            skus = [
                item["sku"]
                for item in requested_items
            ]

            # Lock the selected product rows while reading their prices.
            products = {
                product.sku: product
                for product in Product.objects.select_for_update().filter(
                    sku__in=skus
                )
            }

            missing = [
                sku
                for sku in skus
                if sku not in products
            ]

            if missing:
                return {
                    "error": "Product not found.",
                    "details": missing,
                }

            order = Order.objects.create(
                order_number=generate_order_number(),
                dealer=dealer,
                status="DRAFT",
                subtotal=Decimal("0.00"),
                tax_total=Decimal("0.00"),
                grand_total=Decimal("0.00"),
            )

            order_items = []
            subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")

            for item in requested_items:
                product = products[item["sku"]]
                quantity = item["quantity"]

                # Always use the current catalogue price.
                unit_price = product.price

                line_subtotal = money(
                    unit_price * quantity
                )

                line_tax = money(
                    line_subtotal * tax_rate
                )

                line_total = money(
                    line_subtotal + line_tax
                )

                subtotal += line_subtotal
                tax_total += line_tax

                order_items.append(
                    OrderItem(
                        order=order,
                        product=product,
                        quantity=quantity,
                        unit_price=unit_price,
                        line_subtotal=line_subtotal,
                        line_tax=line_tax,
                        line_total=line_total,
                    )
                )

            OrderItem.objects.bulk_create(order_items)

            order.subtotal = money(subtotal)
            order.tax_total = money(tax_total)
            order.grand_total = money(
                order.subtotal + order.tax_total
            )

            order.save(
                update_fields=[
                    "subtotal",
                    "tax_total",
                    "grand_total",
                ]
            )

        return {
            "order_number": order.order_number,
            "status": order.status,
            "dealer_id": dealer.id,
            "subtotal": f"{order.subtotal:.2f}",
            "tax_total": f"{order.tax_total:.2f}",
            "grand_total": f"{order.grand_total:.2f}",
            "items": [
                {
                    "sku": item.product.sku,
                    "quantity": item.quantity,
                    "unit_price": f"{item.unit_price:.2f}",
                    "line_total": f"{item.line_total:.2f}",
                }
                for item in order_items
            ],
        }

    except Exception:
        # Keep internal exception details out of assistant responses.
        logger.exception("Draft order creation failed.")
        return {
            "error": "Unable to create the draft order."
        }
