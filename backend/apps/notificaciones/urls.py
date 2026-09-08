from django.urls import path

from .views import (
    EjecutarRecordatoriosView,
    EnviarPruebaWhatsAppView,
    EnviarRecordatorioVehiculoView,
    EstadoNotificacionesView,
    PreferenciasView,
    RecordatoriosListView,
)

urlpatterns = [
    path('estado/', EstadoNotificacionesView.as_view(), name='notificaciones-estado'),
    path('preferencias/', PreferenciasView.as_view(), name='notificaciones-preferencias'),
    path('recordatorios/', RecordatoriosListView.as_view(), name='notificaciones-recordatorios'),
    path('recordatorios/enviar-vehiculo/', EnviarRecordatorioVehiculoView.as_view(), name='notificaciones-enviar-vehiculo'),
    path('recordatorios/enviar-prueba/', EnviarPruebaWhatsAppView.as_view(), name='notificaciones-enviar-prueba'),
    path('recordatorios/ejecutar/', EjecutarRecordatoriosView.as_view(), name='notificaciones-ejecutar'),
]