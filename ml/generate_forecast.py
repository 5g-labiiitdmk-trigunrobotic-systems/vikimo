import csv
import math
from datetime import date, timedelta
from pathlib import Path
from forecasting import (
    diwali_offset,
    estimate_diwali_uplift,
    ordinary_baseline,
    promotion_multiplier,
    weekly_sales_by_sku,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OUTPUT_FILE = ROOT_DIR / "ml" / "forecast.csv"

FORECAST_WEEKS = [
    "2026-09-28",
    "2026-10-05",
    "2026-10-12",
    "2026-10-19",
]

COMPONENTS_FILE = ROOT_DIR / "ml" / "forecast_components.csv"


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_festival_dates():
    rows = read_csv(DATA_DIR / "festival_calendar.csv")
    dates = []

    for row in rows:
        if row.get("festival", "").strip().lower() == "diwali":
            dates.append(date.fromisoformat(row["date"]))

    if not dates:
        raise ValueError("No Diwali dates found in festival_calendar.csv.")

    return sorted(dates)


def main():
    sales_rows = read_csv(DATA_DIR / "sales_history.csv")
    promo_rows = read_csv(DATA_DIR / "promo_calendar.csv")
    festival_dates = load_festival_dates()

    sales_by_sku = weekly_sales_by_sku(sales_rows)
    last_history_week = max(
        row["week_start"]
        for rows in sales_by_sku.values()
        for row in rows
    )
    forecast_origin = last_history_week + timedelta(weeks=1)
    baseline_by_sku = {}
    promo_effect = {}
    for sku, rows in sales_by_sku.items():
        baseline_by_sku[sku] = ordinary_baseline(rows)
        promo_effect[sku] = promotion_multiplier(rows)

    # Index promotion flags by SKU and forecast week.
    promo_lookup = {}

    for row in promo_rows:
        promo_lookup[(row["sku"], row["week_start"])] = int(
            row["promo_flag"]
        )

    if sum(festival.year == 2026 for festival in festival_dates) != 1:
        raise ValueError("Expected exactly one Diwali date in 2026.")

    # Generate forecasts with inspectable base and independent adjustments.
    forecast_rows = []
    component_rows = []

    for sku in sorted(sales_by_sku):
        history = sales_by_sku[sku]

        if not history:
            continue

        baseline, baseline_observations, baseline_method = baseline_by_sku[sku]

        for week in FORECAST_WEEKS:
            forecast_week = date.fromisoformat(week)
            is_promo = promo_lookup.get((sku, week), 0)
            promo_multiplier = promo_effect[sku] if is_promo else 1.0
            offset = diwali_offset(forecast_week, festival_dates)
            uplift_estimate = estimate_diwali_uplift(
                sales_rows,
                festival_dates,
                forecast_origin,
                offset if offset is not None else 0,
            )
            diwali_multiplier = uplift_estimate["multiplier"]
            forecast = baseline * promo_multiplier * diwali_multiplier

            forecast_units = max(0, math.floor(forecast + 0.5))

            forecast_rows.append({
                "sku": sku,
                "week_start": week,
                "forecast_units": forecast_units,
            })
            component_rows.append({
                "sku": sku,
                "week_start": week,
                "baseline_units": f"{baseline:.6f}",
                "baseline_observations": baseline_observations,
                "baseline_method": baseline_method,
                "promotion_multiplier": f"{promo_multiplier:.6f}",
                "diwali_multiplier": f"{diwali_multiplier:.6f}",
                "diwali_events": uplift_estimate["event_count"],
                "forecast_units": forecast_units,
            })

    expected_skus = 40
    expected_rows = expected_skus * len(FORECAST_WEEKS)

    if len(sales_by_sku) != expected_skus:
        raise ValueError(
            f"Expected {expected_skus} SKUs in sales history, "
            f"found {len(sales_by_sku)}."
        )

    if len(forecast_rows) != expected_rows:
        raise ValueError(
            f"Expected {expected_rows} forecast rows, "
            f"generated {len(forecast_rows)}."
        )

    seen = set()

    for row in forecast_rows:
        key = (row["sku"], row["week_start"])

        if key in seen:
            raise ValueError(f"Duplicate forecast entry: {key}")

        seen.add(key)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open(
        "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["sku", "week_start", "forecast_units"],
        )
        writer.writeheader()
        writer.writerows(forecast_rows)

    with COMPONENTS_FILE.open(
        "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "sku",
                "week_start",
                "baseline_units",
                "baseline_observations",
                "baseline_method",
                "promotion_multiplier",
                "diwali_multiplier",
                "diwali_events",
                "forecast_units",
            ],
        )
        writer.writeheader()
        writer.writerows(component_rows)

    print("Forecast generation completed.")
    print(f"SKUs: {len(sales_by_sku)}")
    print(f"Forecast weeks: {len(FORECAST_WEEKS)}")
    print(f"Forecast rows: {len(forecast_rows)}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Forecast components: {COMPONENTS_FILE}")


if __name__ == "__main__":
    main()
