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
        'logo_thumb',
        'is_active',
        'created_at'
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('nombre_comercial', 'razon_social', 'ruc', 'email_contacto')
    readonly_fields = ('created_at', 'updated_at', 'logo_preview')
    inlines = [TallerInline]
    
    fieldsets = (
        ('Información Principal', {
            'fields': ('nombre_comercial', 'razon_social', 'ruc')
        }),
        ('Contacto', {
            'fields': ('email_contacto', 'telefono')
        }),
        ('Logo', {
            'fields': ('logo', 'logo_preview')
        }),
        ('Estado', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )

    @admin.display(description='Logo')
    def logo_thumb(self, obj):
        if obj.logo:
            return f'<img src="{obj.logo.url}" style="height:40px;width:auto;" />'
        return '—'

    @admin.display(description='Vista previa')
    def logo_preview(self, obj):
        if obj.logo:
            return f'<img src="{obj.logo.url}" style="max-height:200px;width:auto;" />'
        return 'Sin logo cargado.'


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
