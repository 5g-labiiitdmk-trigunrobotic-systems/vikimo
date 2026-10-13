from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Dealer, Product


class ErrorShapeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dealer = Dealer.objects.create(dealer_code="DLR-E", name="E")
        Product.objects.create(
            sku="E-1", name="Part", category="C", brand="B",
            vehicle_fitment="V", price="10.00", stock=5,
        )

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertIn("message", body["error"])
        self.assertIsInstance(body["error"]["details"], list)

    def test_malformed_json(self):
        for path in ("/api/orders/", "/api/products/", "/api/dealers/"):
            r = self.client.post(path, "{bad", content_type="application/json")
            self.assert_error(r, 400, "VALIDATION_ERROR")

    def test_non_object_bodies(self):
        r = self.client.post("/api/products/", [], format="json")
        self.assert_error(r, 400, "VALIDATION_ERROR")
        r = self.client.patch("/api/products/E-1/", [], format="json")
        self.assert_error(r, 400, "VALIDATION_ERROR")
        r = self.client.put("/api/inventory/E-1/", [], format="json")
        self.assert_error(r, 400, "VALIDATION_ERROR")

    def test_page_out_of_range_uses_error_shape(self):
        self.assert_error(self.client.get("/api/products/?page=99"), 404, "NOT_FOUND")

    def test_huge_quantities_are_rejected_not_500(self):
        r = self.client.put("/api/inventory/E-1/", {"quantity": 10**11}, format="json")
        self.assert_error(r, 400, "VALIDATION_ERROR")
        r = self.client.post(
            "/api/orders/",
            {"dealer_id": self.dealer.id, "items": [{"sku": "E-1", "quantity": 10**11}]},
            format="json",
        )
        self.assert_error(r, 400, "VALIDATION_ERROR")

    def test_inventory_max_value_accepted(self):
        r = self.client.put("/api/inventory/E-1/", {"quantity": 2_147_483_647}, format="json")
        self.assertEqual(r.status_code, 200)
