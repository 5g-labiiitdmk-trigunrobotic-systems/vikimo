# AI / ML Track Decisions

Answers below address the eight AI / ML track questions in the assignment
PDF. File and line references identify the implementation; measured results
are separated from limitations and assumptions.

## 1. Schema and relationships

`Product` and `Dealer` are catalogue/master records. `Order` belongs to one
dealer through a `PROTECT` foreign key, so deleting a dealer cannot erase
orders. `OrderItem` belongs to an order through `CASCADE` (deleting an order
removes its lines) and references a product through `PROTECT` (ordered
products cannot be deleted). A unique constraint prevents duplicate lines for
the same product in one order. `AssistantSession` belongs to a dealer through
`CASCADE` and stores the bounded conversation as JSON. See
`core/models.py:4-129`.

## 2. Retrieval: ranking, choice, and limitations

`core/assistant_tools.py::search_parts` uses database catalogue fields: exact
SKU first, a small explicit part-name variation map, then keyword matching
over name, SKU, brand, and category, with optional category and vehicle
fitment filters (`core/assistant_tools.py:14`). This is deliberately
database-backed keyword retrieval, **not semantic or embedding retrieval**.
For this small, structured catalogue it is dependency-free and auditable;
there is no measured evidence that a vector database would improve the
assessment. Unmapped paraphrases, misspellings, and unusual synonyms can
still fail or rank poorly. A larger catalogue should be evaluated with
measured retrieval relevance before adopting embeddings or a different
ranking scheme.

## 3. Tool-calling flow

`AssistantChatView` validates the request, binds a session to its dealer,
persists up to 20 provider-valid messages, and supplies bounded prior history
to `run_assistant` (`core/assistant_views.py:17`,
`core/assistant_service.py:378`, `core/assistant_service.py:888`). Complete
assistant
`tool_calls` and corresponding tool-result messages are retained, rather than
reducing a tool-assisted turn to plain text. Malformed or incomplete tool
sequences and unsupported roles are dropped; history truncation removes
complete turns so it cannot leave unmatched tool-call IDs.
The service exposes exactly `search_parts`, `check_stock`, and
`create_draft_order`. It parses each model tool call and dispatches through
`execute_tool`, which allowlists the tool and validates its JSON arguments
before calling application code (`core/assistant_service.py:182`). Tool
names, arguments, results, cited SKUs, and draft actions are returned for
inspection. An explicit database-lookup request that receives prose instead
of a tool call is retried with required tool selection. Model-generated SQL
and arbitrary functions are never executed.

## 4. Current prices, stock, and order outcomes

`check_stock` reads current `Product` rows (`core/assistant_tools.py:165`);
`search_parts` likewise reads the catalogue. Draft creation validates via the
shared order serializer and calculates prices/tax/totals from the database
(`core/assistant_tools.py:192`). The model cannot set dealer identity,
prices, GST, totals, or inventory. The assistant order path creates only
transactional `DRAFT` orders and does not confirm, reserve, or deduct stock.
After tool execution, factual replies are formatted from successful tool
results rather than model prose. These controls establish database grounding
at tool time, not dealer authentication: session-to-dealer binding is not
authentication, and production deployment needs authorization.

## 5. Untrusted catalogue text and prompt injection

The system prompt treats catalogue text as data, not instructions
(`core/assistant_service.py:25`); search
returns selected catalogue fields, and the server allowlists tools and
validates their arguments. After tool calls, deterministic formatting uses
the database-backed result and rejects tool errors as success
(`core/assistant_service.py:817`,
`core/assistant_tools.py`). This reduces prompt
injection impact but does not make the LLM a security boundary or prove every
free-form no-tool response true. No-tool factual claims are screened against
successful retained tool results; current stock and price requests require a
fresh lookup and cannot fall back to old stock or price evidence. If the
provider still returns no tool result, the application reports that it could
not verify the current value. This claim screening is intentionally
conservative and pattern-based, not a general natural-language proof system.
Past-tense SKU follow-ups can use successful retained tool results without
being misclassified as a new lookup request; this does not make prior stock
or price current.
Mocked unit tests exercise server-side behavior; they do not establish
live-provider response quality. A session-to-dealer binding is not
authentication.

