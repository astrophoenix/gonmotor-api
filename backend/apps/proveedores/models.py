from django.db import models

from apps.core.models import BaseModel


class Proveedor(BaseModel):
    TIPO_IDENTIFICACION = [
        ('C', 'Cédula'),
        ('R', 'RUC'),
        ('P', 'Pasaporte'),
    ]

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='proveedores'
    )

    tipo_identificacion = models.CharField(
        verbose_name="Tipo de Identificación",
        max_length=1,
        choices=TIPO_IDENTIFICACION,
        default='R'
    )
    identificacion = models.CharField(
        verbose_name="Identificación (Cédula/RUC/Pasaporte)",
        max_length=20,
    )
    nombre = models.CharField(
        verbose_name="Razón Social / Nombre",
        max_length=200
    )
    email = models.EmailField(
        verbose_name="Correo Electrónico",
        blank=True,
        default=""
    )
    telefono = models.CharField(
        verbose_name="Teléfono / WhatsApp",
        max_length=20,
        blank=True,
        default=""
    )
    direccion = models.TextField(
        verbose_name="Dirección",
        blank=True,
        default=""
    )
    contacto = models.CharField(
        verbose_name="Persona de Contacto",
        max_length=150,
        blank=True,
        default=""
    )

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['-created_at']

        # La misma identificación no se duplica dentro de la MISMA empresa,
        # pero SI puede existir en OTRAS empresas distintas.
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'identificacion'],
                name='unique_proveedor_per_empresa'
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.identificacion})"
