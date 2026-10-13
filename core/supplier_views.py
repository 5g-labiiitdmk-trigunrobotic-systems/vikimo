from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.supplier_sync import SupplierUnavailable, run_sync
from core.views import error_response


class SupplierSyncView(APIView):
    """POST /api/sync/supplier/"""

    def post(self, request):
        try:
            result = run_sync()
        except SupplierUnavailable as exc:
            return error_response(
                "SUPPLIER_UNAVAILABLE",
                "The supplier catalogue could not be fetched in full. "
                "Nothing was changed.",
                status.HTTP_502_BAD_GATEWAY,
                str(exc),
            )
        return Response(result, status=status.HTTP_200_OK)
