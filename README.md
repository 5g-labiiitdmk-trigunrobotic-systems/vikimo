# VIKMO DealerDesk

**Track: `aiml`** (AI / ML Intern)

- Required parts built: A1 backend core, C Dealer Assistant, D Forecasting and reorder.
- Optional parts attempted: E frontend (a basic portal in `templates/` and `static/`).
- Not built: A2 order engine (confirm/dispatch/deliver/cancel, idempotency) and B supplier sync.
- LLM provider: **Groq**, model `openai/gpt-oss-20b` (override with `GROQ_MODEL`).
- Video walkthrough: _add your unlisted link here before submitting_

Django REST Framework and PostgreSQL backend for the VIKMO DealerDesk
assessment. The project includes the catalogue, dealers, orders, inventory,
supplier sync, Dealer Assistant, and forecasting endpoints.

## Dealer Assistant

The assistant uses Groq tool calling. Set `GROQ_API_KEY` and `GROQ_MODEL` in
the process environment (or in a local `.env`, which must not be committed).
The configured development model is `openai/gpt-oss-20b`; deployments can
select another Groq-supported model with `GROQ_MODEL`. The application does
not silently fall back to another provider or model.

Order numbers use the contract format `ORD-YYYYMMDD-NNNN`: the date is the
Asia/Kolkata creation date and `NNNN` is a per-day sequence kept in the
`OrderDayCounter` table. The counter row is locked inside the order-creating
transaction, so concurrent requests receive unique, gap-free numbers.
`GROQ_MODEL` defaults to `openai/gpt-oss-20b` when unset; only `GROQ_API_KEY`
is required.

`POST /api/assistant/chat/` accepts `session_id`, `dealer_id`, and `message`.
The request is validated, each session is bound to its dealer, and up to 20
provider-valid messages are persisted. History retains complete assistant
tool-call/result sequences as well as user and assistant turns; malformed or
incomplete sequences are discarded. The bounded history is passed to
`run_assistant` in [`core/assistant_service.py`](./core/assistant_service.py).
The service exposes only `search_parts`, `check_stock`, and
`create_draft_order`; arguments are validated in application code before a
tool executes. Each tool call and its result are returned in `tool_calls`,
cited database SKUs are returned in `cited_skus`, and created draft orders
are returned in `actions`.

Search is a database-backed keyword/catalogue-field retrieval alternative to
embeddings. Exact SKU matches are prioritized; the search also handles a small
set of part-name variations and filters by category and vehicle fitment. This
is suitable for the current, structured catalogue and keeps search results
auditable without adding a vector database or embedding dependency. It is not
semantic retrieval: broad paraphrases, misspellings, and synonyms outside the
small explicit mapping may fail or rank imperfectly.

The assistant's order tool requires a clear order request plus the exact SKU
and quantity in user-provided conversation text. The application ignores
model-generated final facts after a tool call and formats stock, price, and
order replies from tool results. Product prices, GST, and totals come from the
database and the existing order serializer/rules; draft creation is
transactional, leaves the order in `DRAFT`, and does not reserve or deduct
stock. The system prompt treats catalogue text as data, and server-side
allowlisting and argument validation prevent arbitrary tools or SQL. These
controls reduce prompt-injection impact, but they do not make an LLM
deterministic or prove the truth of every free-form, no-tool response. A
no-tool response containing catalogue claims is accepted only when its claims
match successful tool results in the retained conversation. Current stock and
price requests require a tool-call retry if the model first answers in prose;
the response falls back to a verification error if no tool result is obtained.
Past-tense follow-ups such as asking which SKU was just found can use validated
history without forcing a redundant lookup; they do not authorize treating old
stock or price as current.
Tool errors are never formatted as successful results. The Groq response is
not trusted for final factual text after tool execution: the application
renders that text from the tool result. Live Groq behavior is not established
by mocked unit tests.

The API response is JSON encoded as UTF-8, including the rupee symbol. The
test suite checks the actual response bytes and their UTF-8 decoding; a mojibake
display limited to a terminal may therefore be a terminal-code-page issue
rather than corrupted API data.

The assignment explicitly requires no authentication. A `session_id` is a
conversation key, not proof of dealer identity; callers can submit a dealer
ID, and production use would require authentication and authorization.

## Forecasting and reorder suggestions

`ml/forecast.csv` contains the required 40-SKU, four-week forecast. The
forecast uses each SKU's most recent non-promotion demand where available,
applies a promotion factor only when its recent sample is sufficient, and
uses a neutral Diwali factor unless at least two historical Diwali events
support the estimate. `ml/forecast_components.csv` is a generated diagnostic
file that keeps baseline, promotion and festival multipliers separate.
The keyword/catalogue search and the festival adjustment are distinct:
retrieval is not semantic search, and the Diwali fallback does not imply
there is no festival effect.

Regenerate and evaluate the forecast with:

```bash
python ml/generate_forecast.py
python ml/evaluate_forecast.py
python ml/evaluate_diwali_forecast.py
```

The evaluation uses fixed-origin chronological four-week holdouts and
strictly pre-origin sales. Its measured results, Diwali evidence, reorder
formula, and data limitations are recorded in [`DECISIONS.md`](./DECISIONS.md).
Reorder suggestions preserve decimal forecast demand and implement the
assignment's exact lead-time demand and safety-stock formula against current
database stock.

## Setup

Create a fresh PostgreSQL database and set `DATABASE_URL` to its connection
URL. For assistant requests, set `GROQ_API_KEY` and `GROQ_MODEL`. You can use
`.env.example` as a list of variable names, but replace its placeholders in
your local environment; never commit `.env`.

From the repository root, install, migrate, seed twice to verify idempotency,
then start the server using the assignment's run contract:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py seed_data
python manage.py seed_data
python manage.py runserver 0.0.0.0:8000 --noreload
```

## Tests

Run the core tests with:

```bash
python manage.py test core
```

Assistant tests mock Groq responses. They cover request validation, tool
validation and dispatch, grounded stock and order results, incomplete-order
refusal, error handling, provider-valid tool-history persistence, contextual
follow-ups, fresh current-stock tool calls, session isolation/truncation,
unsupported-history rejection, and UTF-8 response bytes. Forecast tests cover the
160-row file contract, decimal endpoint and reorder values, sample-deviation
rounding, Diwali date/uplift/fallback and leakage behavior, and invalid or
missing reorder inputs. They do not make live provider requests.

The bundled smoke test can be run against a freshly seeded disposable
database:

```bash
python smoke_test.py --track aiml
```
