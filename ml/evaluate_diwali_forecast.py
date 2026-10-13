"""Report the non-promotional Diwali slice of the fixed-origin backtest."""

import csv
import math
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "ml" / "forecast_evaluation.csv"
OUTPUT_FILE = ROOT / "ml" / "diwali_backtest_results.csv"


def read_csv(path):
    if not path.is_file():
        raise FileNotFoundError(
            f"Run evaluate_forecast.py first; missing {path}."
        )
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def report_score(rows, prediction_field):
    errors = [
        float(row[prediction_field]) - float(row["actual"])
        for row in rows
    ]
    return mean(abs(error) for error in errors), math.sqrt(
        mean(error * error for error in errors)
    )


def main():
    rows = read_csv(INPUT_FILE)
    eligible = [
        row
        for row in rows
        if int(row["festival_offset"]) in (-4, -3, -2, -1)
        and int(row["is_promo"]) == 0
    ]

    if not eligible:
        raise ValueError("No non-promotional pre-Diwali holdout cases found.")

    print("=== CHRONOLOGICAL DIWALI BACKTEST ===")
    print("Non-promotional, pre-Diwali cases:", len(eligible))
    print(
        "Non-neutral uplift cases:",
        sum(float(row["diwali_multiplier"]) != 1.0 for row in eligible),
    )
    print("Method                  MAE       RMSE")
    for label, field in (
        ("Ordinary baseline", "ordinary_baseline"),
        ("Diwali adjusted", "diwali_adjusted"),
    ):
        mae, rmse = report_score(eligible, field)
        print(f"{label:<22}{mae:>8.3f}{rmse:>11.3f}")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "origin",
        "sku",
        "week_start",
        "horizon_week",
        "festival_offset",
        "actual",
        "ordinary_baseline",
        "diwali_multiplier",
        "diwali_adjusted",
    ]
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {field: row[field] for field in fields}
            for row in eligible
        )
    print(f"Detailed results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
