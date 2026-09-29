from django.urls import path
from .views import HealthCheckView, MejorarTextoView, DashboardView

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('mejorar-texto/', MejorarTextoView.as_view(), name='mejorar-texto'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
]