## 6. Forecasting approach and leakage controls

`ml/generate_forecast.py:62-103` builds a four-week forecast from each SKU's
latest 12 observed weeks. The ordinary baseline is the mean of
non-promotion observations within those latest 12 weeks, with an all-history
fallback if none is available.
Promotion multipliers use only that SKU's recent pre-forecast history, require
at least three promotion and six regular observations, and are applied only
to planned promotion weeks. Baseline, promotion multiplier, Diwali
multiplier, and final forecast are inspectable in
`ml/forecast_components.csv`; the required `ml/forecast.csv` retains the
three-column contract.

`ml/evaluate_forecast.py` uses chronological, non-overlapping four-week
holdouts. The history is frozen strictly before each period's first target
week, and the same history is used for all four horizons. Each scored
SKU/week also has a same-week-last-year comparator. With 13 origins, 1,976
comparable cases, and 38 SKUs, measured MAE/RMSE were: ordinary baseline
10.715/19.091, recent average 11.378/19.227, last value 14.916/27.279, and
same week last year 12.741/20.886. These are historical synthetic-data
results, not a guarantee of future accuracy. The fixed-origin loop is in
`ml/evaluate_forecast.py:68-77`; the shared cutoff and baseline helpers are
in `ml/forecasting.py:67,77`, and Diwali estimation is at
`ml/forecasting.py:115`.

### Diwali analysis (measured)

The calendar contains Diwali on 2024-11-01, 2025-10-21, and 2026-11-08.
Weeks are Monday-starting. For each event-relative target week, comparisons
pair each SKU's non-promotion sales in that week with its mean non-promotion
sales in the four weeks five to eight weeks earlier. The per-SKU ratio
distribution is summarized by its median; the aggregate units ratio is also
shown below. These are observed associations, not causal estimates.

| Event-relative week | Target week (2025) | Paired SKUs | Aggregate units ratio | Median per-SKU ratio |
|---|---|---:|---:|---:|
| -4 | 2025-09-22 | 34 | 1.3398 | 1.3401 |
| -3 | 2025-09-29 | 36 | 1.5243 | 1.4800 |
| -2 | 2025-10-06 | 36 | 1.6491 | 1.6229 |
| -1 | 2025-10-13 | 35 | 1.9860 | 1.9780 |
| Festival week | 2025-10-20 | 35 | 2.1354 | 2.1493 |

For 2024, there are zero eligible paired SKU windows at offsets -4 to 0:
the required earlier comparison weeks precede this dataset's first week
(2024-09-30). Thus 2025 is the only complete historical Diwali event. The
estimator in `ml/forecasting.py:115` requires at least
two baseline weeks per SKU, 20 matched SKUs per event, and two completed
historical Diwali events. It takes the median per-SKU ratio within each event
and then the median across events, using only records before the forecast
origin. The current 2026 forecast has only one eligible historical event, so
the applied uplift is **1.0** (no adjustment); no percentage is invented.

Chronological validation on the 107 available non-promotion, pre-Diwali
holdout cases measured ordinary-baseline MAE/RMSE of 21.988/30.855 and
Diwali-adjusted MAE/RMSE of 21.988/30.855. No case received non-neutral
uplift because the historical-event threshold was not met. Equality is the
measured fallback result, not evidence that Diwali has no effect. Promotion
dates in the provided calendar cover only the future forecast horizon; the
backtest does not use hindsight promotion flags to alter its predictions.

## 7. Weak SKUs, promotions, rare demand, and new SKUs

In the fixed-origin evaluation, the five largest ordinary-baseline SKU MAEs
were BRK-1019 (27.110), BRK-1085 (23.727), FIL-1031 (22.061), FIL-1051
(19.122), and GRP-1024 (18.402), each over 52 cases. The backtest also
reports MAE/RMSE separately for origins where at least half of training weeks
were zero-sales weeks. That subgroup had 120 cases and ordinary-baseline
MAE/RMSE of 0.662/0.849; the below-50%-zero subgroup had 1,856 cases and
MAE/RMSE of 11.365/19.697. This low aggregate rare-demand error is dominated
by low unit volumes and should not be read as a service-level measure.
See `ml/evaluate_forecast.py:200`.

