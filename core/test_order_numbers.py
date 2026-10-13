import os
import re
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.models import Dealer, Order, OrderDayCounter, Product

NUMBER_RE = re.compile(r"^ORD-\d{8}-\d{4}$")


class OrderNumberTests(TransactionTestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(dealer_code="DLR-T", name="Test")
        Product.objects.create(
            sku="T-1", name="Part", category="C", brand="B",
            vehicle_fitment="V", price="10.00", stock=5,
        )
        self.client = APIClient()

    def _create(self):
        return self.client.post(
            "/api/orders/",
            {"dealer_id": self.dealer.id, "items": [{"sku": "T-1", "quantity": 1}]},
            format="json",
        )

    def test_format_and_daily_sequence(self):
        today = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d")
        numbers = [self._create().json()["order_number"] for _ in range(3)]
        self.assertEqual(
            numbers,
            [f"ORD-{today}-0001", f"ORD-{today}-0002", f"ORD-{today}-0003"],
        )

    def test_sequence_restarts_on_new_day(self):
        from django.utils import timezone

        day1 = datetime(2026, 10, 12, 23, 59, tzinfo=ZoneInfo("Asia/Kolkata"))
        day2 = datetime(2026, 10, 13, 0, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch.object(timezone, "now", return_value=day1):
            first = self._create().json()["order_number"]
            second = self._create().json()["order_number"]
        with patch.object(timezone, "now", return_value=day2):
            third = self._create().json()["order_number"]
        self.assertEqual(first, "ORD-20261012-0001")
        self.assertEqual(second, "ORD-20261012-0002")
        self.assertEqual(third, "ORD-20261013-0001")

    def test_utc_evening_counts_as_next_ist_day(self):
        from django.utils import timezone

        # 19:00 UTC on 12 Oct is 00:30 IST on 13 Oct.
        moment = datetime(2026, 10, 12, 19, 0, tzinfo=ZoneInfo("UTC"))
        with patch.object(timezone, "now", return_value=moment):
            number = self._create().json()["order_number"]
        self.assertEqual(number, "ORD-20261013-0001")

    def test_concurrent_creation_gives_unique_numbers(self):
        results, errors = [], []
        barrier = threading.Barrier(12)

        def worker():
            try:
                client = APIClient()
                barrier.wait()
                response = client.post(
                    "/api/orders/",
                    {"dealer_id": self.dealer.id,
                     "items": [{"sku": "T-1", "quantity": 1}]},
                    format="json",
                )
                results.append((response.status_code,
                                response.json().get("order_number")))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(all(code == 201 for code, _ in results), results)
        numbers = [n for _, n in results]
        self.assertEqual(len(set(numbers)), 12)
        self.assertTrue(all(NUMBER_RE.match(n) for n in numbers))
        self.assertEqual(Order.objects.count(), 12)
        self.assertEqual(OrderDayCounter.objects.get().last_number, 12)

    def test_assistant_draft_orders_use_same_numbering(self):
        from core.assistant_tools import create_draft_order

        result = create_draft_order(
            self.dealer.id, [{"sku": "T-1", "quantity": 2}]
        )
        self.assertRegex(result["order_number"], NUMBER_RE)
        second = self._create().json()["order_number"]
        self.assertNotEqual(result["order_number"], second)


class AssistantModelDefaultTests(TransactionTestCase):
    @patch.dict(os.environ, {"GROQ_API_KEY": "k"}, clear=False)
    @patch("core.assistant_service.Groq")
    def test_missing_groq_model_uses_default(self, mock_groq):
        from types import SimpleNamespace
        from core.assistant_service import DEFAULT_GROQ_MODEL, run_assistant

        os.environ.pop("GROQ_MODEL", None)
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=None, content="Hello, how can I help?"))]
        )
        mock_groq.return_value = client
        dealer = Dealer.objects.create(dealer_code="DLR-M", name="M")
        run_assistant(message="hi", dealer_id=dealer.id)
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            DEFAULT_GROQ_MODEL,
        )
