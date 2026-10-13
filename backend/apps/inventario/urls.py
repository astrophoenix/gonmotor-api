from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    RepuestoExcelExportView,
    RepuestoImagenViewSet,
    RepuestoPdfExportView,
    RepuestoViewSet,
    ServicioExcelExportView,
    ServicioPdfExportView,
    ServicioViewSet,
)

router_repuestos = DefaultRouter()
router_repuestos.register(r'', RepuestoViewSet, basename='repuesto')

router_repuesto_imagenes = DefaultRouter()
router_repuesto_imagenes.register(r'', RepuestoImagenViewSet, basename='repuesto-imagen')

router_servicios = DefaultRouter()
router_servicios.register(r'', ServicioViewSet, basename='servicio')

urlpatterns = [
    path('repuestos/exportar-pdf/', RepuestoPdfExportView.as_view(), name='repuesto-exportar-pdf'),
    path('repuestos/export-excel/', RepuestoExcelExportView.as_view(), name='repuesto-export-excel'),
    path('repuestos/', include(router_repuestos.urls)),
    path('repuestos-imagenes/', include(router_repuesto_imagenes.urls)),
    path('servicios/exportar-pdf/', ServicioPdfExportView.as_view(), name='servicio-exportar-pdf'),
    path('servicios/export-excel/', ServicioExcelExportView.as_view(), name='servicio-export-excel'),
    path('servicios/', include(router_servicios.urls)),
]