The supplied history has 40 SKUs: 38 with 104 observations and two recently
launched SKUs, BRK-1105 and CHN-1006, with six observations each. The latter
have no later actual weeks in this file with which to score a holdout. They
receive the mean of their six observed non-promotion weeks; this is a
transparent short-history fallback, not a validated cold-start model.

The planned promotion adjustment is applied only when at least three
promotion and six regular weeks occur in the latest 12 observations. The
current generated components show a non-neutral promotion multiplier on 10
of 160 forecast rows; sparse promotion samples otherwise fall back to 1.0.
Promotion-adjustment quality cannot be fully established from this calendar
because historical future promotion plans are not supplied. Rare/intermittent
SKUs remain a weak area: the baseline includes observed zero weeks, but no
separate intermittent-demand model is supported or justified by the available
backtest.

## 8. Weakest part and next two days

The weakest forecasting assumption is insufficient independent festival
history: one complete Diwali event supports descriptive ratios but not a
reliable uplift estimate. The promotion effect is also sparsely observed and
cannot be fully backtested against historical planned promotions. With two
more days, I would obtain or generate a longer, event-complete and
promotion-plan-aware history, then run rolling-origin subgroup backtests
before revisiting either multiplier. I would not change the current neutral
Diwali fallback without that evidence.

## Reorder rule

`core/reorder_views.py:29,133-161` implements the specified formula: sum
forecast units over the first `L` weeks, calculate sample
standard deviation from the latest 12 observed sales weeks (or all available
weeks), safety stock `ceil(1.65 * sigma * sqrt(L))`, reorder point as demand
plus safety stock, and quantity `max(0, ceil(reorder_point - live_stock))`.
Forecast decimals are preserved. Missing lead times, invalid data, duplicate
weeks, gaps or unexpected dates in the required four-week forecast horizon,
or catalogue gaps return explicit errors without partial suggestions. The
forecast-detail and reorder endpoints both validate against the four contract
weeks rather than accepting any four distinct dates.

## Retrieval and assistant tests

Assistant tests in `core/tests.py` mock Groq and cover request and tool
validation, dispatch, error handling, current database values, draft-order
rules, provider-valid tool-call/result history persistence, contextual
follow-ups grounded in prior tool results, mandatory fresh lookups for current
stock and price, malformed-history rejection, session isolation/truncation,
prompt-injection/unsupported-claim defenses, and UTF-8 rupee-symbol response
bytes. Forecast tests cover output shape/coverage, decimal forecasts,
Diwali uplift and fallback/leakage boundaries, and reorder rounding and
invalid/missing inputs. No unit test requires a live LLM call.

---

# Django Track Decisions

## 1. Where is overselling prevented?

`core/order_services.py:208` (`confirm`). Inside one transaction it locks the
order row (`_lock_order`, line 86) and then every product row with
`SELECT ... FOR UPDATE` in SKU order (`_lock_products`, line 97), checks each
line against the locked stock, and only then deducts. Without the locks, two
confirms for the last 5 units would both read `stock = 5`, both pass the check
and both subtract, leaving stock at -5 (or, with the unsigned column, a database
error) and two promised orders. Locking in SKU order stops two multi-line orders
from deadlocking each other. Tests: `StockTests` in `core/test_order_engine.py`
(six simultaneous confirms for stock 5 give exactly two 200s).

## 2. GST and rounding

`core/order_services.py:49` (`line_amounts`): `line_subtotal = unit_price x
quantity` in `Decimal`, `line_tax = money(line_subtotal x Decimal("0.18"))` with
`ROUND_HALF_UP` (`money`, line 31), per line; order totals are sums of the
rounded lines (`_set_totals`, line 61). Floats cannot represent 0.045 exactly
(it becomes 0.04499...) so it would round down to 0.04 instead of 0.05.
`Decimal`'s default rounding is `ROUND_HALF_EVEN`, which turns 0.125 into 0.12
and 0.045 into 0.04, so the mode is set explicitly.

