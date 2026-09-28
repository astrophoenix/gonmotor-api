from decimal import Decimal
from django.db import models
from django.conf import settings
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

    ESTADOS_VIGENTES = (
        EstadoCotizacion.BORRADOR,
        EstadoCotizacion.ENVIADA,
        EstadoCotizacion.ACEPTADA,
    )

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='cotizaciones')
    sucursal = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cotizaciones',
        verbose_name='Taller',
        help_text='Taller que emite la cotización (secuencia de numeración)'
    )
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='cotizaciones')
    vehiculo = models.ForeignKey('vehiculos.Vehiculo', on_delete=models.SET_NULL, null=True, blank=True)

    numero_cotizacion = models.CharField(max_length=20, verbose_name='Número de Cotización')
    estado = models.CharField(max_length=20, choices=EstadoCotizacion.choices, default=EstadoCotizacion.BORRADOR)
    validez_dias = models.PositiveIntegerField(default=15, verbose_name='Días de validez')

    subtotal_servicios = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_repuestos = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_neto = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_base_0 = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_base_gravada = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), help_text="Subtotal bruto acumulado")
    total_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="Monto total del IVA")
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

    fecha_envio = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de envío')
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
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'numero_cotizacion'],
                name='cotizacion_empresa_numero_unico'
            ),
            # Una sola cotización vigente por inspección y por recepción. Las
            # columnas son null cuando la cotización se creó de forma directa
            # (sin origen previo) y Postgres no las considera duplicadas.
            models.UniqueConstraint(
                fields=['inspeccion_origen'],
                condition=models.Q(estado__in=['BORRADOR', 'ENVIADA', 'ACEPTADA']),
                name='cotizacion_inspeccion_vigente_unica'
            ),
            models.UniqueConstraint(
                fields=['recepcion_origen'],
                condition=models.Q(estado__in=['BORRADOR', 'ENVIADA', 'ACEPTADA']),
                name='cotizacion_recepcion_vigente_unica'
            ),
        ]

    def __str__(self):
        return f'{self.numero_cotizacion} - {self.cliente}'

    def recalcular_totales(self):
        servicios_activos = self.servicios.all()
        repuestos_activos = self.repuestos.all()

        self.subtotal_servicios = Decimal(sum(item.subtotal for item in servicios_activos))
        self.subtotal_repuestos = Decimal(sum(item.subtotal for item in repuestos_activos))
        
        total_desc_serv = sum(item.descuento for item in servicios_activos)
        total_desc_rep = sum(item.descuento for item in repuestos_activos)
        self.descuento = Decimal(total_desc_serv + total_desc_rep)

        self.subtotal_neto = self.subtotal_servicios + self.subtotal_repuestos
        self.subtotal = self.subtotal_neto + self.descuento

        base_0_serv = sum(item.subtotal for item in servicios_activos if item.iva_porcentaje == 0)
        base_0_rep = sum(item.subtotal for item in repuestos_activos if item.iva_porcentaje == 0)
        self.subtotal_base_0 = Decimal(base_0_serv + base_0_rep)

        base_grav_serv = sum(item.subtotal for item in servicios_activos if item.iva_porcentaje > 0)
        base_grav_rep = sum(item.subtotal for item in repuestos_activos if item.iva_porcentaje > 0)
        self.subtotal_base_gravada = Decimal(base_grav_serv + base_grav_rep)

        iva_serv = sum(item.monto_iva for item in servicios_activos)
        iva_rep = sum(item.monto_iva for item in repuestos_activos)
        self.total_iva = Decimal(iva_serv + iva_rep)

        self.total = self.subtotal_neto + self.total_iva
        
        self.save(update_fields=[
            'subtotal_servicios', 'subtotal_repuestos', 'descuento', 
            'subtotal_neto', 'subtotal_base_0', 'subtotal_base_gravada', 
            'subtotal', 'total_iva', 'total', 'updated_at'
        ])

    def sincronizar_desde_inspeccion(self):
        inspeccion = self.inspeccion_origen
        if inspeccion is None:
            raise ValueError('La cotización no tiene una inspección de origen.')
        if inspeccion.orden_trabajo_id:
            raise ValueError(
                'La inspección ya se convirtió en orden de trabajo; no se puede sincronizar la cotización.'
            )

        self.servicios.all().delete()
        self.repuestos.all().delete()

        for det in inspeccion.servicios_detectados.all():
            iva_defecto = getattr(det.servicio, 'iva_porcentaje_defecto', Decimal('0.1500')) if det.servicio_id else Decimal('0.1500')
            DetalleServiceCotizacion_obj = DetalleServicioCotizacion(
                cotizacion=self,
                codigo=det.servicio.codigo if det.servicio_id else None,
                descripcion=(det.descripcion or '').strip(),
                horas_estimadas=det.horas_estimadas,
                precio_unitario=Decimal(det.precio_referencial or '0.00'),
                iva_porcentaje=iva_defecto,
                es_opcional=det.es_sugerido,
            )
            DetalleServiceCotizacion_obj.save()

        for det in inspeccion.repuestos_sugeridos.all():
            iva_defecto = getattr(det.repuesto, 'iva_porcentaje_defecto', Decimal('0.1500')) if det.repuesto_id else Decimal('0.1500')
            DetalleRepuestoCotizacion_obj = DetalleRepuestoCotizacion(
                cotizacion=self,
                codigo_repuesto=det.repuesto.codigo if det.repuesto_id else None,
                descripcion=(det.descripcion or '').strip(),
                cantidad=int(Decimal(det.cantidad or 1)),
                precio_unitario_referencial=Decimal(det.precio_referencial or '0.00'),
                iva_porcentaje=iva_defecto,
                es_opcional=det.es_sugerido,
            )
            DetalleRepuestoCotizacion_obj.save()

        self.recalcular_totales()

    def _resolver_taller(self):
        from apps.empresas.services import resolver_taller

        if self.sucursal_id:
            return self.sucursal

        sucursal = None
        if self.recepcion_origen_id and self.recepcion_origen.sucursal_id:
            sucursal = self.recepcion_origen.sucursal

        if sucursal is None and self.inspeccion_origen_id:
            inspeccion = self.inspeccion_origen
            if inspeccion.sucursal_id:
                sucursal = inspeccion.sucursal
            elif inspeccion.recepcion_id and inspeccion.recepcion.sucursal_id:
                sucursal = inspeccion.recepcion.sucursal

        if sucursal is None and self.orden_trabajo_origen_id and self.orden_trabajo_origen.sucursal_id:
            sucursal = self.orden_trabajo_origen.sucursal

        return resolver_taller(self.empresa_id, sucursal)

    def _generar_numero_orden(self):
        from apps.empresas.services import generar_codigo_secuencial
        taller = self._resolver_taller()
        return generar_codigo_secuencial(taller, 'ot')

    def _crear_orden_trabajo(self, usuario=None):
        from apps.ordenes.models import (
            DetalleRepuestoOrdenTrabajo,
            DetalleServicioOrdenTrabajo,
            OrdenTrabajo,
            TipoTrabajo,
        )

        inspeccion = self.inspeccion_origen
        if inspeccion is None and self.recepcion_origen:
            inspeccion = self.recepcion_origen.inspecciones.first()
        recepcion = self.recepcion_origen or (inspeccion.recepcion if inspeccion else None)

        tipo_trabajo = (
            inspeccion.tipo_inspeccion if inspeccion
            else recepcion.tipo_recepcion if recepcion
            else TipoTrabajo.MANTENIMIENTO
        )

        vehiculo = self.vehiculo or (recepcion.vehiculo if recepcion else None)
        cliente = self.cliente
        if not vehiculo or not cliente:
            raise ValueError(
                'No se puede generar la orden de trabajo: faltan cliente o vehículo. '
                'Asócialos a la cotización o a su recepción de origen.'
            )

        numero_orden = self._generar_numero_orden()
        sucursal_ot = self._resolver_taller()

        ot = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            sucursal=sucursal_ot,
            cliente=cliente,
            vehiculo=vehiculo,
            asesor=usuario,
            cotizacion_origen=self,
            numero_orden=numero_orden,
            tipo_trabajo=tipo_trabajo,
            observaciones=inspeccion.diagnostico_tecnico if inspeccion else None,
        )

        if recepcion:
            recepcion.orden_trabajo = ot
            recepcion.save(update_fields=['orden_trabajo', 'updated_at'])
        if inspeccion:
            inspeccion.orden_trabajo = ot
            inspeccion.estado = 'FINALIZADA'
            inspeccion.save(update_fields=['orden_trabajo', 'estado', 'updated_at'])

        for det in self.servicios.all():
            DetalleServicioOrdenTrabajo.objects.create(
                orden_trabajo=ot,
                descripcion=det.descripcion,
                horas_aplicadas=det.horas_estimadas,
                precio_unitario=det.precio_unitario,
                descuento=det.descuento,
                iva_porcentaje=det.iva_porcentaje
            )

        for det in self.repuestos.all():
            DetalleRepuestoOrdenTrabajo.objects.create(
                orden_trabajo=ot,
                codigo_repuesto=det.codigo_repuesto,
                descripcion=det.descripcion,
                cantidad=Decimal(det.cantidad or 1),
                precio_unitario=det.precio_unitario_referencial,
                descuento=det.descuento,
                iva_porcentaje=det.iva_porcentaje
            )

        ot.calcular_totales()
        return ot

    def generar_orden(self, usuario=None, metodo_aceptacion=None):
        if self.estado != self.EstadoCotizacion.ACEPTADA:
            raise ValueError("La cotización debe estar aceptada para generar una orden.")

        if self.orden_trabajo_origen_id:
            raise ValueError("Esta cotización ya generó una orden de trabajo.")

        if self.inspeccion_origen_id and self.inspeccion_origen.orden_trabajo_id:
            raise ValueError(
                f"La inspección {self.inspeccion_origen.numero_inspeccion} ya se convirtió "
                f"en la orden {self.inspeccion_origen.orden_trabajo.numero_orden}."
            )

        recepcion = self.recepcion_origen
        if recepcion is None and self.inspeccion_origen_id and self.inspeccion_origen.recepcion_id:
            recepcion = self.inspeccion_origen.recepcion
        if recepcion is not None and recepcion.orden_trabajo_id:
            raise ValueError(
                f"La recepción {recepcion.numero_recepcion} ya tiene la orden "
                f"{recepcion.orden_trabajo.numero_orden}. Cotiza el trabajo adicional "
                f"desde esa orden de trabajo."
            )

        if not self.servicios.exists() and not self.repuestos.exists():
            raise ValueError(
                "La cotización no tiene servicios ni repuestos: agrega al menos un "
                "detalle antes de generar la orden de trabajo."
            )

        if self.total <= Decimal('0.00'):
            raise ValueError(
                "La cotización tiene total 0. Revisa precios y cantidades antes de "
                "generar la orden de trabajo."
            )

        ot = self._crear_orden_trabajo(usuario=usuario)

        self.fecha_aceptacion = self.fecha_aceptacion or timezone.now()
        self.aceptada_por = self.aceptada_por or usuario
        self.metodo_aceptacion = metodo_aceptacion or self.metodo_aceptacion or 'PRESENCIAL'
        self.estado = self.EstadoCotizacion.CONVERTIDA

        self.save()
        return ot


