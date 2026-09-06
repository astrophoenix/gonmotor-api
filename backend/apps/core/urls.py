from django.urls import path
from .views import HealthCheckView, MejorarTextoView

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('mejorar-texto/', MejorarTextoView.as_view(), name='mejorar-texto'),
]