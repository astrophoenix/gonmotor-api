from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import BaseTallerModel


class Gasto(BaseTallerModel):
    class Categoria(models.TextChoices):
        ARRIENDO = 'ARRIENDO', 'Arriendo'
        SUELDOS = 'SUELDOS', 'Sueldos/Nómina'
        SERVICIOS = 'SERVICIOS', 'Servicios Básicos (Luz/Agua/Internet)'
        INSUMOS = 'INSUMOS', 'Insumos/Compras menores'
        COMISIONES = 'COMISIONES', 'Comisiones'
        MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento local'
        TRANSPORTE = 'TRANSPORTE', 'Transporte/Gasolina'
        OTROS = 'OTROS', 'Otros'

    taller_field = 'taller'

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='gastos',
    )
    taller = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='gastos',
        verbose_name='Taller',
    )
    fecha = models.DateField(verbose_name='Fecha del gasto')
    categoria = models.CharField(
        max_length=20,
        choices=Categoria.choices,
        default=Categoria.OTROS,
    )
    monto = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    descripcion = models.CharField(max_length=255, blank=True, default='')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='gastos_creados',
    )

    class Meta:
        verbose_name = 'Gasto'
        verbose_name_plural = 'Gastos'
        ordering = ['-fecha', '-created_at']
        indexes = [
            models.Index(
                fields=['empresa', 'taller', '-fecha'],
                condition=models.Q(deleted_at__isnull=True),
                name='gasto_tal_vivos_idx'
            ),
        ]

    def __str__(self):
        return f'Gasto {self.fecha} - {self.monto} ({self.get_categoria_display()})'
