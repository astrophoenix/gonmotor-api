from django.contrib import admin

from apps.core.admin import SoftDeleteAdminMixin
from .models import Vehiculo, VehiculoEmpresa, VehiculoPropietario


class VehiculoEmpresaInline(admin.TabularInline):
    model = VehiculoEmpresa
    extra = 0
    fields = ('empresa', 'is_active', 'deleted_at')
    readonly_fields = ('deleted_at',)


class VehiculoPropietarioInline(admin.TabularInline):
    model = VehiculoPropietario
    extra = 1
    autocomplete_fields = ['cliente']
    fields = ('cliente', 'es_actual', 'fecha_inicio', 'fecha_fin')
    readonly_fields = ('fecha_inicio',)


@admin.register(Vehiculo)
class VehiculoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
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
    inlines = [VehiculoEmpresaInline, VehiculoPropietarioInline]

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