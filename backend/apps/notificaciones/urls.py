from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CampanaViewSet,
    NotificacionProgramadaViewSet,
    CronProcesarNotificacionesView,
    EnviarPruebaWhatsAppView,
    EnviarRecordatorioVehiculoView,
    EstadoNotificacionesView,
    PreferenciasView,
    RecordatoriosListView,
)

router = DefaultRouter()
router.register('programadas', NotificacionProgramadaViewSet, basename='notificaciones-programadas')
router.register('campanas', CampanaViewSet, basename='notificaciones-campanas')

urlpatterns = [
    path('estado/', EstadoNotificacionesView.as_view(), name='notificaciones-estado'),
    path('preferencias/', PreferenciasView.as_view(), name='notificaciones-preferencias'),
    path('recordatorios/', RecordatoriosListView.as_view(), name='notificaciones-recordatorios'),
    path('recordatorios/enviar-vehiculo/', EnviarRecordatorioVehiculoView.as_view(), name='notificaciones-enviar-vehiculo'),
    path('recordatorios/enviar-prueba/', EnviarPruebaWhatsAppView.as_view(), name='notificaciones-enviar-prueba'),
    path('cron/procesar/', CronProcesarNotificacionesView.as_view(), name='notificaciones-cron-procesar'),
    path('', include(router.urls)),
]
