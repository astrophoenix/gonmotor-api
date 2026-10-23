from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CuentasPorCobrarExcelView, CuentasPorCobrarView, PagoOrdenViewSet, RecordarSaldoView

router = DefaultRouter()
router.register(r'', PagoOrdenViewSet, basename='pago')

urlpatterns = [
    # Antes del router: `cuentas-por-cobrar` no es el id de un pago.
    path('cuentas-por-cobrar/', CuentasPorCobrarView.as_view(), name='cuentas-por-cobrar'),
    path(
        'cuentas-por-cobrar/export-excel/', CuentasPorCobrarExcelView.as_view(), name='cuentas-por-cobrar-excel',
    ),
    path('cuentas-por-cobrar/<str:orden_id>/recordar/', RecordarSaldoView.as_view(), name='recordar-saldo'),
    path('', include(router.urls)),
]
