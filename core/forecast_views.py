
import csv
import math
from pathlib import Path
from datetime import date

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.forecast_contract import EXPECTED_FORECAST_WEEKS
from core.models import Product
from core.views import error_response


class ForecastDetailView(APIView):
    """GET /api/forecast/{sku}/"""

    def get(self, request, sku):
        # The SKU must exist in the product catalogue.
        if not Product.objects.filter(sku=sku).exists():
            return error_response(
                "NOT_FOUND",
                "Product not found.",
                status.HTTP_404_NOT_FOUND,
            )

        forecast_file = Path(settings.BASE_DIR) / "ml" / "forecast.csv"

        if not forecast_file.is_file():
            return error_response(
                "INTERNAL_ERROR",
                "Forecast data is not available.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        forecasts = []

        try:
            with forecast_file.open(
                "r", encoding="utf-8-sig", newline=""
            ) as file:
                reader = csv.DictReader(file)
                if reader.fieldnames != [
                    "sku",
                    "week_start",
                    "forecast_units",
                ]:
                    raise ValueError("Invalid forecast columns.")

                for row in reader:
                    if row["sku"] == sku:
                        week_start = date.fromisoformat(row["week_start"])
                        forecast_units = float(row["forecast_units"])
                        if (
                            not math.isfinite(forecast_units)
                            or forecast_units < 0
                        ):
                            raise ValueError("Invalid forecast value.")
                        forecasts.append({
                            "week_start": week_start.isoformat(),
                            "forecast_units": forecast_units,
                        })
        except (OSError, csv.Error, KeyError, TypeError, ValueError):
            return error_response(
                "INTERNAL_ERROR",
                "Forecast data is invalid.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not forecasts:
            return error_response(
                "NOT_FOUND",
                "Forecast not found for this product.",
                status.HTTP_404_NOT_FOUND,
            )

        forecasts.sort(key=lambda item: item["week_start"])
        if tuple(item["week_start"] for item in forecasts) != EXPECTED_FORECAST_WEEKS:
            return error_response(
                "INTERNAL_ERROR",
                "Forecast data is invalid.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {"sku": sku, "forecast": forecasts},
            status=status.HTTP_200_OK,
        )
