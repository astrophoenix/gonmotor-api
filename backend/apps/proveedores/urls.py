from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ProveedorViewSet,
    ProveedorPdfExportView,
    ProveedorExcelExportView,
)

router = DefaultRouter()
router.register(r'', ProveedorViewSet, basename='proveedor')

urlpatterns = [
    path('exportar-pdf/', ProveedorPdfExportView.as_view(), name='proveedor-exportar-pdf'),
    path('export-excel/', ProveedorExcelExportView.as_view(), name='proveedor-export-excel'),
    path('', include(router.urls)),
]
