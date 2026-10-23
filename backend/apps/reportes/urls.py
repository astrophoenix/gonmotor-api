from django.urls import path

from .views import rentabilidad_empresa, rentabilidad_taller, resumen_negocio, resumen_negocio_pdf, productividad_mecanicos, resumen_negocio_excel

urlpatterns = [
    path('resumen/export-excel/', resumen_negocio_excel, name='resumen-negocio-excel'),
    path('productividad/', productividad_mecanicos, name='productividad-mecanicos'),
    path('rentabilidad-taller/', rentabilidad_taller, name='rentabilidad-taller'),
    path('rentabilidad-empresa/', rentabilidad_empresa, name='rentabilidad-empresa'),
    path('resumen/', resumen_negocio, name='resumen-negocio'),
    path('resumen/exportar-pdf/', resumen_negocio_pdf, name='resumen-negocio-pdf'),
]
