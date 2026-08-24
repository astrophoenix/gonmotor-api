from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile, UsuarioEmpresa


class UserProfileInline(admin.StackedInline):
    """Inline para ver/editar el perfil del usuario (teléfono, taller activo)"""
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Perfil de Usuario'
    fk_name = 'user'


class UsuarioEmpresaInline(admin.TabularInline):
    """Inline para asignar empresas (RUCs), roles y talleres al usuario"""
    model = UsuarioEmpresa
    extra = 1
    filter_horizontal = ('talleres',)
    fields = ('empresa', 'rol', 'talleres', 'is_active')
    fk_name = 'user'


# Re-registramos el UserAdmin de Django para inyectar los inlines
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline, UsuarioEmpresaInline)
    list_display = (
        'username', 
        'email', 
        'first_name', 
        'last_name', 
        'is_staff', 
        'get_empresas'
    )

    @admin.display(description='Empresas Asignadas')
    def get_empresas(self, obj):
        empresas = obj.empresas_asociadas.filter(is_active=True).values_list('empresa__nombre_comercial', flat=True)
        return ", ".join(empresas) if empresas else "-"


# Desregistramos el User nativo y registramos la versión personalizada
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UsuarioEmpresa)
class UsuarioEmpresaAdmin(admin.ModelAdmin):
    """
    Vista de administración independiente para buscar/filtrar por Empresa o RUC.
    """
    list_display = (
        'user',
        'empresa',
        'rol',
        'is_active',
        'created_at'
    )
    list_filter = ('rol', 'empresa', 'is_active', 'created_at')
    search_fields = (
        'user__username', 
        'user__first_name', 
        'user__last_name', 
        'empresa__nombre_comercial', 
        'empresa__ruc'
    )
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('talleres',)

    fieldsets = (
        ('Asignación Principal', {
            'fields': ('user', 'empresa', 'rol')
        }),
        ('Talleres/Sucursales Permitidas', {
            'fields': ('talleres',)
        }),
        ('Estado y Auditoría', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )