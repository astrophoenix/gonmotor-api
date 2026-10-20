from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ProveedorViewSet,
    ProveedorPdfExportView,
    ProveedorExcelExportView,
    ProveedorImportErroresView,
    ProveedorImportTemplateView,
    ProveedorImportView,
)

router = DefaultRouter()
router.register(r'', ProveedorViewSet, basename='proveedor')

urlpatterns = [
    path('exportar-pdf/', ProveedorPdfExportView.as_view(), name='proveedor-exportar-pdf'),
    path('export-excel/', ProveedorExcelExportView.as_view(), name='proveedor-export-excel'),
    # Importación antes del router: si no, «importar» se tomaría como un id.
    path('importar/', ProveedorImportView.as_view(), name='proveedor-importar'),
    path('importar/errores/', ProveedorImportErroresView.as_view(), name='proveedor-importar-errores'),
    path('importar/plantilla/', ProveedorImportTemplateView.as_view(), name='proveedor-importar-plantilla'),
    path('', include(router.urls)),
]
