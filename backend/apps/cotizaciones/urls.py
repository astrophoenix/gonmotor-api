from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CotizacionViewSet,
    DetalleRepuestoCotizacionViewSet,
    DetalleServicioCotizacionViewSet,
)

cotizaciones_router = DefaultRouter()
cotizaciones_router.register(r'', CotizacionViewSet, basename='cotizacion')

servicios_router = DefaultRouter()
servicios_router.register(r'', DetalleServicioCotizacionViewSet, basename='cotizacion-servicio')

repuestos_router = DefaultRouter()
repuestos_router.register(r'', DetalleRepuestoCotizacionViewSet, basename='cotizacion-repuesto')

urlpatterns = [
    path('servicios/', include(servicios_router.urls)),
    path('repuestos/', include(repuestos_router.urls)),
    path('', include(cotizaciones_router.urls)),
]