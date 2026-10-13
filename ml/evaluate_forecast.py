"""Fixed-origin, chronological backtest for the four-week demand forecast."""

import csv
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean

from forecasting import (
    HISTORY_WINDOW,
    diwali_offset,
    estimate_diwali_uplift,
    history_before_origin,
    ordinary_baseline,
    weekly_sales_by_sku,
)


ROOT = Path(__file__).resolve().parent.parent
SALES_FILE = ROOT / "data" / "sales_history.csv"
FESTIVAL_FILE = ROOT / "data" / "festival_calendar.csv"
OUTPUT_FILE = ROOT / "ml" / "forecast_evaluation.csv"
HORIZON = 4
YEAR_WEEKS = 52
MIN_TRAIN_WEEKS = 12


def read_csv(path):
    if not path.is_file():
        raise FileNotFoundError(f"Required data file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def scores(rows, field):
    errors = [
        float(row[field]) - float(row["actual"])
        for row in rows
    ]
    return (
        mean(abs(error) for error in errors),
        math.sqrt(mean(error * error for error in errors)),
    )


def main():
    sales_rows = read_csv(SALES_FILE)
    festival_rows = read_csv(FESTIVAL_FILE)
    festival_dates = [
        row["date"]
        for row in festival_rows
        if row.get("festival", "").strip().lower() == "diwali"
    ]
    histories = weekly_sales_by_sku(sales_rows)
    actual = {
        (sku, row["week_start"]): row
        for sku, rows in histories.items()
        for row in rows
    }
    all_weeks = sorted({week for _, week in actual})
    skus = sorted(histories)
    predictions = []
    uplift_cache = {}

    # Every block uses one fixed origin; no target-week outcome is added
    # to the history used to forecast a later week in the same block.
    for start_index in range(
        YEAR_WEEKS,
        len(all_weeks) - HORIZON + 1,
        HORIZON,
    ):
        target_weeks = all_weeks[start_index:start_index + HORIZON]
        origin = target_weeks[0]

        for sku in skus:
            history = history_before_origin(
                histories[sku],
                origin,
                HISTORY_WINDOW,
            )
            if len(history) < MIN_TRAIN_WEEKS:
                continue

            recent_average = mean(row["units_sold"] for row in history)
            ordinary, _, _ = ordinary_baseline(history)
            last_value = history[-1]["units_sold"]
            zero_fraction = sum(
                row["units_sold"] == 0 for row in history
            ) / len(history)

            for target_week in target_weeks:
                actual_row = actual.get((sku, target_week))
                if actual_row is None:
                    continue

                prior_year_week = target_week - timedelta(weeks=YEAR_WEEKS)
                prior_year = actual.get((sku, prior_year_week))
                if prior_year is None:
                    continue

                offset = diwali_offset(target_week, festival_dates)
                cache_key = (origin, offset)
                if cache_key not in uplift_cache:
                    uplift_cache[cache_key] = estimate_diwali_uplift(
                        sales_rows,
                        festival_dates,
                        origin,
                        offset if offset is not None else 0,
                    )["multiplier"]
                uplift = uplift_cache[cache_key]

                predictions.append({
                    "forecast_period_start": origin.isoformat(),
                    "origin": origin.isoformat(),
                    "sku": sku,
                    "week_start": target_week.isoformat(),
                    "horizon_week": (target_week - origin).days // 7 + 1,
                    "actual": actual_row["units_sold"],
                    "last_value": max(0, last_value),
                    "recent_average": max(0, recent_average),
                    "ordinary_baseline": max(0, ordinary),
                    "same_week_last_year": max(0, prior_year["units_sold"]),
                    "diwali_adjusted": max(0, ordinary * uplift),
                    "diwali_multiplier": uplift,
                    "festival_offset": offset or 0,
                    "is_promo": actual_row["promo_flag"],
                    "training_weeks": len(history),
                    "training_zero_fraction": zero_fraction,
                })

    if not predictions:
        raise ValueError("No comparable predictions were evaluated.")

    methods = [
        ("Last value", "last_value"),
        ("Recent average", "recent_average"),
        ("Ordinary baseline", "ordinary_baseline"),
        ("Same week last year", "same_week_last_year"),
    ]

    print("=== FIXED-ORIGIN CHRONOLOGICAL BACKTEST ===")
    print("Forecast periods:", len({row["origin"] for row in predictions}))
    print("Evaluated weeks:", len({row["week_start"] for row in predictions}))
    print("SKUs evaluated:", len({row["sku"] for row in predictions}))
    print("Comparable predictions:", len(predictions))
    print("History is frozen at each four-week period origin.")
    print("Lower MAE and RMSE are better.\n")
    print(f"{'Method':<24}{'N':>8}{'MAE':>12}{'RMSE':>12}")
    for label, field in methods:
        mae, rmse = scores(predictions, field)
        print(f"{label:<24}{len(predictions):>8}{mae:>12.3f}{rmse:>12.3f}")

    diwali_rows = [
        row for row in predictions
        if row["festival_offset"] in (-4, -3, -2, -1)
        and row["is_promo"] == 0
    ]
    print("\n=== DIWALI-ELIGIBLE, NON-PROMO CASES ===")
    print("Comparable cases:", len(diwali_rows))
    print(
        "Cases receiving non-neutral uplift:",
        sum(row["diwali_multiplier"] != 1.0 for row in diwali_rows),
    )
    if diwali_rows:
        for label, field in (
            ("Ordinary baseline", "ordinary_baseline"),
            ("Diwali adjusted", "diwali_adjusted"),
        ):
            mae, rmse = scores(diwali_rows, field)
            print(f"{label}: MAE {mae:.3f}, RMSE {rmse:.3f}")
    else:
        print("No uplift passed the historical evidence threshold.")

    print("\n=== WEAKEST SKUs BY ORDINARY-BASELINE MAE ===")
    by_sku = defaultdict(list)
    for row in predictions:
        by_sku[row["sku"]].append(row)
    sku_scores = sorted(
        (
            (sku, scores(rows, "ordinary_baseline")[0], len(rows))
            for sku, rows in by_sku.items()
        ),
        key=lambda result: result[1],
        reverse=True,
    )
    for sku, mae, count in sku_scores[:5]:
        print(f"{sku}: MAE {mae:.3f} ({count} cases)")

    intermittent = [
        row for row in predictions
        if row["training_zero_fraction"] >= 0.5
    ]
    regular_demand = [
        row for row in predictions
        if row["training_zero_fraction"] < 0.5
    ]
    print("\n=== DEMAND-INTERMITTENCY GROUPS ===")
    for label, rows in (
        ("At least 50% zero training weeks", intermittent),
        ("Below 50% zero training weeks", regular_demand),
    ):
        if not rows:
            print(f"{label}: no comparable cases")
            continue
        mae, rmse = scores(rows, "ordinary_baseline")
        print(
            f"{label}: {len(rows)} cases, "
            f"MAE {mae:.3f}, RMSE {rmse:.3f}"
        )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(predictions[0]))
        writer.writeheader()
        writer.writerows(predictions)
    print(f"\nDetailed results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
