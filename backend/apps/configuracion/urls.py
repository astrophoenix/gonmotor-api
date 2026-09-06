from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EmpresaConfigViewSet,
    TallerConfigViewSet,
    TallerExcelExportView,
    TallerPdfExportView,
)

router = DefaultRouter()
router.register('empresa', EmpresaConfigViewSet, basename='empresa-config')
router.register('sucursales', TallerConfigViewSet, basename='sucursal-config')

urlpatterns = [
    path('sucursales/exportar-pdf/', TallerPdfExportView.as_view(), name='taller-exportar-pdf'),
    path('sucursales/export-excel/', TallerExcelExportView.as_view(), name='taller-export-excel'),
    path('', include(router.urls)),
]