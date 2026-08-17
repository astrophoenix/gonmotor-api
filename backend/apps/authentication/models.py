from django.db import models
from django.contrib.auth.models import User
from apps.core.models import BaseModel


class UserProfile(BaseModel):
    ROLES = [
        ('ADMIN_SISTEMA', 'Superadmin SaaS'),
        ('ADMIN_EMPRESA', 'Dueño / Admin de Empresa'),
        ('ADMIN_TALLER', 'Gerente de Taller'),
        ('ASESOR', 'Asesor de Servicio'),
        ('MECANICO', 'Técnico / Mecánico'),
        ('CAJERO', 'Caja / Facturación'),
    ]

    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='profile'
    )
    # Vinculación obligatoria a la Empresa
    empresa = models.ForeignKey(
        'empresas.Empresa', 
        on_delete=models.CASCADE, 
        related_name='empleados',
        null=True,
        blank=True
    )
    # Sucursales a las que tiene acceso (Relación Muchos a Muchos)
    talleres = models.ManyToManyField(
        'empresas.Taller', 
        related_name='usuarios',
        blank=True,
        verbose_name="Talleres Asignados"
    )
    # Taller en el que está trabajando actualmente
    taller_activo = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usuarios_activos',
        verbose_name="Taller Activo en Sesión"
    )
    
    rol = models.CharField(
        max_length=20, 
        choices=ROLES, 
        default='MECANICO'
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default=""
    )

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuarios"

    def __str__(self):
        return f"{self.user.username} ({self.get_rol_display()}) - {self.empresa}"