from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClienteViewSet, ClientePdfExportView, ClienteExcelExportView

router = DefaultRouter()
router.register(r'', ClienteViewSet, basename='cliente')

urlpatterns = [
    path('exportar-pdf/', ClientePdfExportView.as_view(), name='cliente-exportar-pdf'),
    path('export-excel/', ClienteExcelExportView.as_view(), name='cliente-export-excel'),
    path('', include(router.urls)),
]