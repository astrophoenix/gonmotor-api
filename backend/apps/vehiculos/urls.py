from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VehiculoViewSet, VehiculoPdfExportView, VehiculoExcelExportView

router = DefaultRouter()
router.register(r'', VehiculoViewSet, basename='vehiculo')

urlpatterns = [
    path('exportar-pdf/', VehiculoPdfExportView.as_view(), name='vehiculo-exportar-pdf'),
    path('export-excel/', VehiculoExcelExportView.as_view(), name='vehiculo-export-excel'),
    path('', include(router.urls)),
]