## 3. Unique order numbers and idempotency under concurrency

Order numbers: `generate_order_number` (`core/order_services.py:35`) increments a
per-day row in `OrderDayCounter` (`core/models.py:128`) under `FOR UPDATE`; the
lock lasts until the creating transaction commits, so concurrent requests queue
and get consecutive numbers. The date uses `timezone.localdate()` with
`TIME_ZONE = "Asia/Kolkata"`. Idempotency: `create_order`
(`core/order_services.py:144`) first inserts the `IdempotencyRecord`
(`core/models.py:138`, unique key). A simultaneous duplicate blocks on that
unique insert until the first request commits, then hits `IntegrityError`
(line 161), reads the stored record, and returns the original order (200), or
`IDEMPOTENCY_CONFLICT` if the body hash differs. If the first request fails, its
key row rolls back with it, so the key stays free.

## 4. Schema and `on_delete`

- `Order.dealer` -> `PROTECT`: a dealer with orders cannot be deleted, so order history survives.
- `OrderItem.order` -> `CASCADE`: items have no meaning without their order.
- `OrderItem.product` -> `PROTECT`: an ordered product cannot be deleted (prices and history stay valid).
- `AssistantSession.dealer` -> `CASCADE`: chat memory is disposable with its dealer.
- `IdempotencyRecord.order` -> `CASCADE`: a key is meaningless without its order.
- `OrderItem` has a unique (order, product) constraint; `OrderDayCounter` is keyed by date; `Product` is keyed by SKU.
See `core/models.py:4-150`.

## 5. Status transitions and cancel-once

`core/order_services.py`: `confirm` (208), `_simple_transition` (245, used by
`dispatch` and `deliver`) and `cancel` (269) each lock the order row and compare
`order.status` to the single status they allow; anything else raises
`INVALID_TRANSITION` (`_invalid_transition`, line 107). Because the check runs
under the row lock, two simultaneous cancels serialize: the first restores stock
and sets CANCELLED, the second sees CANCELLED and is rejected before it touches
stock. Test: `test_concurrent_cancels_restore_once`.

## 6. Supplier sync: rate limits, failures, duplicates, all-or-nothing

`core/supplier_sync.py:51` (`_fetch_page`) allows `MAX_ATTEMPTS = 5` per request:
429 waits `Retry-After` (capped at 30 s), 5xx, malformed pages and connection
errors back off 0.5 s doubling, and 401/other 4xx fail at once. `fetch_feed`
(line 97) follows `next_cursor` to the end and raises `SupplierUnavailable` on
any failure or a repeated cursor. The database is only written in `apply_feed`
(line 189), after the whole feed is in memory, so a failed fetch changes
nothing and the view returns `502 SUPPLIER_UNAVAILABLE`. Invalid items go to
`rejected` (`clean_item`, line 149). Duplicate SKUs keep the latest `updated_at`
(`dedupe`, line 179), after invalid items are removed.

## 7. Created / updated / unchanged, and why a second run is a no-op

`apply_feed` loads existing products for the feed's SKUs, then: missing -> create
with stock 0; any of price, name, category, brand, vehicle_fitment or
description different (compared as `Decimal`/exact strings) -> update only those
columns with `bulk_update`; otherwise unchanged. Stock is never in the update
list. After one sync every compared field equals the feed, so the same feed
gives `created 0, updated 0`. A Postgres advisory lock (line 195) serializes
overlapping syncs.

## 8. Weakest part, and two more days

Order creation in the assistant (`core/assistant_tools.py`) still has its own
copy of the order-building code; I would route it through
`order_services` so there is one implementation. Idempotency records never
expire, there is no authentication (the brief asks for none), and the sync is
synchronous, so a very large feed ties up a request thread. With two more days I
would move sync to a background job, expire old keys, and add load tests with
many more concurrent clients than the unit tests use.
