from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CitaViewSet, CompartirCitaIcs

router = DefaultRouter()
router.register(r'', CitaViewSet, basename='cita')

urlpatterns = [
    # El enlace firmado debe ir antes que el router: si no, "compartir" se
    # interpretaría como el pk de una cita.
    path('compartir/<str:token>/', CompartirCitaIcs.as_view(), name='cita-compartir-ics'),
    path('', include(router.urls)),
]
