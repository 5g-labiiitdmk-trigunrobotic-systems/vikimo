from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="dashboard"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    # Add your apps' URLs here, e.g. path("api/", include("orders.urls")),
]
