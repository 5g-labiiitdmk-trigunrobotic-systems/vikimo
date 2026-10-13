
import csv
import math
from pathlib import Path
from statistics import stdev

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from core.forecast_contract import EXPECTED_FORECAST_WEEKS
from core.models import Product
from core.views import error_response


DATA_DIR = Path(settings.BASE_DIR) / "data"
FORECAST_FILE = Path(settings.BASE_DIR) / "ml" / "forecast.csv"
SALES_FILE = DATA_DIR / "sales_history.csv"
LEAD_TIME_FILE = DATA_DIR / "lead_times.csv"


def read_csv(path):
    if not path.is_file():
        raise FileNotFoundError(f"Required data file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


class ReorderSuggestionsView(APIView):
    """Return reorder calculations for all forecast SKUs."""

    def get(self, request):
        try:
            forecast_rows = read_csv(FORECAST_FILE)
            sales_rows = read_csv(SALES_FILE)
            lead_time_rows = read_csv(LEAD_TIME_FILE)
        except OSError:
            return error_response(
                "INTERNAL_ERROR",
                "Required forecast or inventory planning data is unavailable.",
                500,
            )

        # Group forecasts by SKU and sort by week.
        forecasts_by_sku = {}
        try:
            if not forecast_rows:
                raise ValueError("Forecast data is empty.")
            for row in forecast_rows:
                sku = row["sku"].strip()
                week_start = row["week_start"].strip()
                forecast_units = float(row["forecast_units"])
                if (
                    not sku
                    or not week_start
                    or not math.isfinite(forecast_units)
                    or forecast_units < 0
                ):
                    raise ValueError("Invalid forecast row.")
                forecasts_by_sku.setdefault(sku, []).append({
                    "week_start": week_start,
                    "forecast_units": forecast_units,
                })

            for sku in forecasts_by_sku:
                forecasts_by_sku[sku].sort(
                    key=lambda item: item["week_start"]
                )
                weeks = [
                    item["week_start"]
                    for item in forecasts_by_sku[sku]
                ]
                if tuple(weeks) != EXPECTED_FORECAST_WEEKS:
                    raise ValueError("Invalid forecast weeks.")

            # Lead time is an integer number of weeks under the API contract.
            lead_times = {}
            for row in lead_time_rows:
                sku = row["sku"].strip()
                raw_lead_time = row["lead_time_weeks"].strip()
                lead_time = int(raw_lead_time)

                if not sku or lead_time < 1 or sku in lead_times:
                    raise ValueError("Invalid lead-time row.")
                lead_times[sku] = lead_time
            if not lead_times or set(forecasts_by_sku) != set(lead_times):
                raise ValueError("Forecast SKUs and lead-time SKUs differ.")

            # Keep the latest 12 sales weeks for each SKU.
            sales_by_sku = {}
            for row in sales_rows:
                sku = row["sku"].strip()
                week_start = row["week_start"].strip()
                units = int(row["units_sold"])
                if not sku or not week_start or units < 0:
                    raise ValueError("Invalid sales-history row.")
                sales_by_sku.setdefault(sku, []).append((
                    week_start,
                    units,
                ))

            for sku in sales_by_sku:
                sales_by_sku[sku].sort(key=lambda item: item[0])
                sales_by_sku[sku] = sales_by_sku[sku][-12:]
        except (KeyError, TypeError, ValueError, OverflowError):
            return error_response(
                "INTERNAL_ERROR",
                "Forecast or inventory planning data is invalid.",
                500,
            )

        suggestions = []

        for sku in sorted(forecasts_by_sku):
            weekly_forecasts = forecasts_by_sku[sku]
            lead_time = lead_times.get(sku)

            if lead_time is None:
                return error_response(
                    "INTERNAL_ERROR",
                    f"Lead time is missing for SKU {sku}.",
                    500,
                )

            if len(weekly_forecasts) < lead_time:
                return error_response(
                    "INTERNAL_ERROR",
                    "Forecast horizon is shorter than a product lead time.",
                    500,
                )

            # Sum forecasts across the first L weeks.
            lead_time_demand = sum(
                item["forecast_units"]
                for item in weekly_forecasts[:lead_time]
            )

            # Sample standard deviation requires at least two observations.
            sales_values = [
                units for _, units in sales_by_sku.get(sku, [])
            ]
            sigma = stdev(sales_values) if len(sales_values) >= 2 else 0.0

            safety_stock = math.ceil(
                1.65 * sigma * math.sqrt(lead_time)
            )

            reorder_point = lead_time_demand + safety_stock

            product = Product.objects.filter(sku=sku).first()
            if product is None:
                return error_response(
                    "INTERNAL_ERROR",
                    f"Forecast SKU {sku} is missing from the product catalogue.",
                    500,
                )

            current_stock = product.stock
            reorder_qty = max(
                0,
                math.ceil(reorder_point - current_stock),
            )

            suggestions.append({
                "sku": sku,
                "current_stock": current_stock,
                "lead_time_weeks": lead_time,
                "lead_time_demand": lead_time_demand,
                "sigma": round(sigma, 2),
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "reorder_qty": reorder_qty,
            })

        return Response(
    {"results": suggestions},
    status=200,
)
