from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

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

    recepcion_origen = models.ForeignKey(
        'ordenes.RecepcionVehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cotizaciones_generadas',
        help_text='Recepción del vehículo de la cual se derivó esta cotización'
    )

    inspeccion_origen = models.ForeignKey(
        'ordenes.InspeccionVehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cotizaciones_generadas',
        help_text='Inspección técnica de la cual se derivó esta cotización'
    )

    orden_trabajo_origen = models.ForeignKey(
        'ordenes.OrdenTrabajo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cotizaciones_generadas',
        help_text="Orden de trabajo de la cual surgió este presupuesto tras un diagnóstico"
    )

    fecha_aceptacion = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de aceptación')
    aceptada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cotizaciones_aceptadas',
        verbose_name='Aceptada por'
    )
    metodo_aceptacion = models.CharField(
        max_length=20,
        choices=[
            ('PRESENCIAL', 'Presencial en taller'),
            ('EMAIL', 'Correo electrónico'),
            ('WHATSAPP', 'WhatsApp'),
            ('TELEFONO', 'Teléfono'),
        ],
        null=True,
        blank=True,
        verbose_name='Método de aceptación'
    )

    class Meta:
        verbose_name = 'Cotización'
        verbose_name_plural = 'Cotizaciones'

    def __str__(self):
        return f'{self.numero_cotizacion} - {self.cliente}'

    def _generar_numero_orden(self):
        """Genera un numero_orden secuencial único por empresa (prefijo + empresa + id)."""
        from apps.ordenes.models import OrdenTrabajo

        ultimo = OrdenTrabajo.objects.filter(empresa_id=self.empresa_id).order_by('-id').first()
        siguiente = (ultimo.id + 1) if ultimo else 1
        return f'OT-{self.empresa_id:04d}-{siguiente:04d}'

    def convertir_a_orden(self, usuario=None):
        """Crea una OrdenTrabajo desde esta cotización y vincula la recepción/inspección."""
        if self.estado != self.EstadoCotizacion.ACEPTADA:
            raise ValueError("La cotización debe estar aceptada para convertirla a orden.")

        from apps.ordenes.models import OrdenTrabajo

        inspeccion = self.inspeccion_origen
        if inspeccion is None and self.recepcion_origen:
            inspeccion = self.recepcion_origen.inspecciones.first()
        recepcion = self.recepcion_origen or (inspeccion.recepcion if inspeccion else None)

        vehiculo = self.vehiculo or (recepcion.vehiculo if recepcion else None)
        cliente = self.cliente
        if not vehiculo or not cliente:
            raise ValueError(
                'No se puede convertir la cotización a orden: faltan cliente o vehículo. '
                'Asócialos a la cotización o a su recepción de origen.'
            )

        ot = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            sucursal=self.empresa.talleres.first() if hasattr(self.empresa, 'talleres') else None,
            cliente=cliente,
            vehiculo=vehiculo,
            asesor=usuario,
            cotizacion_origen=self,
            numero_orden=self._generar_numero_orden(),
            tipo_trabajo=inspeccion.tipo_inspeccion if inspeccion else OrdenTrabajo.TipoTrabajo.CORRECTIVO,
            observaciones_internas=inspeccion.diagnostico_tecnico if inspeccion else None,
        )

        if recepcion:
            recepcion.orden_trabajo = ot
            recepcion.save(update_fields=['orden_trabajo', 'updated_at'])
        if inspeccion:
            inspeccion.orden_trabajo = ot
            inspeccion.save(update_fields=['orden_trabajo', 'updated_at'])

        self.estado = self.EstadoCotizacion.CONVERTIDA
        self.fecha_aceptacion = timezone.now()
        self.aceptada_por = usuario
        self.save()

        return ot


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