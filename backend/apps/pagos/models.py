from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import BaseModel


class PagoOrden(UuidPublicoModel, BaseModel):
    """Dinero que entra (pago o abono) o sale (devolución) por una orden de trabajo.

    Un movimiento nunca se edita ni se borra: si se registró mal se **anula**
    (con motivo y usuario) y se registra de nuevo. Las reglas de saldo viven en
    `services.py`: no hay saldos a favor del cliente.
    """

    class Tipo(models.TextChoices):
        PAGO = 'PAGO', 'Pago'
        DEVOLUCION = 'DEVOLUCION', 'Devolución'

    class FormaPago(models.TextChoices):
        EFECTIVO = 'EFECTIVO', 'Efectivo'
        TRANSFERENCIA = 'TRANSFERENCIA', 'Transferencia'
        TARJETA_DEBITO = 'TARJETA_DEBITO', 'Tarjeta de débito'
        TARJETA_CREDITO = 'TARJETA_CREDITO', 'Tarjeta de crédito'
        CHEQUE = 'CHEQUE', 'Cheque'
        OTRO = 'OTRO', 'Otro'

    orden_trabajo = models.ForeignKey(
        'ordenes.OrdenTrabajo',
        on_delete=models.PROTECT,
        related_name='pagos',
        verbose_name='Orden de trabajo',
    )
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.PAGO)
    forma_pago = models.CharField(max_length=20, choices=FormaPago.choices, verbose_name='Forma de pago')
    monto = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    # La fija el servidor al registrar: el cierre de caja cuenta los
    # movimientos del día y no admite fechas pasadas.
    fecha = models.DateTimeField(default=timezone.now, editable=False)
    referencia = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Número de comprobante, transferencia, voucher o cheque',
    )
    observacion = models.CharField(max_length=255, blank=True, default='')
    # Pago recibido antes de que el trabajo esté completado.
    es_anticipo = models.BooleanField(default=False, editable=False)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_registrados',
    )

    anulado = models.BooleanField(default=False)
    fecha_anulacion = models.DateTimeField(null=True, blank=True)
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_anulados',
    )
    motivo_anulacion = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Pago de orden'
        verbose_name_plural = 'Pagos de órdenes'
        ordering = ['-fecha', '-id']
        indexes = [
            models.Index(fields=['orden_trabajo', 'anulado'], name='pago_orden_vigente_idx'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.monto} - OT {self.orden_trabajo_id}'
