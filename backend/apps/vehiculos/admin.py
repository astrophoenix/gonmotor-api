from django.contrib import admin
from .models import Vehiculo


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = (
        'placa',
        'marca',
        'modelo',
        'anio',
        'cliente',
        'transmision',
        'combustible',
        'kilometraje_actual',
        'is_active'
    )
    list_filter = ('transmision', 'combustible', 'marca', 'is_active')
    search_fields = (
        'placa',
        'vin',
        'numero_motor',
        'marca',
        'modelo',
        'cliente__nombre',
        'cliente__identificacion'
    )
    autocomplete_fields = ['cliente']  # Util para seleccionar cliente mediante buscador en lugar de lista desplegable gigante
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        ('Propietario', {
            'fields': ('cliente',)
        }),
        ('Identificación del Vehículo', {
            'fields': ('placa', 'vin', 'numero_motor')
        }),
        ('Especificaciones Técnicas', {
            'fields': (
                ('marca', 'modelo', 'anio'),
                ('color', 'transmision', 'combustible'),
                'kilometraje_actual'
            )
        }),
        ('Detalles Adicionales', {
            'fields': ('observaciones', 'is_active', 'created_at', 'updated_at')
        }),
    )