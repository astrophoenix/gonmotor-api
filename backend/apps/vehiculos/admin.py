from django.contrib import admin
from .models import Vehiculo, VehiculoPropietario


class VehiculoPropietarioInline(admin.TabularInline):
    model = VehiculoPropietario
    extra = 1
    autocomplete_fields = ['cliente']
    fields = ('cliente', 'es_actual', 'fecha_inicio', 'fecha_fin')
    readonly_fields = ('fecha_inicio',)


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = (
        'placa',
        'marca',
        'modelo',
        'anio',
        'tipo',
        'pais_origen',
        'transmision',
        'combustible',
        'kilometraje_actual',
        'is_active'
    )
    list_filter = ('transmision', 'combustible', 'tipo', 'pais_origen', 'marca', 'is_active', 'empresas')
    search_fields = (
        'placa',
        'vin',
        'numero_motor',
        'marca',
        'modelo',
        'propietarios__cliente__nombre',
        'propietarios__cliente__identificacion',
        'empresas__nombre_comercial',
        'empresas__ruc'
    )
    readonly_fields = ('created_at', 'updated_at')
    inlines = [VehiculoPropietarioInline]
    filter_horizontal = ('empresas',)

    fieldsets = (
        ('Identificación del Vehículo', {
            'fields': ('placa', 'vin', 'numero_motor')
        }),
        ('Especificaciones Técnicas', {
            'fields': (
                ('marca', 'modelo', 'anio'),
                ('color', 'transmision', 'combustible', 'tipo', 'pais_origen'),
                'kilometraje_actual'
            )
        }),
        ('Empresas', {
            'fields': ('empresas',)
        }),
        ('Detalles Adicionales', {
            'fields': ('observaciones', 'is_active', 'created_at', 'updated_at')
        }),
    )


@admin.register(VehiculoPropietario)
class VehiculoPropietarioAdmin(admin.ModelAdmin):
    list_display = ('vehiculo', 'cliente', 'es_actual', 'fecha_inicio', 'fecha_fin')
    list_filter = ('es_actual', 'fecha_inicio')
    search_fields = (
        'vehiculo__placa',
        'vehiculo__marca',
        'vehiculo__modelo',
        'cliente__nombre',
        'cliente__identificacion',
    )
    autocomplete_fields = ('vehiculo', 'cliente')
    readonly_fields = ('fecha_inicio',)