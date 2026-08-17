from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import OrdenTrabajoViewSet

router = DefaultRouter()
router.register(r'', OrdenTrabajoViewSet, basename='orden-trabajo')

urlpatterns = [
    path('', include(router.urls)),
]
