from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VehiculoViewSet, VehiculoPdfExportView

router = DefaultRouter()
router.register(r'', VehiculoViewSet, basename='vehiculo')

urlpatterns = [
    path('exportar-pdf/', VehiculoPdfExportView.as_view(), name='vehiculo-exportar-pdf'),
    path('', include(router.urls)),
]