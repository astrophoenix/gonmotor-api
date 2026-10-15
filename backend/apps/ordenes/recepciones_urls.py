from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import RecepcionVehiculoViewSet

router = DefaultRouter()
router.register(r'', RecepcionVehiculoViewSet, basename='recepciones')

urlpatterns = [
    path('', include(router.urls)),
]
