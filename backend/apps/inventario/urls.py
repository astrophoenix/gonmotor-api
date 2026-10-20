from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    RepuestoExcelExportView,
    RepuestoImagenViewSet,
    RepuestoImportErroresView,
    RepuestoImportTemplateView,
    RepuestoImportView,
    RepuestoPdfExportView,
    RepuestoViewSet,
    ServicioExcelExportView,
    ServicioImportErroresView,
    ServicioImportTemplateView,
    ServicioImportView,
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
    # Importación antes del router: si no, «importar» se tomaría como un id.
    path('repuestos/importar/', RepuestoImportView.as_view(), name='repuesto-importar'),
    path('repuestos/importar/errores/', RepuestoImportErroresView.as_view(), name='repuesto-importar-errores'),
    path('repuestos/importar/plantilla/', RepuestoImportTemplateView.as_view(), name='repuesto-importar-plantilla'),
    path('repuestos/', include(router_repuestos.urls)),
    path('repuestos-imagenes/', include(router_repuesto_imagenes.urls)),
    path('servicios/exportar-pdf/', ServicioPdfExportView.as_view(), name='servicio-exportar-pdf'),
    path('servicios/export-excel/', ServicioExcelExportView.as_view(), name='servicio-export-excel'),
    path('servicios/importar/', ServicioImportView.as_view(), name='servicio-importar'),
    path('servicios/importar/errores/', ServicioImportErroresView.as_view(), name='servicio-importar-errores'),
    path('servicios/importar/plantilla/', ServicioImportTemplateView.as_view(), name='servicio-importar-plantilla'),
    path('servicios/', include(router_servicios.urls)),
]