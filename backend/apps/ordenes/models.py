from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import BaseTallerModel


class TipoTrabajo(models.TextChoices):
    """Taxonomía única del motivo por el que el vehículo ingresa al taller.

    La comparten `RecepcionVehiculo.tipo_recepcion`, `InspeccionVehiculo.tipo_inspeccion`
    y `OrdenTrabajo.tipo_trabajo`: las tres responden a la misma pregunta de negocio
    ("¿por qué entra el vehículo?"), por lo que deben usar exactamente los mismos
    valores. Se registra en recepción y se hereda en inspección y orden de trabajo.
    """

    MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento'
    REPARACION = 'REPARACION', 'Reparación'
    DIAGNOSTICO = 'DIAGNOSTICO', 'Diagnóstico'
    ESTETICA = 'ESTETICA', 'Estética'
    GARANTIA = 'GARANTIA', 'Garantía'
    SINIESTRO = 'SINIESTRO', 'Siniestro'
    OTRO = 'OTRO', 'Otro'


class OrdenTrabajo(UuidPublicoModel, BaseTallerModel):
    """Modelo principal para la gestión de la reparación o mantenimiento del vehículo."""

    # Alias retrocompatible: el enum vive a nivel de módulo para poder reutilizarlo
    # desde otros apps (citas, cotizaciones) sin depender de esta clase.
    TipoTrabajo = TipoTrabajo

    class EstadoOrden(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        EN_ESPERA = 'EN_ESPERA', 'En espera'
        EN_PROCESO = 'EN_PROCESO', 'En proceso'
        COMPLETADO = 'COMPLETADO', 'Completado'
        ENTREGADO = 'ENTREGADO', 'Entregado'
        CANCELADO = 'CANCELADO', 'Anulado'

    class Prioridad(models.TextChoices):
        BAJA = 'BAJA', 'Baja'
        MEDIA = 'MEDIA', 'Media'
        ALTA = 'ALTA', 'Alta'
        URGENTE = 'URGENTE', 'Urgente / Emergencia'

    class TipoComprobante(models.TextChoices):
        FACTURA = 'FACTURA', 'Factura'
        NOTA_VENTA = 'NOTA_VENTA', 'Nota de venta'

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='ordenes_trabajo')
    sucursal = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_trabajo',
        verbose_name='Taller',
        help_text='Taller donde se atiende la orden (opcional)'
    )
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.PROTECT, related_name='ordenes_trabajo')
    vehiculo = models.ForeignKey('vehiculos.Vehiculo', on_delete=models.PROTECT, related_name='ordenes_trabajo')
    asesor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_asesoradas',
        help_text='Asesor o recepcionista a cargo de la atención'
    )
    mecanico_principal = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_asignadas',
        help_text='Mecánico o técnico responsable de la reparación'
    )
    orden_garantia_origen = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='ordenes_garantia',
    )
    cotizacion_origen = models.OneToOneField(
        'cotizaciones.Cotizacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orden_trabajo',
        help_text='Cotización previa de la cual se derivó esta orden (si aplica)'
    )

    numero_orden = models.CharField(max_length=20, verbose_name='Número de OT')
    estado = models.CharField(max_length=20, choices=EstadoOrden.choices, default=EstadoOrden.PENDIENTE)
    motivo_espera = models.CharField(
        max_length=80,
        blank=True,
        null=True,
        verbose_name='Motivo de espera',
        help_text='Razón de la espera cuando la orden está pausada (repuestos, aprobación del cliente, trabajo adicional, etc.)',
    )
    prioridad = models.CharField(max_length=10, choices=Prioridad.choices, default=Prioridad.MEDIA)
    tipo_trabajo = models.CharField(max_length=20, choices=TipoTrabajo.choices, default=TipoTrabajo.MANTENIMIENTO)

    observaciones = models.TextField(blank=True, null=True, help_text='Notas visibles para el cliente')

    # Editable: el usuario puede registrar la fecha y hora reales de ingreso.
    fecha_ingreso = models.DateTimeField(default=timezone.now)
    fecha_entrega = models.DateTimeField(blank=True, null=True)

    # Lectura del odómetro de la visita: obligatoria para el historial del
    # vehículo. Si no llega, se toma de la recepción/inspección o del vehículo.
    kilometraje = models.PositiveIntegerField(
        verbose_name='Kilometraje',
        help_text='Lectura del odómetro del vehículo en esta orden de trabajo',
    )

    # Entrega a crédito: saldo que quedó pendiente al entregar y quién la autorizó.
    saldo_al_entregar = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Saldo pendiente al entregar',
    )
    entrega_credito_autorizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='entregas_credito_autorizadas',
        verbose_name='Entrega a crédito autorizada por',
    )

    # Comprobante emitido fuera del sistema (facturador del SRI o talonario).
    tipo_comprobante = models.CharField(
        max_length=12, choices=TipoComprobante.choices, blank=True, default='',
        verbose_name='Tipo de comprobante',
    )
    numero_factura = models.CharField(
        max_length=17, blank=True, default='', verbose_name='Número de comprobante',
        help_text='Formato SRI: 001-001-000000123',
    )
    fecha_factura = models.DateField(null=True, blank=True, verbose_name='Fecha del comprobante')

    subtotal_servicios = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_repuestos = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_base_0 = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_base_gravada = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_neto = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    monto_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        verbose_name = 'Orden de Trabajo'
        verbose_name_plural = 'Órdenes de Trabajo'
        ordering = ['-created_at']
        constraints = [
            # Completa a propósito (sin condición de borrado): un número de OT
            # emitido nunca se reutiliza, aunque la OT se elimine lógicamente.
            models.UniqueConstraint(
                fields=['empresa', 'sucursal', 'numero_orden'],
                name='ot_empresa_sucursal_numero_unico'
            ),
            # Un comprobante externo corresponde a una sola OT viva de la empresa.
            # Factura y nota de venta llevan secuencias distintas en el SRI.
            models.UniqueConstraint(
                fields=['empresa', 'tipo_comprobante', 'numero_factura'],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(numero_factura=''),
                name='ot_comprobante_unico_vivo',
            ),
        ]
        indexes = [
            models.Index(
                fields=['empresa', 'sucursal', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='ot_tal_vivos_idx'
            ),
            # Vista "Todos los talleres" (sin filtro de sucursal): sin este
            # índice el listado une y ordena todas las filas de la empresa.
            models.Index(
                fields=['empresa', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='ot_emp_vivos_idx'
            ),
        ]

    def __str__(self):
        return f'OT {self.numero_orden} - {self.vehiculo.placa} ({self.get_estado_display()})'

    @staticmethod
    def kilometraje_de_visita(vehiculo=None, recepcion=None, inspeccion=None):
        """Lectura más alta registrada en la visita; si no hay, la del vehículo."""
        lecturas = [
            km for km in (
                getattr(recepcion, 'kilometraje_ingreso', None),
                getattr(inspeccion, 'kilometraje_diagnostico', None),
            )
            if km is not None
        ]
        if lecturas:
            return max(lecturas)
        return getattr(vehiculo, 'kilometraje_actual', None) or 0

    def save(self, *args, **kwargs):
        if self.kilometraje is None and self.vehiculo_id:
            self.kilometraje = self.kilometraje_de_visita(self.vehiculo)
        # El odómetro maestro solo sube (misma regla que la inspección).
        if self.vehiculo_id and self.kilometraje is not None:
            vehiculo = self.vehiculo
            if self.kilometraje > (vehiculo.kilometraje_actual or 0):
                vehiculo.kilometraje_actual = self.kilometraje
                vehiculo.save(update_fields=['kilometraje_actual', 'updated_at'])
        super().save(*args, **kwargs)


    def calcular_totales(self):
        # Las propuestas conservan sus importes, pero no participan del cobro.
        servicios = [linea for linea in self.servicios.all() if linea.aprobado]
        repuestos = [linea for linea in self.repuestos.all() if linea.aprobado]
        lineas = servicios + repuestos
        cero = Decimal('0.00')
        self.subtotal_servicios = sum((linea.subtotal for linea in servicios), cero)
        self.subtotal_repuestos = sum((linea.subtotal for linea in repuestos), cero)
        self.descuento = sum((linea.descuento for linea in lineas), cero)
        self.subtotal_neto = self.subtotal_servicios + self.subtotal_repuestos
        self.subtotal_base_0 = sum((linea.subtotal for linea in lineas if linea.iva_porcentaje == 0), cero)
        self.subtotal_base_gravada = sum((linea.subtotal for linea in lineas if linea.iva_porcentaje > 0), cero)
        self.monto_iva = sum((linea.monto_iva for linea in lineas), cero)
        self.total = self.subtotal_neto + self.monto_iva
        from apps.pagos.services import validar_total_cubre_pagos
        validar_total_cubre_pagos(self)
        self.save()


class LineaAdicional(models.Model):
    """La aprobación pertenece a la versión comercial de una línea adicional."""

    class Medio(models.TextChoices):
        WHATSAPP = 'WHATSAPP', 'WhatsApp'
        LLAMADA = 'LLAMADA', 'Llamada'
        PRESENCIAL = 'PRESENCIAL', 'Presencial'
        EMAIL = 'EMAIL', 'Correo'
        OTRO = 'OTRO', 'Otro'

    es_adicional = models.BooleanField(default=False)
    aprobado = models.BooleanField(default=True)
    medio_aprobacion = models.CharField(max_length=12, choices=Medio.choices, blank=True, default='')
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        abstract = True

    @property
    def version_aprobacion(self):
        from .adicionales import version_linea
        return version_linea(self)

    def save(self, *args, **kwargs):
        if self._state.adding and self.es_adicional:
            self.aprobado = False
        super().save(*args, **kwargs)


class DetalleServicioOrdenTrabajo(UuidPublicoModel, LineaAdicional):
    """Líneas de mano de obra aplicadas en la orden."""

    detalle_cotizacion_origen = models.OneToOneField(
        'cotizaciones.DetalleServicioCotizacion', on_delete=models.PROTECT,
        null=True, blank=True, related_name='detalle_orden_trabajo',
    )
    orden_trabajo = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='servicios')
    descripcion = models.CharField(max_length=255, verbose_name='Descripción del Servicio')
    mecanico_asignado = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Técnico que ejecutó el servicio')
    horas_aplicadas = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=10,decimal_places=2,default=Decimal('0.00'))
    costo_total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    margen_linea = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.1500'))
    monto_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    completado = models.BooleanField(default=False)
    servicio = models.ForeignKey(
        'inventario.Servicio',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='detalles_ot',
        verbose_name='Servicio del catálogo',
    )
    garantia_dias = models.PositiveIntegerField(null=True, default=None)
    garantia_km = models.PositiveIntegerField(null=True, default=None)
    servicio_codigo = models.CharField(max_length=50, blank=True, null=True)
    es_subcontratado = models.BooleanField(default=False)
    proveedor = models.ForeignKey(
        'proveedores.Proveedor', on_delete=models.PROTECT, null=True, blank=True,
        related_name='trabajos_subcontratados',
    )
    cantidad = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1.00'))

    def save(self, *args, **kwargs):
        if self._state.adding:
            from apps.inventario.models import Servicio
            servicio = self.servicio if self.servicio_id else None
            if servicio is None and self.servicio_codigo:
                servicio = Servicio.objects.filter(empresa_id=self.orden_trabajo.empresa_id, codigo=self.servicio_codigo.strip().upper(), is_active=True).first()
            for campo in ('garantia_dias', 'garantia_km'):
                if getattr(self, campo) is None:
                    setattr(self, campo, getattr(servicio, campo, 0))
        if self.orden_trabajo.tipo_trabajo == 'GARANTIA':
            self.precio_unitario = self.descuento = Decimal('0.00')
            self.garantia_dias = self.garantia_km = 0
        # 1. Calcular el bruto total de la línea
        unidades = Decimal(self.cantidad if self.es_subcontratado else self.horas_aplicadas)
        bruto_linea = unidades * Decimal(self.precio_unitario)
        # 2. Restar el descuento asignado a esta línea
        self.subtotal = max(Decimal('0.00'), bruto_linea - Decimal(self.descuento))
        # 3. El IVA se calcula directamente sobre el subtotal neto de la línea
        self.monto_iva = (self.subtotal * self.iva_porcentaje).quantize(Decimal('0.01'))
        self.costo_total = (unidades * Decimal(self.costo_unitario)).quantize(Decimal('0.01'))
        self.margen_linea = (self.subtotal - self.costo_total).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)


    def __str__(self):
        return f'{self.descripcion} - {self.orden_trabajo.numero_orden}'


