# from django.conf import settings
# from django.core.management.base import BaseCommand


# class Command(BaseCommand):
#     help = "Load the files in data/ into the database. Must be safe to run more than once."

#     def handle(self, *args, **options):
#         data_dir = settings.DATA_DIR  # <repo root>/data
#         # TODO: load data_dir / "catalogue.csv" and data_dir / "dealers.csv",
#         # plus anything else your app needs. Running this twice must not create duplicates.
#         self.stdout.write(self.style.WARNING(f"seed_data is not implemented yet (data folder: {data_dir})"))

import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Dealer, Product


class Command(BaseCommand):
    help = "Load products and dealers from CSV files without creating duplicates."

    @transaction.atomic
    def handle(self, *args, **options):
        data_dir = Path(settings.DATA_DIR)
        catalogue_file = data_dir / "catalogue.csv"
        dealers_file = data_dir / "dealers.csv"

        # Check that the required input files exist.
        for file_path in (catalogue_file, dealers_file):
            if not file_path.is_file():
                raise CommandError(f"Required file not found: {file_path}")

        products_created = 0
        products_updated = 0
        dealers_created = 0
        dealers_updated = 0

        # Import products.
        with catalogue_file.open(
            "r", encoding="utf-8-sig", newline=""
        ) as file:
            reader = csv.DictReader(file)

            required_columns = {
                "sku", "name", "category", "brand",
                "vehicle_fitment", "price", "stock", "description",
            }
            if not required_columns.issubset(reader.fieldnames or []):
                raise CommandError("catalogue.csv has missing required columns.")

            for row in reader:
                sku = row["sku"].strip()
                defaults = {
                    "name": row["name"].strip(),
                    "category": row["category"].strip(),
                    "brand": row["brand"].strip(),
                    "vehicle_fitment": row["vehicle_fitment"].strip(),
                    "price": Decimal(row["price"]),
                    "description": row["description"].strip(),
                }

                product, created = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        **defaults,
                        "stock": int(row["stock"]),
                    },
                )

                if created:
                    products_created += 1
                else:
                    # Refresh catalogue details but preserve live inventory.
                    for field, value in defaults.items():
                        setattr(product, field, value)
                    product.save(update_fields=list(defaults.keys()))
                    products_updated += 1

        # Import dealers.
        with dealers_file.open(
            "r", encoding="utf-8-sig", newline=""
        ) as file:
            reader = csv.DictReader(file)

            required_columns = {
                "dealer_code", "name", "city", "phone",
                "email", "gstin", "credit_limit",
            }
            if not required_columns.issubset(reader.fieldnames or []):
                raise CommandError("dealers.csv has missing required columns.")

            for row in reader:
                _, created = Dealer.objects.update_or_create(
                    dealer_code=row["dealer_code"].strip(),
                    defaults={
                        "name": row["name"].strip(),
                        "city": row["city"].strip(),
                        "phone": row["phone"].strip(),
                        "email": row["email"].strip(),
                        "gstin": row["gstin"].strip(),
                        "credit_limit": Decimal(row["credit_limit"]),
                    },
                )

                if created:
                    dealers_created += 1
                else:
                    dealers_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Data seeding completed successfully.\n"
                f"Products created: {products_created}\n"
                f"Products updated: {products_updated}\n"
                f"Dealers created: {dealers_created}\n"
                f"Dealers updated: {dealers_updated}\n"
                f"Total products in database: {Product.objects.count()}\n"
                f"Total dealers in database: {Dealer.objects.count()}"
            )
        )
