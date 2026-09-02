from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import InspeccionVehiculoViewSet, OrdenTrabajoViewSet

router = DefaultRouter()
router.register(r'ordenes-trabajo', OrdenTrabajoViewSet, basename='orden-trabajo')
router.register(r'inspecciones', InspeccionVehiculoViewSet, basename='inspeccion-vehiculo')

urlpatterns = [
    path('', include(router.urls)),
]
