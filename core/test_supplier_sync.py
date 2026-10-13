import importlib.util
import time
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core import supplier_sync
from core.models import Product

_spec = importlib.util.spec_from_file_location(
    "mock_supplier",
    Path(settings.BASE_DIR) / "supplier_mock" / "mock_supplier.py",
)
mock_supplier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_supplier)

KEY = "test-key"


def item(sku, price="100.00", updated="2026-10-01T10:00:00Z", **over):
    base = {
        "supplier_sku": f"SUP-{sku}", "sku": sku, "name": f"Name {sku}",
        "category": "Cat", "brand": "Brand", "vehicle_fitment": "Fit",
        "description": "Desc", "price": price, "supplier_stock": 999,
        "updated_at": updated,
    }
    base.update(over)
    return base


class SupplierBase(TransactionTestCase):
    def serve(self, feed, **kwargs):
        self.mock = mock_supplier.SupplierMock(feed, KEY, **kwargs)
        httpd = mock_supplier.serve(self.mock, port=0)
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        override = override_settings(SUPPLIER_API_URL=url, SUPPLIER_API_KEY=KEY)
        override.enable()
        self.addCleanup(override.disable)
        # keep backoff waits short; Retry-After waits stay real
        patcher = patch.object(supplier_sync, "BACKOFF_BASE", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self):
        return APIClient().post("/api/sync/supplier/")

    def snapshot(self):
        return list(Product.objects.order_by("sku").values_list(
            "sku", "name", "price", "stock", "description"))


class SyncRulesTests(SupplierBase):
    def test_create_update_unchanged_and_stock_untouched(self):
        Product.objects.create(
            sku="OLD-1", name="Name OLD-1", category="Cat", brand="Brand",
            vehicle_fitment="Fit", description="Desc", price="100.00", stock=42)
        Product.objects.create(
            sku="OLD-2", name="Old name", category="Cat", brand="Brand",
            vehicle_fitment="Fit", description="Desc", price="50.00", stock=7)
        self.serve([item("OLD-1"), item("OLD-2", price="55.50"), item("NEW-1")],
                   )
        body = self.sync().json()
        self.assertEqual((body["created"], body["updated"], body["unchanged"]), (1, 1, 1))
        self.assertEqual(body["rejected"], [])
        self.assertEqual(Product.objects.get(sku="OLD-1").stock, 42)
        old2 = Product.objects.get(sku="OLD-2")
        self.assertEqual((old2.stock, str(old2.price), old2.name), (7, "55.50", "Name OLD-2"))
        new = Product.objects.get(sku="NEW-1")
        self.assertEqual(new.stock, 0)  # never takes supplier_stock

    def test_second_run_changes_nothing(self):
        self.serve([item(f"S-{i}") for i in range(45)])
        first = self.sync().json()
        self.assertEqual(first["created"], 45)
        before = self.snapshot()
        second = self.sync().json()
        self.assertEqual((second["created"], second["updated"], second["unchanged"]), (0, 0, 45))
        self.assertEqual(self.snapshot(), before)

    def test_pagination_fetches_every_page(self):
        self.serve([item(f"P-{i:03d}") for i in range(47)])
        body = self.sync().json()
        self.assertEqual(body["pages_fetched"], 3)
        self.assertEqual(body["requests_made"], 3)
        self.assertEqual(Product.objects.count(), 47)

    def test_empty_feed(self):
        self.serve([])
        body = self.sync().json()
        self.assertEqual((body["created"], body["updated"], body["unchanged"]), (0, 0, 0))

    def test_invalid_items_are_rejected_not_fatal(self):
        feed = [
            item("OK-1"),
            item("", supplier_sku="SUP-A"),
            item("  ", supplier_sku="SUP-B"),
            {"supplier_sku": "SUP-C", "name": "no sku"},
            item("BAD-P1", price="N/A", supplier_sku="SUP-D"),
            item("BAD-P2", price="12.345", supplier_sku="SUP-E"),
            item("BAD-P3", price="0", supplier_sku="SUP-F"),
            item("BAD-P4", price="-5.00", supplier_sku="SUP-G"),
            item("BAD-P5", price=None, supplier_sku="SUP-H"),
            item("BAD-P6", price="NaN", supplier_sku="SUP-I"),
            item("BAD-P7", price="1e3", supplier_sku="SUP-J") | {"price": "1e400"},
            item("BAD-N", name="x" * 300, supplier_sku="SUP-K"),
            "not an object",
        ]
        self.serve(feed)
        body = self.sync().json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(len(body["rejected"]), 12)
        self.assertEqual(
            {r["supplier_sku"] for r in body["rejected"]} - {None},
            {f"SUP-{c}" for c in "ABCDEFGHIJK"},
        )
        self.assertEqual(Product.objects.filter(sku__startswith="BAD").count(), 0)
        self.assertTrue(all(r["reason"] for r in body["rejected"]))

    def test_whole_numbers_and_one_decimal_prices_are_valid(self):
        self.serve([item("N-1", price="150"), item("N-2", price="99.5"),
                    item("N-3", price=75), item("N-4", price=12.5)])
        body = self.sync().json()
        self.assertEqual(body["created"], 4)
        self.assertEqual(str(Product.objects.get(sku="N-2").price), "99.50")

    def test_duplicates_use_latest_updated_at(self):
        feed = [
            item("D-1", price="10.00", updated="2026-10-05T00:00:00Z"),
            item("D-1", price="30.00", updated="2026-10-07T00:00:00Z"),
            item("D-1", price="20.00", updated="2026-10-06T00:00:00Z"),
            item("D-2", price="5.00", updated="2026-10-02T00:00:00Z"),
            item("D-2", price="9.00", updated="2026-10-01T00:00:00Z"),
        ]
        self.serve(feed)
        body = self.sync().json()
        self.assertEqual(body["created"], 2)  # unique SKUs, not rows
        self.assertEqual(str(Product.objects.get(sku="D-1").price), "30.00")
        self.assertEqual(str(Product.objects.get(sku="D-2").price), "5.00")

    def test_invalid_newer_duplicate_does_not_win(self):
        feed = [item("E-1", price="10.00", updated="2026-10-01T00:00:00Z"),
                item("E-1", price="bad", updated="2026-10-09T00:00:00Z")]
        self.serve(feed)
        body = self.sync().json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(len(body["rejected"]), 1)
        self.assertEqual(str(Product.objects.get(sku="E-1").price), "10.00")

    def test_counts_add_up(self):
        Product.objects.create(sku="X-1", name="Name X-1", category="Cat", brand="Brand",
                               vehicle_fitment="Fit", description="Desc", price="1.00")
        feed = [item("X-1", price="2.00"), item("X-1", price="3.00", updated="2027-01-01T00:00:00Z"),
                item("X-2"), item("X-3"), item("", supplier_sku="Z")]
        self.serve(feed)
        body = self.sync().json()
        self.assertEqual(body["created"] + body["updated"] + body["unchanged"], 3)

    def test_each_text_field_change_counts_as_update(self):
        for field, value in [("name", "Changed"), ("category", "Other"), ("brand", "Other"),
                             ("vehicle_fitment", "Other"), ("description", "Other")]:
            with self.subTest(field=field):
                Product.objects.all().delete()
                Product.objects.create(sku="U-1", name="Name U-1", category="Cat", brand="Brand",
                                       vehicle_fitment="Fit", description="Desc", price="100.00")
                self.serve([item("U-1", **{field: value})])
                body = self.sync().json()
                self.assertEqual((body["updated"], body["unchanged"]), (1, 0))
                self.assertEqual(getattr(Product.objects.get(sku="U-1"), field), value)

    def test_missing_optional_text_fields_are_tolerated(self):
        self.serve([{"sku": "M-1", "price": "10.00"}])
        body = self.sync().json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(self.sync().json()["unchanged"], 1)


