from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DetalleRepuestoInspeccionViewSet,
    DetalleRepuestoOrdenTrabajoViewSet,
    DetalleServicioInspeccionViewSet,
    DetalleServicioOrdenTrabajoViewSet,
    FotoInspeccionViewSet,
    FotoOrdenTrabajoViewSet,
    InspeccionVehiculoViewSet,
    OrdenTrabajoViewSet,
)

router = DefaultRouter()
router.register(r'ordenes-trabajo', OrdenTrabajoViewSet, basename='orden-trabajo')
router.register(r'inspecciones', InspeccionVehiculoViewSet, basename='inspeccion-vehiculo')
router.register(r'inspeccion-servicios', DetalleServicioInspeccionViewSet, basename='inspeccion-servicio')
router.register(r'inspeccion-repuestos', DetalleRepuestoInspeccionViewSet, basename='inspeccion-repuesto')
router.register(r'inspeccion-fotos', FotoInspeccionViewSet, basename='inspeccion-foto')
router.register(r'orden-fotos', FotoOrdenTrabajoViewSet, basename='orden-foto')
router.register(r'ot-servicios', DetalleServicioOrdenTrabajoViewSet, basename='ot-servicio')
router.register(r'ot-repuestos', DetalleRepuestoOrdenTrabajoViewSet, basename='ot-repuesto')

urlpatterns = [
    path('', include(router.urls)),
]
