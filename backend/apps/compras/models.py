from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import BaseModel, BaseTallerModel


class Compra(UuidPublicoModel, BaseTallerModel):
    """Factura de un proveedor: suma stock y, a crédito, queda por pagar.

    No se edita: una compra mal registrada se anula (revierte el stock) y se
    registra de nuevo. Ver `services.py`.
    """

    class Condicion(models.TextChoices):
        CONTADO = 'CONTADO', 'Contado'
        CREDITO = 'CREDITO', 'Crédito'

    class Estado(models.TextChoices):
        REGISTRADA = 'REGISTRADA', 'Registrada'
        ANULADA = 'ANULADA', 'Anulada'

    taller_field = 'taller'

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='compras')
    taller = models.ForeignKey(
        'empresas.Taller', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='compras', verbose_name='Taller',
    )
    proveedor = models.ForeignKey('proveedores.Proveedor', on_delete=models.PROTECT, related_name='compras')
    numero_factura = models.CharField(max_length=30, verbose_name='Número de factura del proveedor')
    fecha = models.DateField(verbose_name='Fecha de la factura')
    condicion = models.CharField(max_length=10, choices=Condicion.choices, default=Condicion.CONTADO)
    fecha_vencimiento = models.DateField(null=True, blank=True, verbose_name='Vence (crédito)')
    observaciones = models.CharField(max_length=255, blank=True, default='')

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    monto_iva = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.REGISTRADA)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='compras_registradas',
    )
    fecha_anulacion = models.DateTimeField(null=True, blank=True)
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='compras_anuladas',
    )
    motivo_anulacion = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Compra'
        verbose_name_plural = 'Compras'
        ordering = ['-fecha', '-created_at']
        constraints = [
            # La misma factura del mismo proveedor no se registra dos veces
            # (sí después de anularla).
            models.UniqueConstraint(
                fields=['empresa', 'proveedor', 'numero_factura'],
                condition=models.Q(estado='REGISTRADA', deleted_at__isnull=True),
                name='compra_factura_unica_vigente',
            ),
        ]
        indexes = [
            models.Index(
                fields=['empresa', 'taller', '-fecha'],
                condition=models.Q(deleted_at__isnull=True),
                name='compra_tal_vivas_idx',
            ),
        ]

    def __str__(self):
        return f'Compra {self.numero_factura} - {self.proveedor_id}'


class DetalleCompra(UuidPublicoModel, models.Model):
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name='detalles')
    repuesto = models.ForeignKey('inventario.Repuesto', on_delete=models.PROTECT, related_name='detalles_compra')
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    # Sin IVA: es el costo que entra al promedio del repuesto.
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.1500'))
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    monto_iva = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.subtotal = (Decimal(self.cantidad) * Decimal(self.costo_unitario)).quantize(Decimal('0.01'))
        self.monto_iva = (self.subtotal * Decimal(self.iva_porcentaje)).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)


class PagoCompra(UuidPublicoModel, BaseModel):
    """Pago al proveedor contra una compra. No se edita ni se borra: se anula."""

    compra = models.ForeignKey(Compra, on_delete=models.PROTECT, related_name='pagos')
    forma_pago = models.CharField(max_length=20, choices=[
        ('EFECTIVO', 'Efectivo'),
        ('TRANSFERENCIA', 'Transferencia'),
        ('TARJETA_DEBITO', 'Tarjeta de débito'),
        ('TARJETA_CREDITO', 'Tarjeta de crédito'),
        ('CHEQUE', 'Cheque'),
        ('OTRO', 'Otro'),
    ])
    monto = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    fecha = models.DateTimeField(default=timezone.now, editable=False)
    referencia = models.CharField(max_length=100, blank=True, default='')
    observacion = models.CharField(max_length=255, blank=True, default='')
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pagos_compra_registrados',
    )
    anulado = models.BooleanField(default=False)
    fecha_anulacion = models.DateTimeField(null=True, blank=True)
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pagos_compra_anulados',
    )
    motivo_anulacion = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Pago a proveedor'
        verbose_name_plural = 'Pagos a proveedores'
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'Pago {self.monto} - compra {self.compra_id}'
