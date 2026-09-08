from django.contrib import admin

from .models import Cita


@admin.register(Cita)
class CitaAdmin(admin.ModelAdmin):
    list_display = (
        'fecha_cita',
        'hora_cita',
        'cliente',
        'vehiculo',
        'estado',
        'motivo',
        'asesor',
        'created_at',
    )
    list_filter = ('estado', 'motivo', 'empresa', 'fecha_cita')
    search_fields = (
        'cliente__nombre',
        'cliente__identificacion',
        'vehiculo__placa',
        'vehiculo__marca',
        'empresa__nombre_comercial',
    )
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('cliente', 'vehiculo', 'asesor', 'taller')