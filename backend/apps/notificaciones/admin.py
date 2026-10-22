from django.contrib import admin

from .models import NotificacionProgramada, PreferenciaMantenimiento, RegistroMensajeWhatsApp


@admin.register(PreferenciaMantenimiento)
class PreferenciaMantenimientoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'intervalo_km', 'dias_antelacion', 'frecuencia_dias', 'notificaciones_activas')
    list_filter = ('notificaciones_activas', 'recordatorios_cita_activos')


@admin.register(RegistroMensajeWhatsApp)
class RegistroMensajeWhatsAppAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'cliente_nombre', 'canal', 'celular', 'correo', 'estado', 'origen', 'proveedor', 'vehiculo')
    list_filter = ('canal', 'estado', 'origen', 'proveedor')
    search_fields = ('cliente_nombre', 'celular', 'correo', 'mensaje')
    readonly_fields = tuple(f.name for f in RegistroMensajeWhatsApp._meta.fields)

@admin.register(NotificacionProgramada)
class NotificacionProgramadaAdmin(admin.ModelAdmin):
    list_display = ('fecha_programada', 'empresa', 'tipo', 'canal', 'estado', 'origen', 'cliente', 'vehiculo', 'intentos')
    list_filter = ('estado', 'tipo', 'canal', 'origen')
    search_fields = ('mensaje', 'cliente__nombre', 'vehiculo__placa')
    raw_id_fields = ('cliente', 'vehiculo', 'orden', 'cita', 'registro', 'creado_por')
