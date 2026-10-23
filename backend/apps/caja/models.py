from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import BaseModel


class Caja(UuidPublicoModel, BaseModel):
    """Caja física de un taller.

    Hoy cada taller usa una sola («Caja principal», creada al abrirla por
    primera vez); el modelo admite varias para un taller con más de un cajero.
    """

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='cajas')
    taller = models.ForeignKey('empresas.Taller', on_delete=models.CASCADE, related_name='cajas')
    nombre = models.CharField(max_length=60, default='Caja principal')

    class Meta:
        verbose_name = 'Caja'
        verbose_name_plural = 'Cajas'
        ordering = ['taller_id', 'id']
        constraints = [
            models.UniqueConstraint(fields=['taller', 'nombre'], name='caja_taller_nombre_unico'),
        ]

    def __str__(self):
        return f'{self.nombre} - {self.taller}'


class SesionCaja(UuidPublicoModel, BaseModel):
    """Una jornada de la caja: desde que se abre con el efectivo inicial hasta
    que se cierra con el efectivo contado. Cerrada no se modifica; solo un
    administrador la reabre (acción especial `reabrir_caja`)."""

    class Estado(models.TextChoices):
        ABIERTA = 'ABIERTA', 'Abierta'
        CERRADA = 'CERRADA', 'Cerrada'

    caja = models.ForeignKey(Caja, on_delete=models.PROTECT, related_name='sesiones')
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTA)

    monto_inicial = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))],
    )
    fecha_apertura = models.DateTimeField(default=timezone.now, editable=False)
    abierta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cajas_abiertas',
    )

    fecha_cierre = models.DateTimeField(null=True, blank=True)
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cajas_cerradas',
    )
    # Fotografía del cierre: lo que el sistema esperaba y lo que se contó.
    efectivo_esperado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    efectivo_contado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    diferencia = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    observacion_cierre = models.CharField(max_length=255, blank=True, default='')

    # Última reapertura (el historial completo queda en la auditoría).
    reaperturas = models.PositiveSmallIntegerField(default=0)
    fecha_reapertura = models.DateTimeField(null=True, blank=True)
    reabierta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cajas_reabiertas',
    )
    motivo_reapertura = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Sesión de caja'
        verbose_name_plural = 'Sesiones de caja'
        ordering = ['-fecha_apertura', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['caja'], condition=Q(estado='ABIERTA'), name='caja_una_sesion_abierta',
            ),
        ]

    def __str__(self):
        return f'{self.caja} {timezone.localtime(self.fecha_apertura):%d/%m/%Y} ({self.get_estado_display()})'


class MovimientoCaja(UuidPublicoModel, BaseModel):
    """Entrada o salida de efectivo que no es un pago de orden (compra menor,
    cambio, retiro del dueño...). Como los pagos, no se edita: se anula."""

    class Tipo(models.TextChoices):
        INGRESO = 'INGRESO', 'Ingreso'
        EGRESO = 'EGRESO', 'Egreso'

    sesion = models.ForeignKey(SesionCaja, on_delete=models.PROTECT, related_name='movimientos')
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    monto = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    motivo = models.CharField(max_length=255)
    gasto = models.OneToOneField(
        'gastos.Gasto', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimiento_caja',
    )
    fecha = models.DateTimeField(default=timezone.now, editable=False)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_caja',
    )

    anulado = models.BooleanField(default=False)
    fecha_anulacion = models.DateTimeField(null=True, blank=True)
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_caja_anulados',
    )
    motivo_anulacion = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Movimiento de caja'
        verbose_name_plural = 'Movimientos de caja'
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'{self.get_tipo_display()} {self.monto} - {self.motivo}'
