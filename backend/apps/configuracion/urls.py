from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EmpresaConfigViewSet

router = DefaultRouter()
router.register('empresa', EmpresaConfigViewSet, basename='empresa-config')

urlpatterns = [
    path('', include(router.urls)),
]