from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AbrirCajaView, EstadoCajaView, MovimientoCajaViewSet, SesionCajaViewSet

router = DefaultRouter()
router.register(r'sesiones', SesionCajaViewSet, basename='sesion-caja')
router.register(r'movimientos', MovimientoCajaViewSet, basename='movimiento-caja')

urlpatterns = [
    path('estado/', EstadoCajaView.as_view(), name='caja-estado'),
    path('abrir/', AbrirCajaView.as_view(), name='caja-abrir'),
    path('', include(router.urls)),
]
