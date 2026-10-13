from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SALES_FILE = ROOT / "data" / "sales_history.csv"
OUTPUT_FILE = ROOT / "ml" / "forecast_evaluation_v2.csv"

HORIZON = 4
HISTORY_WINDOW = 12
MIN_TRAIN_WEEKS = 12


def promotion_multiplier(history):
    """Estimate promotion effect from historical data only."""
    promo = history.loc[history["promo_flag"] == 1, "units_sold"]
    regular = history.loc[history["promo_flag"] == 0, "units_sold"]

    if promo.empty or regular.empty or regular.mean() <= 0:
        return 1.0

    ratio = promo.mean() / regular.mean()
    return min(max(float(ratio), 0.5), 2.0)


def main():
    df = pd.read_csv(SALES_FILE)

    required = {"sku", "week_start", "units_sold", "promo_flag"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df["week_start"] = pd.to_datetime(df["week_start"], errors="coerce")
    df["units_sold"] = pd.to_numeric(df["units_sold"], errors="coerce")
    df["promo_flag"] = pd.to_numeric(df["promo_flag"], errors="coerce")
    df = df.dropna(subset=list(required))
    df["units_sold"] = df["units_sold"].clip(lower=0)
    df["promo_flag"] = df["promo_flag"].astype(int)

    # Aggregate duplicates and retain the observed promotion flag.
    weekly = (
        df.groupby(["sku", "week_start"], as_index=False)
        .agg(
            units_sold=("units_sold", "sum"),
            promo_flag=("promo_flag", "max"),
        )
        .sort_values(["sku", "week_start"])
    )

    all_weeks = sorted(weekly["week_start"].unique())
    test_weeks = all_weeks[-HORIZON:]
    predictions = []

    for target_week in test_weeks:
        target_week = pd.Timestamp(target_week)

        for sku, group in weekly.groupby("sku"):
            group = group.sort_values("week_start")

            # Strictly exclude the target week and all later observations.
            history = group[group["week_start"] < target_week].tail(
                HISTORY_WINDOW
            )

            actual_rows = group[group["week_start"] == target_week]
            if len(history) < MIN_TRAIN_WEEKS or actual_rows.empty:
                continue

            actual = float(actual_rows.iloc[0]["units_sold"])
            is_promo = int(actual_rows.iloc[0]["promo_flag"])

            last_value = float(history.iloc[-1]["units_sold"])
            recent_average = float(history["units_sold"].mean())

            # Same week last year: exactly 52 weeks before target.
            prior_year_week = target_week - pd.Timedelta(weeks=52)
            prior_year_rows = group[
                group["week_start"] == prior_year_week
            ]

            if prior_year_rows.empty:
                continue

            same_week_last_year = float(
                prior_year_rows.iloc[0]["units_sold"]
            )

            multiplier = promotion_multiplier(history)
            promotion_aware = recent_average
            if is_promo:
                promotion_aware *= multiplier

            predictions.append({
                "sku": sku,
                "week_start": target_week.date().isoformat(),
                "actual": actual,
                "last_value": max(0.0, last_value),
                "recent_average": max(0.0, recent_average),
                "same_week_last_year": max(0.0, same_week_last_year),
                "promotion_aware": max(0.0, promotion_aware),
                "is_promo": is_promo,
                "promotion_multiplier": multiplier,
                "history_observations": len(history),
            })

    result = pd.DataFrame(predictions)

    if result.empty:
        raise ValueError(
            "No comparable predictions. Check history and target weeks."
        )

    print("=== FAIR CHRONOLOGICAL BACKTEST ===")
    print("Holdout weeks:", ", ".join(sorted(result["week_start"].unique())))
    print("Comparable predictions:", len(result))
    print("SKUs evaluated:", result["sku"].nunique())
    print("Note: SKUs without a same-week-last-year observation are excluded.")
    print("All compared methods use the same evaluated SKU/week cases.")

    methods = [
        ("Last value", "last_value"),
        ("Recent average", "recent_average"),
        ("Same week last year", "same_week_last_year"),
        ("Promotion-aware", "promotion_aware"),
    ]

    scores = []
    for name, column in methods:
        errors = result[column] - result["actual"]
        mae = float(errors.abs().mean())
        rmse = float(np.sqrt((errors ** 2).mean()))

        scores.append({
            "method": name,
            "predictions": len(result),
            "MAE": mae,
            "RMSE": rmse,
        })

    score_df = pd.DataFrame(scores)
    print("\n=== RESULTS (LOWER IS BETTER) ===")
    print(score_df.to_string(index=False, formatters={
        "MAE": "{:.3f}".format,
        "RMSE": "{:.3f}".format,
    }))

    best_baseline = score_df[
        score_df["method"] != "Promotion-aware"
    ]["MAE"].min()

    model_mae = score_df.loc[
        score_df["method"] == "Promotion-aware", "MAE"
    ].iloc[0]

    if best_baseline > 0:
        improvement = (best_baseline - model_mae) / best_baseline * 100
        print(f"\nMAE improvement over best baseline: {improvement:+.2f}%")
    else:
        print("\nBest baseline MAE is zero; percentage improvement is undefined.")

    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nDetailed predictions saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
