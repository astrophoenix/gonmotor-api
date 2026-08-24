from django.contrib import admin
from .models import Cliente
from apps.vehiculos.models import VehiculoPropietario


class VehiculoPropietarioInline(admin.TabularInline):
    """
    Permite ver y agregar vehículos directamente en la pantalla del Cliente.
    """
    model = VehiculoPropietario
    extra = 1
    fields = ('vehiculo', 'es_actual', 'fecha_inicio', 'fecha_fin')
    readonly_fields = ('fecha_inicio',)
    autocomplete_fields = ('vehiculo',)
    show_change_link = True


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        'identificacion',
        'nombre',
        'tipo_identificacion',
        'telefono',
        'email',
        'is_active',
        'created_at'
    )
    list_filter = ('tipo_identificacion', 'is_active', 'created_at')
    search_fields = ('identificacion', 'nombre', 'email', 'telefono', 'contifico_id')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [VehiculoPropietarioInline]
    
    fieldsets = (
        ('Información Principal', {
            'fields': ('empresa', 'tipo_identificacion', 'identificacion', 'nombre')
        }),
        ('Contacto & Ubicación', {
            'fields': ('telefono', 'email', 'direccion')
        }),
        ('Integraciones & Estado', {
            'fields': ('contifico_id', 'is_active', 'created_at', 'updated_at')
        }),
    )