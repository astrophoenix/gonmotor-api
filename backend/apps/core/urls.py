from django.urls import path
from .views import HealthCheckView, MejorarTextoView, DashboardView, DashboardV2View
from .busqueda import BusquedaGlobalView

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('mejorar-texto/', MejorarTextoView.as_view(), name='mejorar-texto'),
    path('buscar/', BusquedaGlobalView.as_view(), name='busqueda-global'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('dashboard-v2/', DashboardV2View.as_view(), name='dashboard-v2'),
]