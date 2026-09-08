from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CitaViewSet

router = DefaultRouter()
router.register(r'', CitaViewSet, basename='cita')

urlpatterns = [
    path('', include(router.urls)),
]