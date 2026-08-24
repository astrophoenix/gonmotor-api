from django.db import models
from django.contrib.auth.models import User
from apps.core.models import BaseModel


class UserProfile(BaseModel):
    """
    Perfil global del usuario (datos personales / preferencias).
    Las empresas y roles específicos se manejan en UsuarioEmpresa.
    """
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='profile'
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default=""
    )
    # Taller en el que está trabajando actualmente durante su sesión actual
    taller_activo = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usuarios_activos',
        verbose_name="Taller Activo en Sesión"
    )

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuarios"

    def __str__(self):
        return f"Perfil de {self.user.username}"


class UsuarioEmpresa(BaseModel):
    """
    Tabla intermedia que permite a un usuario pertenecer a N Empresas (RUCs distintos)
    y tener un rol específico en cada una de ellas.
    """
    ROLES = [
        ('ADMIN_SISTEMA', 'Superadmin SaaS'),
        ('ADMIN_EMPRESA', 'Dueño / Admin de Empresa'),
        ('ADMIN_TALLER', 'Gerente de Taller'),
        ('ASESOR', 'Asesor de Servicio'),
        ('MECANICO', 'Técnico / Mecánico'),
        ('CAJERO', 'Caja / Facturación'),
    ]

    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='empresas_asociadas'
    )
    empresa = models.ForeignKey(
        'empresas.Empresa', 
        on_delete=models.CASCADE, 
        related_name='usuarios_asociados'
    )
    rol = models.CharField(
        max_length=20, 
        choices=ROLES, 
        default='MECANICO'
    )
    # Sucursales de ESTA empresa específica a las que tiene acceso el usuario
    talleres = models.ManyToManyField(
        'empresas.Taller', 
        related_name='usuarios_asignados',
        blank=True,
        verbose_name="Talleres Asignados"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Acceso Activo"
    )

    class Meta:
        verbose_name = "Asignación Usuario-Empresa"
        verbose_name_plural = "Asignaciones Usuarios-Empresas"
        # Evita duplicar la relación usuario - empresa
        unique_together = ('user', 'empresa')

    def __str__(self):
        return f"{self.user.username} - {self.empresa} ({self.get_rol_display()})"