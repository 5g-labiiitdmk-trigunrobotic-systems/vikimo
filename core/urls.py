from django.urls import path

from core import views
from core.dealer_views import DealerDetailView, DealerListCreateView
from core.inventory_views import InventoryUpdateView
from core.order_views import (
    OrderCancelView,
    OrderConfirmView,
    OrderDeliverView,
    OrderDetailView,
    OrderDispatchView,
    OrderListCreateView,
)
from core.supplier_views import SupplierSyncView
from core.forecast_views import ForecastDetailView
from core.reorder_views import ReorderSuggestionsView
from core.assistant_views import AssistantChatView

urlpatterns = [
    path("health/", views.health),

    path("products/", views.ProductListCreateView.as_view()),
    path("products/<str:sku>/", views.ProductDetailView.as_view()),

    path("inventory/<str:sku>/", InventoryUpdateView.as_view()),

    path("dealers/", DealerListCreateView.as_view()),
    path("dealers/<int:pk>/", DealerDetailView.as_view()),

    path("orders/", OrderListCreateView.as_view()),
    path("orders/<str:order_number>/", OrderDetailView.as_view()),
    path("orders/<str:order_number>/confirm/", OrderConfirmView.as_view()),
    path("orders/<str:order_number>/dispatch/", OrderDispatchView.as_view()),
    path("orders/<str:order_number>/deliver/", OrderDeliverView.as_view()),
    path("orders/<str:order_number>/cancel/", OrderCancelView.as_view()),

    path("sync/supplier/", SupplierSyncView.as_view()),

    path("forecast/<str:sku>/", ForecastDetailView.as_view()),
    path("reorder-suggestions/", ReorderSuggestionsView.as_view()),

    path("assistant/chat/", AssistantChatView.as_view()),
]