class SyncFailureTests(SupplierBase):
    def feed(self, n=60):
        return [item(f"F-{i:03d}") for i in range(n)]

    def test_rate_limits_are_retried(self):
        self.serve(self.feed(), throttle_every=2, retry_after=1)
        started = time.monotonic()
        response = self.sync()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pages_fetched"], 3)
        self.assertGreater(body["requests_made"], 3)
        self.assertEqual(Product.objects.count(), 60)
        self.assertGreaterEqual(time.monotonic() - started, 1.0)  # honoured Retry-After

    def test_transient_5xx_is_retried(self):
        self.serve(self.feed(), transient={1: [500, 502, 503]})
        body = self.sync().json()
        self.assertEqual(body["pages_fetched"], 3)
        self.assertEqual(body["requests_made"], 6)
        self.assertEqual(Product.objects.count(), 60)

    def test_exactly_five_attempts_then_502_and_nothing_changes(self):
        Product.objects.create(sku="KEEP", name="Keep", category="c", brand="b",
                               vehicle_fitment="v", price="1.00", stock=3)
        before = self.snapshot()
        self.serve(self.feed(), outage_pages=[1])
        response = self.sync()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "SUPPLIER_UNAVAILABLE")
        self.assertEqual(self.snapshot(), before)
        failed_page = [e for e in self.mock.log if e["page"] == 1 and e["status"] == 503]
        self.assertEqual(len(failed_page), 5)  # at most 5 attempts

    def test_four_failures_then_success_is_fine(self):
        self.serve(self.feed(25), transient={1: [500, 500, 500, 500]})
        self.assertEqual(self.sync().status_code, 200)

    def test_five_failures_is_too_many(self):
        self.serve(self.feed(25), transient={1: [500] * 5})
        self.assertEqual(self.sync().status_code, 502)
        self.assertEqual(Product.objects.count(), 0)

    def test_wrong_key_returns_502_without_retrying(self):
        self.serve(self.feed(5))
        with override_settings(SUPPLIER_API_KEY="wrong"):
            response = self.sync()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "SUPPLIER_UNAVAILABLE")
        self.assertEqual(len(self.mock.log), 1)
        self.assertEqual(Product.objects.count(), 0)

    def test_unreachable_supplier_returns_502(self):
        with override_settings(SUPPLIER_API_URL="http://127.0.0.1:1", SUPPLIER_API_KEY=KEY):
            with patch.object(supplier_sync, "BACKOFF_BASE", 0.01):
                response = self.sync()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(Product.objects.count(), 0)

    def test_missing_url_returns_502(self):
        with override_settings(SUPPLIER_API_URL=""):
            self.assertEqual(self.sync().status_code, 502)

    def test_sync_works_again_after_an_outage(self):
        self.serve(self.feed(25), transient={1: [500] * 5})
        self.assertEqual(self.sync().status_code, 502)
        self.assertEqual(self.sync().status_code, 200)
        self.assertEqual(Product.objects.count(), 25)


class FetchEdgeTests(TestCase):
    """Malformed supplier replies, with HTTP mocked."""

    def fake_session(self, responses):
        calls = iter(responses)

        class Resp:
            def __init__(self, status, payload=None, headers=None, bad_json=False):
                self.status_code, self.headers = status, headers or {}
                self._payload, self._bad = payload, bad_json

            def json(self):
                if self._bad:
                    raise ValueError("bad json")
                return self._payload

        class Session:
            headers = {}

            def get(self, *a, **k):
                r = next(calls)
                if isinstance(r, Exception):
                    raise r
                return Resp(*r) if isinstance(r, tuple) else r

        return Session()

    def run_fetch(self, responses):
        with override_settings(SUPPLIER_API_URL="http://x", SUPPLIER_API_KEY="k"), \
             patch.object(supplier_sync.requests, "Session",
                          return_value=self.fake_session(responses)), \
             patch.object(supplier_sync.time, "sleep"):
            return supplier_sync.fetch_feed()

    def test_malformed_json_is_retried(self):
        items, pages, made = self.run_fetch([
            (200, None, None, True),
            (200, {"items": [item("A")], "next_cursor": None}),
        ])
        self.assertEqual((len(items), pages, made), (1, 1, 2))

    def test_missing_items_key_is_retried_then_fails(self):
        with self.assertRaises(supplier_sync.SupplierUnavailable):
            self.run_fetch([(200, {"oops": 1})] * 5)

    def test_cursor_loop_is_detected(self):
        page = (200, {"items": [], "next_cursor": "same"})
        with self.assertRaises(supplier_sync.SupplierUnavailable):
            self.run_fetch([page] * 10)

    def test_retry_after_is_honoured_and_capped(self):
        sleeps = []
        responses = [(429, None, {"Retry-After": "7"}),
                     (429, None, {"Retry-After": "9999"}),
                     (429, None, {"Retry-After": "soon"}),
                     (200, {"items": [], "next_cursor": None})]
        with override_settings(SUPPLIER_API_URL="http://x", SUPPLIER_API_KEY="k"), \
             patch.object(supplier_sync.requests, "Session",
                          return_value=self.fake_session(responses)), \
             patch.object(supplier_sync.time, "sleep", side_effect=sleeps.append):
            supplier_sync.fetch_feed()
        self.assertEqual(sleeps, [7.0, float(supplier_sync.MAX_RETRY_AFTER), 1.0])

    def test_connection_errors_are_retried(self):
        error = supplier_sync.requests.ConnectionError("boom")
        items, _, made = self.run_fetch([
            error, error, (200, {"items": [item("A")], "next_cursor": None})])
        self.assertEqual((len(items), made), (1, 3))


class ParsingTests(TestCase):
    def test_price_parsing(self):
        parse = supplier_sync._parse_price
        for good, expected in [("1459.50", "1459.50"), ("10", "10.00"), (7, "7.00"),
                               (12.5, "12.50"), (" 3.1 ", "3.10"), ("0.01", "0.01")]:
            self.assertEqual(str(parse(good)), expected)
        for bad in ["", "abc", "0", "0.00", "-1", "1.001", "NaN", "Infinity", None, True,
                    [], {}, "1e400", "99999999999.00"]:
            self.assertIsNone(parse(bad), bad)

    def test_timestamps_with_and_without_zone(self):
        t = supplier_sync._parse_time
        self.assertGreater(t("2026-10-08T09:15:00Z"), t("2026-10-08T09:14:59Z"))
        self.assertGreater(t("2026-10-08T09:15:00+00:00"), t("2026-10-08T09:00:00"))
        self.assertLess(t("garbage"), t("2000-01-01T00:00:00Z"))
        self.assertLess(t(None), t("2000-01-01T00:00:00Z"))
