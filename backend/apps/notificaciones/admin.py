from django.contrib import admin

from .models import PreferenciaMantenimiento, RegistroMensajeWhatsApp


@admin.register(PreferenciaMantenimiento)
class PreferenciaMantenimientoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'intervalo_km', 'dias_antelacion', 'frecuencia_dias', 'notificaciones_activas')
    list_filter = ('notificaciones_activas', 'notificar_vehiculos_sin_programar')


@admin.register(RegistroMensajeWhatsApp)
class RegistroMensajeWhatsAppAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'cliente_nombre', 'celular', 'estado', 'origen', 'proveedor', 'vehiculo')
    list_filter = ('estado', 'origen', 'proveedor')
    search_fields = ('cliente_nombre', 'celular', 'mensaje')
    readonly_fields = tuple(f.name for f in RegistroMensajeWhatsApp._meta.fields)