class DetalleRepuestoOrdenTrabajo(UuidPublicoModel, LineaAdicional):
    """Líneas de repuestos consumidos en la orden."""

    provisto_cliente = models.BooleanField(default=False)
    contifico_producto_id = models.CharField(max_length=100, blank=True, null=True)
    detalle_cotizacion_origen = models.OneToOneField(
        'cotizaciones.DetalleRepuestoCotizacion', on_delete=models.PROTECT,
        null=True, blank=True, related_name='detalle_orden_trabajo',
    )
    orden_trabajo = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='repuestos')
    codigo_repuesto = models.CharField(max_length=50, blank=True, null=True)
    # Repuesto del catálogo: solo las líneas con repuesto mueven stock al
    # despacharse. Si llega solo el código, `save()` lo resuelve.
    repuesto = models.ForeignKey(
        'inventario.Repuesto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='detalles_ot', verbose_name='Repuesto del catálogo',
    )
    cantidad_despachada = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    fecha_despacho = models.DateTimeField(null=True, blank=True, verbose_name='Último despacho')
    despachado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='repuestos_despachados',
    )
    descripcion = models.CharField(max_length=255, verbose_name='Descripción del Repuesto')
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    costo_total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    margen_linea = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cantidad = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1.00'))
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.1500'))
    monto_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    def save(self, *args, **kwargs):
        if self.orden_trabajo.tipo_trabajo == 'GARANTIA':
            self.precio_unitario = self.descuento = Decimal('0.00')
        if self.provisto_cliente:
            self.precio_unitario = self.costo_unitario = self.descuento = Decimal('0.00')
        # 1. Calcular el bruto total de la línea
        bruto_linea = Decimal(self.cantidad) * Decimal(self.precio_unitario)

        # 2. Restar el descuento asignado a esta línea
        self.subtotal = max(Decimal('0.00'), bruto_linea - Decimal(self.descuento))

        # 3. El IVA se calcula directamente sobre el subtotal neto de la línea
        self.monto_iva = (self.subtotal * self.iva_porcentaje).quantize(Decimal('0.01'))

        self.costo_total = (Decimal(self.cantidad) * Decimal(self.costo_unitario)).quantize(Decimal('0.01'))
        self.margen_linea = (self.subtotal - self.costo_total).quantize(Decimal('0.01'))

        self.resolver_repuesto()
        super().save(*args, **kwargs)

    def resolver_repuesto(self):
        """Vincula la línea al catálogo por su código (las líneas de cotización
        y las antiguas solo traen el código)."""
        if self.provisto_cliente or self.repuesto_id or not self.codigo_repuesto or not self.orden_trabajo_id:
            return
        from apps.inventario.models import Repuesto

        self.repuesto = Repuesto.objects.filter(
            empresa_id=self.orden_trabajo.empresa_id, codigo=self.codigo_repuesto.strip().upper(),
        ).first()

    @property
    def cantidad_pendiente_despacho(self):
        if self.provisto_cliente:
            return Decimal('0.00')
        return max(Decimal(self.cantidad) - Decimal(self.cantidad_despachada), Decimal('0.00'))

    def __str__(self):
        return f'{self.descripcion} - {self.orden_trabajo.numero_orden}'


