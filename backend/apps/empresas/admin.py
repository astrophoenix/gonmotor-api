from django.contrib import admin
from .models import Empresa, Taller


class TallerInline(admin.TabularInline):
    """
    Permite ver y agregar talleres directamente en la pantalla de Empresa.
    """
    model = Taller
    extra = 1
    fields = ('nombre', 'codigo_sucursal', 'direccion', 'telefono', 'is_active')
    show_change_link = True


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = (
        'nombre_comercial',
        'ruc',
        'email_contacto',
        'telefono',
        'is_active',
        'created_at'
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('nombre_comercial', 'razon_social', 'ruc', 'email_contacto')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [TallerInline]
    
    fieldsets = (
        ('Información Principal', {
            'fields': ('nombre_comercial', 'razon_social', 'ruc')
        }),
        ('Contacto', {
            'fields': ('email_contacto', 'telefono')
        }),
        ('Estado', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )


@admin.register(Taller)
class TallerAdmin(admin.ModelAdmin):
    list_display = (
        'nombre',
        'empresa',
        'codigo_sucursal',
        'direccion',
        'telefono',
        'is_active'
    )
    list_filter = ('empresa', 'is_active', 'created_at')
    search_fields = ('nombre', 'codigo_sucursal', 'direccion', 'empresa__nombre_comercial')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Empresa & Información', {
            'fields': ('empresa', 'nombre', 'codigo_sucursal')
        }),
        ('Ubicación & Contacto', {
            'fields': ('direccion', 'telefono')
        }),
        ('Estado', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )
