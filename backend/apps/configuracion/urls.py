from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EmpresaConfigViewSet, TallerConfigViewSet

router = DefaultRouter()
router.register('empresa', EmpresaConfigViewSet, basename='empresa-config')
router.register('sucursales', TallerConfigViewSet, basename='sucursal-config')

urlpatterns = [
    path('', include(router.urls)),
]