class DetalleServicioCotizacion(BaseModel):
    """Mano de obra o servicios estimativos."""

    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='servicios')
    codigo = models.CharField(max_length=50, blank=True, null=True, verbose_name='Código')
    descripcion = models.CharField(max_length=255, verbose_name='Servicio / Mano de obra')
    horas_estimadas = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.1500'))
    monto_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, help_text="Representa el NETO de la línea")
    es_opcional = models.BooleanField(default=False, help_text='Para sugerencias adicionales al cliente')

    def save(self, *args, **kwargs):
        bruto_linea = Decimal(self.horas_estimadas) * Decimal(self.precio_unitario)
        self.subtotal = max(Decimal('0.00'), bruto_linea - Decimal(self.descuento))
        self.monto_iva = (self.subtotal * self.iva_porcentaje).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.descripcion} - {self.cotizacion.numero_cotizacion}'


class DetalleRepuestoCotizacion(BaseModel):
    """Repuestos requeridos para la cotización."""

    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='repuestos')
    codigo_repuesto = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.CharField(max_length=255)
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario_referencial = models.DecimalField(max_digits=10, decimal_places=2)
    
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.1500'))
    monto_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, help_text="Representa el NETO de la línea")
    es_opcional = models.BooleanField(default=False, help_text='Para sugerencias adicionales al cliente')

    def save(self, *args, **kwargs):
        bruto_linea = Decimal(self.cantidad) * Decimal(self.precio_unitario_referencial)
        self.subtotal = max(Decimal('0.00'), bruto_linea - Decimal(self.descuento))
        self.monto_iva = (self.subtotal * self.iva_porcentaje).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.descripcion} - {self.cotizacion.numero_cotizacion}'
