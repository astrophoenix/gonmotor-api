from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClienteViewSet, ClientePdfExportView

router = DefaultRouter()
router.register(r'', ClienteViewSet, basename='cliente')

urlpatterns = [
    path('exportar-pdf/', ClientePdfExportView.as_view(), name='cliente-exportar-pdf'),
    path('', include(router.urls)),
]