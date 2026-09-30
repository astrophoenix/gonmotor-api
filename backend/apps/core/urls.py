from django.urls import path
from .views import HealthCheckView, MejorarTextoView, DashboardView, DashboardV2View

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('mejorar-texto/', MejorarTextoView.as_view(), name='mejorar-texto'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('dashboard-v2/', DashboardV2View.as_view(), name='dashboard-v2'),
]