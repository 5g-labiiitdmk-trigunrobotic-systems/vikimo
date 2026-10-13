"""Shared, leakage-aware forecasting helpers for DealerDesk."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median


HISTORY_WINDOW = 12
DIWALI_LOOKBACK_WEEKS = 4
MIN_DIWALI_EVENTS = 2
MIN_DIWALI_SKUS_PER_EVENT = 20
MIN_DIWALI_BASELINE_WEEKS = 2
MIN_PROMO_WEEKS = 3
MIN_REGULAR_WEEKS = 6


def monday_of(value):
    current = value if isinstance(value, date) else date.fromisoformat(str(value))
    return current - timedelta(days=current.weekday())


def diwali_offset(target_week, festival_dates):
    """Return the relative week before the next Diwali, or None."""
    target_monday = monday_of(target_week)
    upcoming = sorted(
        monday_of(festival)
        for festival in festival_dates
        if monday_of(festival) >= target_monday
    )
    if not upcoming:
        return None

    offset = (target_monday - upcoming[0]).days // 7
    if -DIWALI_LOOKBACK_WEEKS <= offset <= -1:
        return offset
    return None


def weekly_sales_by_sku(sales_rows, cutoff=None):
    """Aggregate weekly units and promo flags using rows before cutoff only."""
    cutoff_monday = monday_of(cutoff) if cutoff is not None else None
    totals = defaultdict(int)
    promo_flags = defaultdict(int)

    for row in sales_rows:
        week = monday_of(row["week_start"])
        if cutoff_monday is not None and week >= cutoff_monday:
            continue
        key = (row["sku"], week)
        totals[key] += max(0, int(row["units_sold"]))
        promo_flags[key] = max(promo_flags[key], int(row["promo_flag"]))

    return {
        sku: [
            {
                "week_start": week,
                "units_sold": units,
                "promo_flag": promo_flags[(sku, week)],
            }
            for (row_sku, week), units in sorted(totals.items())
            if row_sku == sku
        ]
        for sku in sorted({sku for sku, _ in totals})
    }


def history_before_origin(history, origin, window=HISTORY_WINDOW):
    """Return the most recent observations strictly before one forecast origin."""
    cutoff = monday_of(origin)
    return [
        row
        for row in history
        if row["week_start"] < cutoff
    ][-window:]


def ordinary_baseline(history, window=HISTORY_WINDOW):
    """Mean non-promotional demand in the latest window, without future rows."""
    recent = history[-window:]
    regular = [
        row["units_sold"]
        for row in recent
        if row["promo_flag"] == 0
    ]
    if regular:
        return mean(regular), len(regular), "non_promo"
    if recent:
        return (
            mean(row["units_sold"] for row in recent),
            len(recent),
            "all_history_fallback",
        )
    return 0.0, 0, "no_history"


def promotion_multiplier(history):
    """Estimate a bounded promo ratio from pre-origin observations only."""
    promo = [
        row["units_sold"]
        for row in history
        if row["promo_flag"] == 1
    ]
    regular = [
        row["units_sold"]
        for row in history
        if row["promo_flag"] == 0
    ]
    if len(promo) < MIN_PROMO_WEEKS or len(regular) < MIN_REGULAR_WEEKS:
        return 1.0
    if mean(regular) <= 0:
        return 1.0
    return min(max(mean(promo) / mean(regular), 0.5), 2.0)


def estimate_diwali_uplift(
    sales_rows,
    festival_dates,
    origin,
    offset,
    min_events=MIN_DIWALI_EVENTS,
    min_skus_per_event=MIN_DIWALI_SKUS_PER_EVENT,
):
    """Estimate a Diwali multiplier from completed, non-promo events only.

    Each historical festival contributes one median of matched per-SKU ratios.
    A matched SKU needs a non-promotional target week, at least two available
    non-promotional baseline weeks, and positive baseline demand. Baseline
    weeks are offsets -5 through -8 from the event-relative target week.
    """
    origin_monday = monday_of(origin)
    if not -DIWALI_LOOKBACK_WEEKS <= offset <= -1:
        return {
            "multiplier": 1.0,
            "event_count": 0,
            "sku_count": 0,
            "events": [],
        }

    weekly = weekly_sales_by_sku(sales_rows, cutoff=origin_monday)
    estimates = []
    for festival in sorted(festival_dates):
        festival_date = (
            festival if isinstance(festival, date) else date.fromisoformat(str(festival))
        )
        festival_week = monday_of(festival_date)
        if festival_week >= origin_monday:
            continue

        event_week = festival_week + timedelta(weeks=offset)
        if event_week >= origin_monday:
            continue

        sku_ratios = []
        for sku, rows in weekly.items():
            by_week = {row["week_start"]: row for row in rows}
            event = by_week.get(event_week)
            if event is None or event["promo_flag"] != 0:
                continue

            baseline_weeks = [
                event_week - timedelta(weeks=distance)
                for distance in range(5, 9)
            ]
            baseline_units = [
                by_week[week]["units_sold"]
                for week in baseline_weeks
                if week in by_week and by_week[week]["promo_flag"] == 0
            ]
            if (
                len(baseline_units) < MIN_DIWALI_BASELINE_WEEKS
                or mean(baseline_units) <= 0
            ):
                continue
            sku_ratios.append(event["units_sold"] / mean(baseline_units))

        if len(sku_ratios) >= min_skus_per_event:
            estimates.append({
                "year": festival_date.year,
                "sku_count": len(sku_ratios),
                "multiplier": median(sku_ratios),
            })

    if len(estimates) < min_events:
        return {
            "multiplier": 1.0,
            "event_count": len(estimates),
            "sku_count": max(
                (event["sku_count"] for event in estimates),
                default=0,
            ),
            "events": estimates,
        }

    return {
        "multiplier": median(event["multiplier"] for event in estimates),
        "event_count": len(estimates),
        "sku_count": min(event["sku_count"] for event in estimates),
        "events": estimates,
    }