class RecepcionVehiculo(UuidPublicoModel, BaseTallerModel):
    """Registro del estado físico y administrativo del vehículo al ingresar al taller.

    Puede existir independientemente de una Orden de Trabajo.
    """
    # --- ESTADO Y ACEPTACIÓN DE LA RECEPCIÓN ---
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('ACEPTADA', 'Aceptada'),
        ('NO_ACEPTADA', 'Rechazada'),
    ]

    # Misma taxonomía que Inspección y Orden de Trabajo (ver TipoTrabajo).
    TIPOS_CHOICES = TipoTrabajo.choices

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='recepciones',
        null=True,
        blank=True,
    )
    sucursal = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recepciones',
        verbose_name='Taller',
        help_text='Taller donde ingresó el vehículo (opcional)',
    )
    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.PROTECT,
        related_name='recepciones',
    )
    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo',
        on_delete=models.PROTECT,
        related_name='recepciones',
    )
    orden_trabajo = models.ForeignKey(
        'ordenes.OrdenTrabajo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recepciones',
        help_text='Orden de trabajo asociada (si ya fue generada)',
    )
    recibido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recepciones_recibidas',
        verbose_name='Recibido por',
        help_text='Empleado que realizó la recepción del vehículo',
    )

    # --- NÚMERO DE RECEPCIÓN (secuencia configurable por taller) ---
    numero_recepcion = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Número de Recepción',
        help_text='Código secuencial asignado automáticamente desde el taller',
    )

    # --- TIPO Y MOTIVO DE INGRESO ---
    tipo_recepcion = models.CharField(
        max_length=20,
        choices=TIPOS_CHOICES,
        default=TipoTrabajo.MANTENIMIENTO,
        verbose_name='Tipo de Recepción',
    )
    motivo_ingreso = models.TextField(
        blank=True,
        null=True,
        verbose_name='Motivo de Ingreso',
        help_text='Razón por la cual el cliente trae el vehículo',
    )

    # --- FECHAS Y DATOS OPERATIVOS ---
    fecha_ingreso = models.DateTimeField(
        default=timezone.now, verbose_name='Fecha de Ingreso al Taller'
    )
    fecha_salida = models.DateTimeField(
        null=True, blank=True, verbose_name='Fecha de Salida del Taller'
    )

    kilometraje_ingreso = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Kilometraje de Ingreso',
        help_text='Opcional cuando el cliente no deja el vehículo',
    )
    nivel_combustible = models.CharField(
        max_length=10,
        choices=[
            ('VACIO', 'Vacíio'),
            ('RESERVA', 'Reserva'),
            ('1/4', '1/4'),
            ('1/2', '1/2'),
            ('3/4', '3/4'),
            ('LLENO', 'Lleno'),
        ],
        default='1/4',
    )

    ingreso_en_grua = models.BooleanField(
        default=False, verbose_name='¿Ingresó en grúa?'
    )
    datos_grua = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        verbose_name='Datos de la grúa / Chófer',
    )

    # --- CUSTODIA / LL LAVES Y PERTENENCIAS ---
    cantidad_llaves = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Cantidad de llaves',
        help_text='Cantidad de llaves del vehículo entregadas por el cliente',
    )
    tiene_llave_control = models.BooleanField(
        default=False, verbose_name='Llave de control / Control remoto'
    )
    pertenencia_celular = models.BooleanField(default=False, verbose_name='Celular')
    pertenencia_billetera = models.BooleanField(default=False, verbose_name='Billetera')
    pertenencia_dinero = models.BooleanField(default=False, verbose_name='Dinero en efectivo')
    pertenencia_documentos = models.BooleanField(default=False, verbose_name='Documentos personales')
    pertenencia_gafas = models.BooleanField(default=False, verbose_name='Gafas / Lentes')
    pertenencia_equipaje = models.BooleanField(default=False, verbose_name='Equipaje / Bolsos')
    pertenencia_otros = models.BooleanField(default=False, verbose_name='Otros objetos de valor')
    pertenencias_observaciones = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Observaciones de pertenencias',
        help_text='Detalles adicionales sobre objetos de valor dejados en el vehículo',
    )

    # --- SEGURO / SINIESTROS ---
    compania_seguro = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name='Compañía de seguro',
    )
    numero_poliza = models.CharField(
        max_length=80,
        blank=True,
        null=True,
        verbose_name='Número de póliza',
    )
    numero_reclamo = models.CharField(
        max_length=80,
        blank=True,
        null=True,
        verbose_name='Número de reclamo / siniestro',
    )
    ajustador_nombre = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name='Nombre del ajustador',
    )
    ajustador_telefono = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        verbose_name='Teléfono del ajustador',
    )

    # --- TESTIGOS DEL TABLERO AL INGRESO ---
    testigo_check_engine = models.BooleanField(default=False, verbose_name='Check Engine')
    testigo_abs = models.BooleanField(default=False, verbose_name='ABS')
    testigo_airbag = models.BooleanField(default=False, verbose_name='Airbag')
    testigo_bateria = models.BooleanField(default=False, verbose_name='Batería')
    testigo_aceite = models.BooleanField(default=False, verbose_name='Presión de Aceite')
    testigo_temperatura = models.BooleanField(default=False, verbose_name='Temperatura / Refrigerante')
    testigo_presion_llantas = models.BooleanField(default=False, verbose_name='Presión de Llantas')
    testigo_desempanado = models.BooleanField(default=False, verbose_name='Desempañado del Parabrisas')
    testigo_limpiaparabrisas = models.BooleanField(default=False, verbose_name='Limpiaparabrisas')
    testigo_luces_largas = models.BooleanField(default=False, verbose_name='Luces Largas')
    testigo_combustible_bajo = models.BooleanField(default=False, verbose_name='Nivel Bajo de Combustible')
    testigo_antiniebla_traseras = models.BooleanField(default=False, verbose_name='Luces Antiniebla Traseras')
    testigo_esp = models.BooleanField(default=False, verbose_name='Control de Estabilidad (ESP)')
    testigo_bujias_precalentamiento = models.BooleanField(default=False, verbose_name='Bujías de Precalentamiento')
    testigo_pedal_freno = models.BooleanField(default=False, verbose_name='Pedal de Freno')
    testigo_luces_emergencia = models.BooleanField(default=False, verbose_name='Luces de Emergencia')
    testigo_puerta_maletero = models.BooleanField(default=False, verbose_name='Puerta o Maletero Abierto')
    testigo_cinturon = models.BooleanField(default=False, verbose_name='Cinturón Desabrochado')
    testigo_freno_estacionamiento = models.BooleanField(default=False, verbose_name='Freno de Estacionamiento')
    testigo_frenos_fallo = models.BooleanField(default=False, verbose_name='Fallo en el Sistema de Frenos')
    testigo_ev_listo = models.BooleanField(default=False, verbose_name='Vehículo Eléctrico Listo (READY)')
    testigo_potencia_baja = models.BooleanField(default=False, verbose_name='Potencia Baja (EV)')
    testigo_carga_ev = models.BooleanField(default=False, verbose_name='Carga del Vehículo Eléctrico')
    testigo_bateria_alto_voltaje = models.BooleanField(default=False, verbose_name='Nivel Bajo de Batería de Alto Voltaje')
    testigo_servicio_ev = models.BooleanField(default=False, verbose_name='Advertencia de Servicio (EV)')
    testigo_dpf = models.BooleanField(default=False, verbose_name='Filtro de Partículas Diésel (DPF)')
    testigo_antirrobo = models.BooleanField(default=False, verbose_name='Sistema Antirrobo')
    testigo_precolision = models.BooleanField(default=False, verbose_name='Asistente de Precolisión')
    testigo_salida_carril = models.BooleanField(default=False, verbose_name='Salida de Carril')
    testigo_punto_ciego = models.BooleanField(default=False, verbose_name='Punto Ciego')
    otros_testigos_observaciones = models.CharField(max_length=255, blank=True, null=True, verbose_name='Otros Testigos u Observaciones del Tablero')

    # --- INVENTARIO / CHECKLIST (Columna 1 Hoja Física) ---
    tiene_espejo_izquierdo = models.BooleanField(default=False, verbose_name='Espejo Izquierdo')
    tiene_espejo_derecho = models.BooleanField(default=False, verbose_name='Espejo Derecho')
    tiene_vidrios = models.BooleanField(default=False, verbose_name='Vidrios / Cristales')
    tiene_radio = models.BooleanField(default=False, verbose_name='Radio / Mascarilla')
    tiene_pantalla = models.BooleanField(default=False, verbose_name='Pantalla / Multimedia')
    tiene_encendedor = models.BooleanField(default=False, verbose_name='Encendedor')
    tiene_antena = models.BooleanField(default=False, verbose_name='Antena')
    tiene_control_puertas = models.BooleanField(default=False, verbose_name='Control de Puertas')
    tiene_cargador_celular = models.BooleanField(default=False, verbose_name='Cargador de Celular')
    tiene_triangulos = models.BooleanField(default=False, verbose_name='Triángulos de Seguridad')

    # --- INVENTARIO / CHECKLIST (Columna 2 Hoja Física) ---
    tiene_cubresol = models.BooleanField(default=False, verbose_name='Cubresol')
    tiene_herramientas = models.BooleanField(default=False, verbose_name='Juego de Herramientas')
    tiene_gata_palanca = models.BooleanField(default=False, verbose_name='Gato y Palanca')
    tiene_llanta_repuesto = models.BooleanField(default=False, verbose_name='Llanta de Refacción / Repuesto')
    tiene_faros_lunas = models.BooleanField(default=False, verbose_name='Faros / Lunas')
    tiene_tapa_gasolina = models.BooleanField(default=False, verbose_name='Tapa de Gasolina')
    tiene_placas = models.BooleanField(default=False, verbose_name='Placas de Circulación')
    tiene_tapetes = models.BooleanField(default=False, verbose_name='Tapetes / Alfombras')
    tiene_extintor = models.BooleanField(default=False, verbose_name='Extintor')
    tiene_botiquin = models.BooleanField(default=False, verbose_name='Botiquín')
    tiene_copas_ruedas = models.BooleanField(default=False, verbose_name='Copas / Tapacubos')
    tiene_llave_tuercas = models.BooleanField(default=False, verbose_name='Llave de Tuercas / Llave de Cruz')

    # --- ESTADO FÍSICO Y OBSERVACIONES ---
    # Guarda puntos x,y/daños marcados en la silueta interactiva del frontend
    datos_danos_carroceria = models.JSONField(
        blank=True,
        null=True,
        help_text='Puntos de daños marcados sobre el gráfico del vehículo',
    )
    detalles_carroceria = models.TextField(
        blank=True,
        null=True,
        verbose_name='Observaciones / Descripción de Golpes, Rayones o Estado de Pintura',
    )
    firma_cliente = models.ImageField(
        upload_to='ordenes/firmas/',
        blank=True,
        null=True,
        verbose_name='Firma del cliente',
        help_text='Firma digital del cliente aceptando la recepción (PNG)',
    )
    fecha_firma_cliente = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha y hora de firma del cliente',
    )
    aceptacion_condiciones = models.BooleanField(
        default=False,
        verbose_name='Aceptación de condiciones',
        help_text='Indica si el cliente aceptó las condiciones de recepción y estado del vehículo',
    )
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='PENDIENTE',
        verbose_name='Estado de la recepción',
        help_text='Estado de la recepción respecto a la firma y aceptación del cliente',
    )
    motivo_no_recepcion = models.TextField(
        blank=True,
        null=True,
        verbose_name='Motivo de no recepción',
        help_text='Observaciones cuando el cliente no aceptó las condiciones, no firmó o decidió no dejar el vehículo',
    )

    class Meta:
        verbose_name = 'Recepción de Vehículo'
        verbose_name_plural = 'Recepciones de Vehículos'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'sucursal', 'numero_recepcion'],
                name='recepcion_empresa_sucursal_numero_unico'
            )
        ]
        indexes = [
            models.Index(
                fields=['empresa', 'sucursal', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='recepcion_tal_vivos_idx'
            ),
            # Vista "Todos los talleres" (sin filtro de sucursal): sin este
            # índice el listado une y ordena todas las filas de la empresa.
            models.Index(
                fields=['empresa', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='recepcion_emp_vivos_idx'
            ),
        ]

    def __str__(self):
        if self.orden_trabajo_id:
            return f'Recepción OT {self.orden_trabajo.numero_orden}'
        return f'Recepción #{self.pk} - Sin OT vinculada'


class InspeccionVehiculo(UuidPublicoModel, BaseTallerModel):
    """Registro técnico del diagnóstico y estado mecánico del vehículo.
    Puede existir independientemente de una Orden de Trabajo."""

    # Misma taxonomía que Recepción y Orden de Trabajo (ver TipoTrabajo).
    TIPO_CHOICES = TipoTrabajo.choices
    
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('EN_PROCESO', 'En Proceso'),
        ('FINALIZADA', 'Finalizada'),
    ]

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='inspecciones', null=True, blank=True)
    sucursal = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspecciones',
        verbose_name='Taller',
        help_text='Taller donde se realizó la inspección (opcional)'
    )
    orden_trabajo = models.OneToOneField(
        'ordenes.OrdenTrabajo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspeccion',
        help_text='Orden de trabajo asociada (si ya fue generada)'
    )
    recepcion = models.ForeignKey(
        'ordenes.RecepcionVehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspecciones',
        help_text='Recepción del vehículo de la cual se derivó esta inspección'
    )
    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspecciones',
        help_text='Cliente asociado cuando la inspección no proviene de una recepción'
    )
    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspecciones',
        help_text='Vehículo asociado cuando la inspección no proviene de una recepción'
    )
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspecciones_responsables',
        verbose_name='Responsable',
        help_text='Mecánico o técnico responsable de la inspección'
    )

    # Número de inspección (secuencia configurable por taller)
    numero_inspeccion = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Número de Inspección',
        help_text='Código secuencial asignado automáticamente desde el taller',
    )

    # Clasificación y Estado
    tipo_inspeccion = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TipoTrabajo.DIAGNOSTICO)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='PENDIENTE')

    # Contexto del Cliente (Adaptado para incluir mantenimientos sin fallas)
    motivo_ingreso = models.TextField(verbose_name='Motivo de Ingreso o Falla Reportada', help_text='Ej: Ruido en el motor, o Mantenimiento 10k')
    
    # Diagnóstico Técnico
    codigos_dtc = models.CharField(max_length=255, blank=True, null=True, verbose_name='Códigos de Falla (DTC OBD2)')
    diagnostico_tecnico = models.TextField(blank=True, null=True, verbose_name='Hallazgos y Diagnóstico del Mecánico')
    recomendaciones = models.TextField(blank=True, null=True, verbose_name='Recomendaciones y Plan de Acción')

    # Testigos del Tablero
    testigo_check_engine = models.BooleanField(default=False, verbose_name='Check Engine')
    testigo_abs = models.BooleanField(default=False, verbose_name='ABS')
    testigo_airbag = models.BooleanField(default=False, verbose_name='Airbag')
    testigo_bateria = models.BooleanField(default=False, verbose_name='Batería')
    testigo_aceite = models.BooleanField(default=False, verbose_name='Presión de Aceite')
    testigo_temperatura = models.BooleanField(default=False, verbose_name='Temperatura / Refrigerante')
    testigo_presion_llantas = models.BooleanField(default=False, verbose_name='Presión de Llantas')
    testigo_desempanado = models.BooleanField(default=False, verbose_name='Desempañado del Parabrisas')
    testigo_limpiaparabrisas = models.BooleanField(default=False, verbose_name='Limpiaparabrisas')
    testigo_luces_largas = models.BooleanField(default=False, verbose_name='Luces Largas')
    testigo_combustible_bajo = models.BooleanField(default=False, verbose_name='Nivel Bajo de Combustible')
    testigo_antiniebla_traseras = models.BooleanField(default=False, verbose_name='Luces Antiniebla Traseras')
    testigo_esp = models.BooleanField(default=False, verbose_name='Control de Estabilidad (ESP)')
    testigo_bujias_precalentamiento = models.BooleanField(default=False, verbose_name='Bujías de Precalentamiento')
    testigo_pedal_freno = models.BooleanField(default=False, verbose_name='Pedal de Freno')
    testigo_luces_emergencia = models.BooleanField(default=False, verbose_name='Luces de Emergencia')
    testigo_puerta_maletero = models.BooleanField(default=False, verbose_name='Puerta o Maletero Abierto')
    testigo_cinturon = models.BooleanField(default=False, verbose_name='Cinturón Desabrochado')
    testigo_freno_estacionamiento = models.BooleanField(default=False, verbose_name='Freno de Estacionamiento')
    testigo_frenos_fallo = models.BooleanField(default=False, verbose_name='Fallo en el Sistema de Frenos')
    testigo_ev_listo = models.BooleanField(default=False, verbose_name='Vehículo Eléctrico Listo (READY)')
    testigo_potencia_baja = models.BooleanField(default=False, verbose_name='Potencia Baja (EV)')
    testigo_carga_ev = models.BooleanField(default=False, verbose_name='Carga del Vehículo Eléctrico')
    testigo_bateria_alto_voltaje = models.BooleanField(default=False, verbose_name='Nivel Bajo de Batería de Alto Voltaje')
    testigo_servicio_ev = models.BooleanField(default=False, verbose_name='Advertencia de Servicio (EV)')
    testigo_dpf = models.BooleanField(default=False, verbose_name='Filtro de Partículas Diésel (DPF)')
    testigo_antirrobo = models.BooleanField(default=False, verbose_name='Sistema Antirrobo')
    testigo_precolision = models.BooleanField(default=False, verbose_name='Asistente de Precolisión')
    testigo_salida_carril = models.BooleanField(default=False, verbose_name='Salida de Carril')
    testigo_punto_ciego = models.BooleanField(default=False, verbose_name='Punto Ciego')
    otros_testigos_observaciones = models.CharField(max_length=255, blank=True, null=True, verbose_name='Otros Testigos u Observaciones del Tablero')

    fecha_inspeccion = models.DateTimeField(
        default=timezone.now,
        verbose_name='Fecha de inspección',
        help_text='Momento real en que se realizó (o se inició) el diagnóstico'
    )

    fecha_finalizacion = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha de finalización',
        help_text='Se registra automáticamente al finalizar la inspección y se limpia al reabrirla'
    )

    # Lectura del odómetro en el momento del diagnóstico. El vehículo puede salir a
    # prueba de ruta, así que no siempre coincide con el kilometraje de la recepción.
    # Al finalizar, si es mayor, se propaga a `Vehiculo.kilometraje_actual`.
    kilometraje_diagnostico = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name='Kilometraje de diagnóstico',
        help_text='Lectura del odómetro al momento del diagnóstico, después de la prueba de ruta si la hubo',
    )

    class Meta:
        verbose_name = 'Inspección de Vehículo'
        verbose_name_plural = 'Inspecciones de Vehículos'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['recepcion'],
                condition=models.Q(recepcion__isnull=False, deleted_at__isnull=True),
                name='una_inspeccion_viva_por_recepcion'
            ),
            models.UniqueConstraint(
                fields=['empresa', 'sucursal', 'numero_inspeccion'],
                name='inspeccion_empresa_sucursal_numero_unico'
            )
        ]
        indexes = [
            models.Index(
                fields=['empresa', 'sucursal', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='inspeccion_tal_vivos_idx'
            ),
            # Vista "Todos los talleres" (sin filtro de sucursal): sin este
            # índice el listado une y ordena todas las filas de la empresa.
            models.Index(
                fields=['empresa', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='inspeccion_emp_vivos_idx'
            ),
        ]

    def __str__(self):
        if self.orden_trabajo_id:
            return f'Inspección OT {self.orden_trabajo.numero_orden}'
        if self.recepcion_id:
            return f'Inspección #{self.pk} - Recepción {self.recepcion_id}'
        return f'Inspección #{self.pk} - Sin vínculo'

    @property
    def duracion_inspeccion(self):
        """Minutos entre el inicio del diagnóstico y su cierre."""
        if not self.fecha_finalizacion or not self.fecha_inspeccion:
            return None
        return max(0, int((self.fecha_finalizacion - self.fecha_inspeccion).total_seconds() // 60))

    def _sincronizar_kilometraje_vehiculo(self):
        """Propaga la lectura del diagnóstico al odómetro maestro del vehículo.

        El maestro solo sube: una lectura menor es un error de captura, no un
        retroceso real del odómetro.
        """
        if not self.vehiculo_id or self.kilometraje_diagnostico is None:
            return
        vehiculo = self.vehiculo
        if self.kilometraje_diagnostico > (vehiculo.kilometraje_actual or 0):
            vehiculo.kilometraje_actual = self.kilometraje_diagnostico
            vehiculo.save(update_fields=['kilometraje_actual', 'updated_at'])

    def save(self, *args, **kwargs):
        # Al crearla se precarga el odómetro vigente del vehículo; después es una
        # lectura propia del diagnóstico y no debe volver a sobrescribirse.
        if self._state.adding and self.vehiculo_id and self.kilometraje_diagnostico is None:
            self.kilometraje_diagnostico = self.vehiculo.kilometraje_actual

        # La fecha de cierre la gobierna el sistema, nunca el cliente.
        if self.estado == 'FINALIZADA':
            if self.fecha_finalizacion is None:
                self.fecha_finalizacion = timezone.now()
        else:
            self.fecha_finalizacion = None

        if self.fecha_inspeccion is None:
            self.fecha_inspeccion = timezone.now()

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {
                'fecha_inspeccion',
                'fecha_finalizacion',
                'kilometraje_diagnostico',
            }

        # Solo se consolida el odómetro cuando el diagnóstico queda confirmado.
        if self.estado == 'FINALIZADA':
            self._sincronizar_kilometraje_vehiculo()

        super().save(*args, **kwargs)


class DetalleServicioInspeccion(UuidPublicoModel, models.Model):
    """Servicios detectados/recomendados durante la inspección técnica.

    El mecánico selecciona ítems desde el catálogo maestro de Servicios sin
    fijar precios comerciales ni generar compromiso de facturación. No maneja
    stock (los servicios son mano de obra).
    """

    class Prioridad(models.TextChoices):
        BAJA = 'BAJA', 'Baja'
        MEDIA = 'MEDIA', 'Media'
        ALTA = 'ALTA', 'Alta'

    inspeccion = models.ForeignKey(
        InspeccionVehiculo,
        on_delete=models.CASCADE,
        related_name='servicios_detectados',
    )
    servicio = models.ForeignKey(
        'inventario.Servicio',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='detalles_inspeccion',
        verbose_name='Servicio del catálogo',
    )
    descripcion = models.CharField(max_length=255, verbose_name='Servicio / Mano de obra detectado')
    horas_estimadas = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('1.00'),
        verbose_name='Horas estimadas',
    )
    precio_referencial = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Precio referencial',
        help_text='Valor referencial del catálogo; no fija precio comercial.',
    )
    es_sugerido = models.BooleanField(
        default=False,
        verbose_name='Es sugerido',
        help_text='Indica si es una recomendación opcional; por defecto es necesario/obligatorio',
    )
    prioridad = models.CharField(
        max_length=10,
        choices=Prioridad.choices,
        default=Prioridad.MEDIA,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Detalle de Servicio en Inspección'
        verbose_name_plural = 'Detalles de Servicios en Inspecciones'
        ordering = ['-prioridad', 'created_at']

    def __str__(self):
        return f'{self.descripcion} - Inspección #{self.inspeccion_id}'

    def save(self, *args, **kwargs):
        if self.servicio_id:
            if not self.descripcion:
                self.descripcion = self.servicio.nombre
            if not self.precio_referencial:
                self.precio_referencial = Decimal(self.servicio.precio_referencial or '0.00')
        super().save(*args, **kwargs)


class DetalleRepuestoInspeccion(UuidPublicoModel, models.Model):
    """Repuestos sugeridos durante la inspección técnica.

    El mecánico selecciona ítems desde el catálogo maestro de Repuestos sin
    fijar precios comerciales y SIN descontar stock todavía (el consumo real
    se descuenta al ejecutar la orden de trabajo).
    """

    class Prioridad(models.TextChoices):
        BAJA = 'BAJA', 'Baja'
        MEDIA = 'MEDIA', 'Media'
        ALTA = 'ALTA', 'Alta'

    inspeccion = models.ForeignKey(
        InspeccionVehiculo,
        on_delete=models.CASCADE,
        related_name='repuestos_sugeridos',
    )
    repuesto = models.ForeignKey(
        'inventario.Repuesto',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='detalles_inspeccion',
        verbose_name='Repuesto del catálogo',
    )
    descripcion = models.CharField(max_length=255, verbose_name='Repuesto sugerido')
    cantidad = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('1.00'),
        verbose_name='Cantidad estimada',
    )
    precio_referencial = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Precio referencial',
        help_text='Valor referencial del catálogo; no fija precio comercial ni descuenta stock.',
    )
    es_sugerido = models.BooleanField(
        default=False,
        verbose_name='Es sugerido',
        help_text='Indica si es una recomendación opcional; por defecto es necesario/obligatorio',
    )
    prioridad = models.CharField(
        max_length=10,
        choices=Prioridad.choices,
        default=Prioridad.MEDIA,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Detalle de Repuesto en Inspección'
        verbose_name_plural = 'Detalles de Repuestos en Inspecciones'
        ordering = ['-prioridad', 'created_at']

    def __str__(self):
        return f'{self.descripcion} - Inspección #{self.inspeccion_id}'

    def save(self, *args, **kwargs):
        if self.repuesto_id:
            if not self.descripcion:
                self.descripcion = self.repuesto.nombre
            if not self.precio_referencial:
                self.precio_referencial = Decimal(self.repuesto.precio_venta or '0.00')
        super().save(*args, **kwargs)


class FotoRecepcion(UuidPublicoModel, models.Model):
    """Adjunta múltiples fotos de la recepción del vehículo."""

    class TipoVista(models.TextChoices):
        FRONTAL = 'FRONTAL', 'Frontal'
        LATERAL_IZQ = 'LATERAL_IZQ', 'Lateral Izquierda'
        LATERAL_DER = 'LATERAL_DER', 'Lateral Derecha'
        POSTERIOR = 'POSTERIOR', 'Posterior'
        TABLERO = 'TABLERO', 'Tablero / Kilometraje'

    VISTAS_OBLIGATORIAS = [
        TipoVista.FRONTAL,
        TipoVista.LATERAL_IZQ,
        TipoVista.LATERAL_DER,
        TipoVista.POSTERIOR,
        TipoVista.TABLERO,
    ]

    recepcion = models.ForeignKey(RecepcionVehiculo, on_delete=models.CASCADE, related_name='fotos')
    tipo_vista = models.CharField(
        max_length=30,
        choices=TipoVista.choices,
        verbose_name='Vista fotográfica'
    )
    imagen = models.ImageField(upload_to='ordenes/recepcion_fotos/')
    miniatura = models.ImageField(upload_to='ordenes/recepcion_fotos/miniaturas/', blank=True, null=True)
    descripcion = models.CharField(max_length=100, blank=True, null=True, verbose_name='Ej: Rayón puerta izquierda')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Foto de la Recepción'
        verbose_name_plural = 'Fotos de las Recepciones'
        ordering = ['tipo_vista', 'created_at']

    def __str__(self):
        return f'{self.recepcion_id} - {self.get_tipo_vista_display()}'


class FotoInspeccion(UuidPublicoModel, models.Model):
    """Adjunta hasta 5 fotos de evidencia de los hallazgos de la inspección."""

    MAX_FOTOS = 5

    inspeccion = models.ForeignKey(
        InspeccionVehiculo,
        on_delete=models.CASCADE,
        related_name='fotos',
        verbose_name='Inspección',
    )
    imagen = models.ImageField(upload_to='ordenes/inspeccion_fotos/')
    miniatura = models.ImageField(upload_to='ordenes/inspeccion_fotos/miniaturas/', blank=True, null=True)
    descripcion = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Foto de Inspección'
        verbose_name_plural = 'Fotos de Inspecciones'
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'Inspección #{self.inspeccion_id} - foto {self.pk}'


class FotoOrdenTrabajo(UuidPublicoModel, models.Model):
    """Adjunta hasta 5 fotos de evidencia del trabajo realizado en la orden."""

    MAX_FOTOS = 5

    orden_trabajo = models.ForeignKey(
        OrdenTrabajo,
        on_delete=models.CASCADE,
        related_name='fotos',
        verbose_name='Orden de Trabajo',
    )
    imagen = models.ImageField(upload_to='ordenes/orden_fotos/')
    miniatura = models.ImageField(upload_to='ordenes/orden_fotos/miniaturas/', blank=True, null=True)
    descripcion = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Foto de la Orden de Trabajo'
        verbose_name_plural = 'Fotos de las Ordenes de Trabajo'
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'OT #{self.orden_trabajo_id} - foto {self.pk}'