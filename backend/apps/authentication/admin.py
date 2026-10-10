from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Rol, UserProfile, UsuarioEmpresa


class UsuarioEmpresaAdminForm(forms.ModelForm):
    """Asignación de rol con desplegable dinámico.

    Las opciones se leen de la tabla `Rol` (los de sistema y los
    personalizados de todas las empresas) para evitar códigos escritos a
    mano; un rol personalizado solo puede asignarse a su propia empresa.
    """

    class Meta:
        model = UsuarioEmpresa
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        opciones = [] if self.instance and self.instance.rol else [('', '---------')]
        for rol in Rol.objects.filter(is_active=True):
            etiqueta = rol.nombre
            if rol.empresa_id:
                etiqueta = f'{etiqueta} — {rol.empresa.nombre_comercial}'
            opciones.append((rol.codigo, etiqueta))
        if self.instance and self.instance.rol and not any(
            valor == self.instance.rol for valor, _ in opciones
        ):
            opciones.append((self.instance.rol, f'{self.instance.rol} (eliminado)'))
        self.fields['rol'].widget = forms.Select(choices=opciones)
        self.fields['rol'].choices = opciones

    def clean(self):
        cleaned = super().clean()
        rol_codigo = cleaned.get('rol')
        empresa = cleaned.get('empresa')
        if rol_codigo and empresa:
            rol = Rol.objects.filter(codigo=rol_codigo).first()
            if rol and rol.empresa_id and rol.empresa_id != empresa.pk:
                self.add_error('rol', 'Este rol pertenece a otra empresa.')
        return cleaned


class UserProfileInline(admin.StackedInline):
    """Inline para ver/editar el perfil del usuario (teléfono, taller activo)"""
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Perfil de Usuario'
    fk_name = 'user'


class UsuarioEmpresaInline(admin.TabularInline):
    """Inline para asignar empresas (RUCs), roles y talleres al usuario"""
    model = UsuarioEmpresa
    form = UsuarioEmpresaAdminForm
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
    form = UsuarioEmpresaAdminForm
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
        ('Talleres Permitidos', {
            'fields': ('talleres',)
        }),
        ('Estado y Auditoría', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )
