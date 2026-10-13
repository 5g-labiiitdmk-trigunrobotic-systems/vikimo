import threading
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.models import Dealer, Order, Product


class EngineBase(TransactionTestCase):
    def setUp(self):
        self.api = APIClient()
        self.dealer = Dealer.objects.create(dealer_code="DLR-X", name="X")
        self.a = Product.objects.create(
            sku="A-1", name="Alpha Pad", category="C", brand="B",
            vehicle_fitment="V", price="1459.50", stock=10,
        )
        self.b = Product.objects.create(
            sku="B-1", name="Beta Filter", category="C", brand="B",
            vehicle_fitment="V", price="99.99", stock=3,
        )

    def draft(self, items=None, **headers):
        items = items or [{"sku": "A-1", "quantity": 2}]
        return self.api.post(
            "/api/orders/", {"dealer_id": self.dealer.id, "items": items},
            format="json", **headers,
        )

    def act(self, number, action):
        return self.api.post(f"/api/orders/{number}/{action}/")

    def stock(self, sku):
        return Product.objects.get(sku=sku).stock

    def err(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)
        return response.json()["error"]


class StatusFlowTests(EngineBase):
    def test_full_happy_path(self):
        n = self.draft().json()["order_number"]
        self.assertEqual(self.stock("A-1"), 10)  # draft leaves stock alone
        for action, expected in [("confirm", "CONFIRMED"),
                                 ("dispatch", "DISPATCHED"),
                                 ("deliver", "DELIVERED")]:
            r = self.act(n, action)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], expected)
        self.assertEqual(self.stock("A-1"), 8)

    def test_invalid_transitions(self):
        n = self.draft().json()["order_number"]
        for action in ("dispatch", "deliver"):
            self.err(self.act(n, action), 409, "INVALID_TRANSITION")
        self.act(n, "confirm")
        for action in ("confirm", "deliver"):
            self.err(self.act(n, action), 409, "INVALID_TRANSITION")
        self.act(n, "dispatch")
        for action in ("confirm", "dispatch", "cancel"):
            self.err(self.act(n, action), 409, "INVALID_TRANSITION")
        self.act(n, "deliver")
        for action in ("confirm", "dispatch", "deliver", "cancel"):
            self.err(self.act(n, action), 409, "INVALID_TRANSITION")

    def test_cancelled_is_final(self):
        n = self.draft().json()["order_number"]
        self.assertEqual(self.act(n, "cancel").json()["status"], "CANCELLED")
        for action in ("confirm", "dispatch", "deliver", "cancel"):
            self.err(self.act(n, action), 409, "INVALID_TRANSITION")

    def test_unknown_order_is_404(self):
        for action in ("confirm", "dispatch", "deliver", "cancel"):
            self.err(self.act("ORD-19990101-0001", action), 404, "NOT_FOUND")
        r = self.api.put("/api/orders/ORD-19990101-0001/",
                         {"items": [{"sku": "A-1", "quantity": 1}]}, format="json")
        self.err(r, 404, "NOT_FOUND")


class StockTests(EngineBase):
    def test_confirm_all_or_nothing_and_lists_every_short_line(self):
        n = self.draft([{"sku": "A-1", "quantity": 4},
                        {"sku": "B-1", "quantity": 5}]).json()["order_number"]
        self.api.put("/api/inventory/A-1/", {"quantity": 1}, format="json")
        error = self.err(self.act(n, "confirm"), 409, "INSUFFICIENT_STOCK")
        self.assertEqual(
            sorted(error["details"], key=lambda d: d["sku"]),
            [{"sku": "A-1", "requested": 4, "available": 1},
             {"sku": "B-1", "requested": 5, "available": 3}],
        )
        self.assertEqual(
            error["message"],
            "Insufficient stock for Alpha Pad. Available: 1, Requested: 4 "
            "Insufficient stock for Beta Filter. Available: 3, Requested: 5",
        )
        self.assertEqual(self.stock("A-1"), 1)
        self.assertEqual(self.stock("B-1"), 3)
        self.assertEqual(Order.objects.get(order_number=n).status, "DRAFT")

    def test_exact_stock_can_be_confirmed(self):
        n = self.draft([{"sku": "B-1", "quantity": 3}]).json()["order_number"]
        self.assertEqual(self.act(n, "confirm").status_code, 200)
        self.assertEqual(self.stock("B-1"), 0)

    def test_cancel_restores_stock_exactly_once(self):
        n = self.draft().json()["order_number"]
        self.act(n, "confirm")
        self.assertEqual(self.stock("A-1"), 8)
        self.assertEqual(self.act(n, "cancel").status_code, 200)
        self.assertEqual(self.stock("A-1"), 10)
        self.err(self.act(n, "cancel"), 409, "INVALID_TRANSITION")
        self.assertEqual(self.stock("A-1"), 10)

    def test_cancel_draft_does_not_touch_stock(self):
        n = self.draft().json()["order_number"]
        self.act(n, "cancel")
        self.assertEqual(self.stock("A-1"), 10)

    def test_inventory_update_does_not_change_existing_orders(self):
        n = self.draft().json()["order_number"]
        self.act(n, "confirm")
        self.api.put("/api/inventory/A-1/", {"quantity": 500}, format="json")
        order = self.api.get(f"/api/orders/{n}/").json()
        self.assertEqual(order["items"][0]["quantity"], 2)
        self.assertEqual(order["status"], "CONFIRMED")

    def _race(self, count, target):
        results, barrier = [], threading.Barrier(count)

        def worker(number):
            try:
                barrier.wait()
                results.append(APIClient().post(f"/api/orders/{number}/{target}/"))
            finally:
                connection.close()

        return worker, results

    def test_concurrent_confirms_never_oversell(self):
        self.api.put("/api/inventory/B-1/", {"quantity": 5}, format="json")
        numbers = [
            self.draft([{"sku": "B-1", "quantity": 2}]).json()["order_number"]
            for _ in range(6)
        ]
        results, barrier = [], threading.Barrier(len(numbers))

        def worker(number):
            try:
                barrier.wait()
                results.append(APIClient().post(f"/api/orders/{number}/confirm/"))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(n,)) for n in numbers]
        [t.start() for t in threads]
        [t.join() for t in threads]

        codes = sorted(r.status_code for r in results)
        self.assertEqual(codes, [200, 200, 409, 409, 409, 409])
        self.assertTrue(all(
            r.json()["error"]["code"] == "INSUFFICIENT_STOCK"
            for r in results if r.status_code == 409))
        self.assertEqual(self.stock("B-1"), 1)

    def test_concurrent_cancels_restore_once(self):
        n = self.draft().json()["order_number"]
        self.act(n, "confirm")
        results, barrier = [], threading.Barrier(5)

        def worker():
            try:
                barrier.wait()
                results.append(APIClient().post(f"/api/orders/{n}/cancel/"))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(r.status_code for r in results),
                         [200, 409, 409, 409, 409])
        self.assertEqual(self.stock("A-1"), 10)

    def test_concurrent_multi_line_confirms_do_not_deadlock(self):
        self.api.put("/api/inventory/A-1/", {"quantity": 100}, format="json")
        self.api.put("/api/inventory/B-1/", {"quantity": 100}, format="json")
        orders = []
        for i in range(8):
            items = [{"sku": "A-1", "quantity": 1}, {"sku": "B-1", "quantity": 1}]
            orders.append(self.draft(items if i % 2 else items[::-1]).json()["order_number"])
        results, barrier = [], threading.Barrier(len(orders))

        def worker(number):
            try:
                barrier.wait()
                results.append(APIClient().post(f"/api/orders/{number}/confirm/").status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(n,)) for n in orders]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [200] * 8)
        self.assertEqual(self.stock("A-1"), 92)
        self.assertEqual(self.stock("B-1"), 92)


class PricingTests(EngineBase):
    def test_gst_per_line_round_half_up(self):
        # 0.5 * 18% would be 0.09; 0.25 -> line_tax 0.045 -> 0.05 (half up)
        p = Product.objects.create(
            sku="C-1", name="Cheap", category="C", brand="B",
            vehicle_fitment="V", price="0.25", stock=10)
        order = self.draft([{"sku": "C-1", "quantity": 1},
                            {"sku": "A-1", "quantity": 3}]).json()
        by_sku = {i["sku"]: i for i in order["items"]}
        self.assertEqual(by_sku["C-1"]["line_tax"], "0.05")
        self.assertEqual(by_sku["A-1"]["line_subtotal"], "4378.50")
        self.assertEqual(by_sku["A-1"]["line_tax"], "788.13")
        self.assertEqual(by_sku["A-1"]["line_total"], "5166.63")
        self.assertEqual(order["subtotal"], "4378.75")
        self.assertEqual(order["tax_total"], "788.18")
        self.assertEqual(order["grand_total"], "5166.93")

    def test_draft_follows_price_confirmed_is_locked(self):
        n = self.draft().json()["order_number"]
        self.api.patch("/api/products/A-1/", {"price": "2000.00"}, format="json")
        order = self.api.get(f"/api/orders/{n}/").json()
        self.assertEqual(order["items"][0]["unit_price"], "2000.00")
        self.assertEqual(order["subtotal"], "4000.00")
        listed = self.api.get("/api/orders/").json()["results"][0]
        self.assertEqual(listed["items"][0]["unit_price"], "2000.00")

        self.api.patch("/api/products/A-1/", {"price": "2500.00"}, format="json")
        confirmed = self.act(n, "confirm").json()
        self.assertEqual(confirmed["items"][0]["unit_price"], "2500.00")
        self.assertEqual(confirmed["grand_total"], "5900.00")

        self.api.patch("/api/products/A-1/", {"price": "1.00"}, format="json")
        for _ in range(2):
            order = self.api.get(f"/api/orders/{n}/").json()
            self.assertEqual(order["items"][0]["unit_price"], "2500.00")
            self.assertEqual(order["grand_total"], "5900.00")


class EditTests(EngineBase):
    def test_put_replaces_items(self):
        n = self.draft().json()["order_number"]
        r = self.api.put(f"/api/orders/{n}/",
                         {"items": [{"sku": "B-1", "quantity": 1}]}, format="json")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([i["sku"] for i in body["items"]], ["B-1"])
        self.assertEqual(body["subtotal"], "99.99")
        self.assertEqual(body["tax_total"], "18.00")
        self.assertEqual(body["grand_total"], "117.99")

    def test_put_validation(self):
        n = self.draft().json()["order_number"]
        for items in ([], [{"sku": "NOPE", "quantity": 1}],
                      [{"sku": "A-1", "quantity": 0}],
                      [{"sku": "A-1", "quantity": 1}, {"sku": "A-1", "quantity": 2}]):
            r = self.api.put(f"/api/orders/{n}/", {"items": items}, format="json")
            self.err(r, 400, "VALIDATION_ERROR")
        self.assertEqual(
            self.api.get(f"/api/orders/{n}/").json()["items"][0]["quantity"], 2)

    def test_put_non_draft_is_locked(self):
        n = self.draft().json()["order_number"]
        self.act(n, "confirm")
        r = self.api.put(f"/api/orders/{n}/",
                         {"items": [{"sku": "B-1", "quantity": 1}]}, format="json")
        self.err(r, 409, "ORDER_LOCKED")
        self.act(n, "cancel")
        r = self.api.put(f"/api/orders/{n}/",
                         {"items": [{"sku": "B-1", "quantity": 1}]}, format="json")
        self.err(r, 409, "ORDER_LOCKED")


class IdempotencyTests(EngineBase):
    def test_same_key_same_body_returns_original(self):
        first = self.draft(HTTP_IDEMPOTENCY_KEY="k1")
        second = self.draft(HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["order_number"], second.json()["order_number"])
        self.assertEqual(Order.objects.count(), 1)

    def test_same_key_different_body_conflicts(self):
        self.draft(HTTP_IDEMPOTENCY_KEY="k2")
        r = self.draft([{"sku": "B-1", "quantity": 1}], HTTP_IDEMPOTENCY_KEY="k2")
        self.err(r, 409, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(Order.objects.count(), 1)

    def test_different_keys_make_different_orders(self):
        a = self.draft(HTTP_IDEMPOTENCY_KEY="a").json()["order_number"]
        b = self.draft(HTTP_IDEMPOTENCY_KEY="b").json()["order_number"]
        self.assertNotEqual(a, b)

    def test_invalid_body_with_key_creates_nothing_and_key_stays_free(self):
        bad = self.api.post("/api/orders/", {"dealer_id": self.dealer.id, "items": []},
                            format="json", HTTP_IDEMPOTENCY_KEY="k3")
        self.err(bad, 400, "VALIDATION_ERROR")
        self.assertEqual(self.draft(HTTP_IDEMPOTENCY_KEY="k3").status_code, 201)

    def test_key_order_of_json_fields_does_not_matter(self):
        body1 = {"dealer_id": self.dealer.id, "items": [{"sku": "A-1", "quantity": 2}]}
        body2 = {"items": [{"quantity": 2, "sku": "A-1"}], "dealer_id": self.dealer.id}
        r1 = self.api.post("/api/orders/", body1, format="json", HTTP_IDEMPOTENCY_KEY="k4")
        r2 = self.api.post("/api/orders/", body2, format="json", HTTP_IDEMPOTENCY_KEY="k4")
        self.assertEqual(r1.json()["order_number"], r2.json()["order_number"])

    def test_simultaneous_duplicates_create_one_order(self):
        results, barrier = [], threading.Barrier(10)

        def worker():
            try:
                barrier.wait()
                results.append(self.__class__.draft(self, HTTP_IDEMPOTENCY_KEY="race"))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(r.status_code for r in results), [200] * 9 + [201])
        self.assertEqual(len({r.json()["order_number"] for r in results}), 1)
        self.assertEqual(Order.objects.count(), 1)
