from django.db import models
from apps.core.models import BaseModel

# Create your models here.
class Cliente(BaseModel):
    TIPO_IDENTIFICACION = [
        ('C', 'Cédula'),
        ('R', 'RUC'),
        ('P', 'Pasaporte'),
    ]

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='clientes'
    )

    tipo_identificacion = models.CharField(
        verbose_name="Tipo de Identificación",
        max_length=1,
        choices=TIPO_IDENTIFICACION,
        default='C'
    )
    identificacion = models.CharField(
        verbose_name="Identificación (Cédula/RUC/Pasaporte)",
        max_length=13,
        unique=True
    )
    nombre = models.CharField(
        verbose_name="Nombre o Razón Social",
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
    contifico_id = models.CharField(
        verbose_name="ID de Integración Contífico",
        max_length=50,
        blank=True,
        default=""
    )

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['-created_at']  # Muestra los clientes más recientes primero

    def __str__(self):
        return f"{self.nombre} ({self.identificacion})"

