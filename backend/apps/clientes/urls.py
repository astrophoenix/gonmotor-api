from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ClienteViewSet,
    ClientePdfExportView,
    ClienteExcelExportView,
    ClienteImportView,
    ClienteImportErroresView,
    ClienteImportTemplateView,
)

router = DefaultRouter()
router.register(r'', ClienteViewSet, basename='cliente')

urlpatterns = [
    path('exportar-pdf/', ClientePdfExportView.as_view(), name='cliente-exportar-pdf'),
    path('export-excel/', ClienteExcelExportView.as_view(), name='cliente-export-excel'),
    path('importar/', ClienteImportView.as_view(), name='cliente-importar'),
    path('importar/errores/', ClienteImportErroresView.as_view(), name='cliente-importar-errores'),
    path('importar/plantilla/', ClienteImportTemplateView.as_view(), name='cliente-importar-plantilla'),
    path('', include(router.urls)),
]