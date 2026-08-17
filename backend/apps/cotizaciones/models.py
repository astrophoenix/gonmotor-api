from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel


class Cotizacion(BaseModel):
    class EstadoCotizacion(models.TextChoices):
        BORRADOR = 'BORRADOR', 'Borrador'
        ENVIADA = 'ENVIADA', 'Enviada al Cliente'
        ACEPTADA = 'ACEPTADA', 'Aceptada'
        RECHAZADA = 'RECHAZADA', 'Rechazada'
        VENCIDA = 'VENCIDA', 'Vencida'
        CONVERTIDA = 'CONVERTIDA', 'Convertida a Orden'

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='cotizaciones')
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='cotizaciones')
    vehiculo = models.ForeignKey('vehiculos.Vehiculo', on_delete=models.SET_NULL, null=True, blank=True)

    numero_cotizacion = models.CharField(max_length=20, unique=True)
    estado = models.CharField(max_length=20, choices=EstadoCotizacion.choices, default=EstadoCotizacion.BORRADOR)
    validez_dias = models.PositiveIntegerField(default=15, verbose_name='Días de validez')

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    observaciones = models.TextField(blank=True, null=True)

    orden_trabajo_origen = models.ForeignKey(
        'ordenes.OrdenTrabajo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cotizaciones_generadas',
        help_text="Orden de trabajo de la cual surgió este presupuesto tras un diagnóstico"
    )
    
    class Meta:
        verbose_name = 'Cotización'
        verbose_name_plural = 'Cotizaciones'

    def __str__(self):
        return f'{self.numero_cotizacion} - {self.cliente}'


class DetalleServicioCotizacion(models.Model):
    """Mano de obra o servicios estimativos."""

    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='servicios')
    descripcion = models.CharField(max_length=255, verbose_name='Servicio / Mano de obra')
    horas_estimadas = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        self.subtotal = Decimal(self.horas_estimadas) * Decimal(self.precio_unitario)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.descripcion} - {self.cotizacion.numero_cotizacion}'


class DetalleRepuestoCotizacion(models.Model):
    """Repuestos requeridos para la cotización."""

    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='repuestos')
    codigo_repuesto = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.CharField(max_length=255)
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario_referencial = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    es_opcional = models.BooleanField(default=False, help_text='Para sugerencias adicionales al cliente')

    def save(self, *args, **kwargs):
        self.subtotal = Decimal(self.cantidad) * Decimal(self.precio_unitario_referencial)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.descripcion} - {self.cotizacion.numero_cotizacion}'