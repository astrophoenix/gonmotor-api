from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CompraViewSet, PagoCompraViewSet

router_pagos = DefaultRouter()
router_pagos.register(r'', PagoCompraViewSet, basename='pago-compra')

router = DefaultRouter()
router.register(r'', CompraViewSet, basename='compra')

urlpatterns = [
    # Antes del router de compras: `pagos` no es el id de una compra.
    path('pagos/', include(router_pagos.urls)),
    path('', include(router.urls)),
]
