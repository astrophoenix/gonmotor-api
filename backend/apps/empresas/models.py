from django.db import models
from apps.core.models import BaseModel


class Empresa(BaseModel):
    """
    Representa al Negocio / Razón Social principal (Cliente SaaS).
    """
    nombre_comercial = models.CharField(
        max_length=200, 
        verbose_name="Nombre Comercial"
    )
    razon_social = models.CharField(
        max_length=200, 
        blank=True, 
        default="", 
        verbose_name="Razón Social"
    )
    ruc = models.CharField(
        max_length=13, 
        unique=True, 
        verbose_name="RUC"
    )
    email_contacto = models.EmailField(
        verbose_name="Correo Electrónico de Contacto"
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default="", 
        verbose_name="Teléfono Principal"
    )
    logo = models.ImageField(
        upload_to='empresa_logos/',
        blank=True,
        null=True,
        verbose_name="Logo / Imagen de Empresa"
    )

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self):
        return f"{self.nombre_comercial} ({self.ruc})"


class Taller(BaseModel):
    """
    Representa una Sucursal o Taller físico de la Empresa.
    """
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='talleres',
        verbose_name="Empresa Perteneciente"
    )
    nombre = models.CharField(
        max_length=150, 
        verbose_name="Nombre del Taller / Sucursal"
    )
    codigo_sucursal = models.CharField(
        max_length=10, 
        blank=True, 
        default="001",
        verbose_name="Código de Sucursal (SRI / Contífico)"
    )
    ciudad = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Ciudad"
    )
    direccion = models.TextField(
        verbose_name="Dirección Físicas"
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default="", 
        verbose_name="Teléfono Taller"
    )

    class Meta:
        verbose_name = "Taller / Sucursal"
        verbose_name_plural = "Talleres / Sucursales"
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'codigo_sucursal'],
                condition=models.Q(is_active=True),
                name='una_sucursal_activa_por_empresa_codigo'
            )
        ]

    def __str__(self):
        return f"{self.empresa.nombre_comercial} - {self.nombre}"