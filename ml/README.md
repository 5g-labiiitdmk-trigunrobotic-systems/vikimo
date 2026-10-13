# Forecasting

Run these commands from the repository root, in order:

```bash
python ml/generate_forecast.py
python ml/evaluate_forecast.py
python ml/evaluate_diwali_forecast.py
```

`generate_forecast.py` writes the contract CSV, `forecast.csv`, and the
diagnostic `forecast_components.csv`. The latter records the ordinary
baseline, baseline observation count/method, planned-promotion multiplier,
Diwali multiplier, and final value separately. Forecast output is rounded to
non-negative whole units; the API/reorder formula supports decimal forecast
values if present.

The ordinary forecast baseline is the mean of non-promotion observations
within the latest 12 observed weeks, falling back to all recent weeks if
there are no non-promotion observations. A promotion multiplier is estimated
from recent history only and used only for a planned promotion; fewer than
three promo and six regular observations results in a neutral multiplier.
Historical promotion plans are not provided, so the promotion adjustment is
not fully backtestable.

Diwali comparisons pair non-promotion SKU demand at an event-relative week
with that SKU's average non-promotion demand in weeks five through eight
before the target. The estimator requires two non-promotion baseline weeks,
20 paired SKUs in each event, and two completed historical events; it then
uses medians at SKU and event level. All training sales are strictly before
the forecast origin. The supplied history has only one complete Diwali event:
2024 comparisons lack earlier baseline weeks. Therefore 2026 uses a
`1.0` Diwali multiplier rather than assuming an uplift.

`evaluate_forecast.py` reports fixed-origin chronological four-week holdouts.
Training is frozen before each block, and a same-week-last-year comparator
uses only a week already observed by the origin. The Diwali evaluator filters
the held-out, non-promotion, pre-Diwali cases after producing forecasts; it
does not use held-out outcomes to estimate or apply a multiplier. See
[`../DECISIONS.md`](../DECISIONS.md) for measured MAE/RMSE, SKU-level errors,
historical ratios, and limitations.
