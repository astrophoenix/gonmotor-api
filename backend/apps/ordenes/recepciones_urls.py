from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import RecepcionVehiculoViewSet, RecepcionPdfExportView, RecepcionExcelExportView

router = DefaultRouter()
router.register(r'', RecepcionVehiculoViewSet, basename='recepciones')

urlpatterns = [
    path('exportar-pdf/', RecepcionPdfExportView.as_view(), name='recepcion-exportar-pdf'),
    path('export-excel/', RecepcionExcelExportView.as_view(), name='recepcion-export-excel'),
    path('', include(router.urls)),
]
