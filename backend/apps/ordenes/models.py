from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class OrdenTrabajo(BaseModel):
    """Modelo principal para la gestión de la reparación o mantenimiento del vehículo."""

    class TipoTrabajo(models.TextChoices):
        PREVENTIVO = 'PREVENTIVO', 'Mantenimiento Preventivo'
        CORRECTIVO = 'CORRECTIVO', 'Reparación Correctiva'
        DIAGNOSTICO = 'DIAGNOSTICO', 'Solo Diagnóstico / Escaneo'
        ESTETICA = 'ESTETICA', 'Enderezada, Pintura o Detailing'
        GARANTIA = 'GARANTIA', 'Garantía / Retorno'

    class EstadoOrden(models.TextChoices):
        INGRESADO = 'INGRESADO', 'En Recepción / Diagnóstico'
        EN_PROCESO = 'EN_PROCESO', 'En Trabajo / Ejecución'
        COMPLETADO = 'COMPLETADO', 'Trabajo Listo'
        ENTREGADO = 'ENTREGADO', 'Entregado y Cerrado'
        CANCELADO = 'CANCELADO', 'Anulado / Cancelado'

    class Prioridad(models.TextChoices):
        BAJA = 'BAJA', 'Baja'
        MEDIA = 'MEDIA', 'Media'
        ALTA = 'ALTA', 'Alta'
        URGENTE = 'URGENTE', 'Urgente / Emergencia'

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
    cotizacion_origen = models.OneToOneField(
        'cotizaciones.Cotizacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orden_trabajo',
        help_text='Cotización previa de la cual se derivó esta orden (si aplica)'
    )

    numero_orden = models.CharField(max_length=20, unique=True, verbose_name='Número de OT')
    estado = models.CharField(max_length=20, choices=EstadoOrden.choices, default=EstadoOrden.INGRESADO)
    prioridad = models.CharField(max_length=10, choices=Prioridad.choices, default=Prioridad.MEDIA)
    tipo_trabajo = models.CharField(max_length=20, choices=TipoTrabajo.choices, default=TipoTrabajo.PREVENTIVO)

    observaciones_internas = models.TextField(blank=True, null=True, help_text='Notas no visibles para el cliente')

    fecha_ingreso = models.DateTimeField(auto_now_add=True)
    fecha_entrega = models.DateTimeField(blank=True, null=True)

    subtotal_servicios = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_repuestos = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subtotal_neto = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    monto_iva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        verbose_name = 'Orden de Trabajo'
        verbose_name_plural = 'Órdenes de Trabajo'
        ordering = ['-created_at']

    def __str__(self):
        return f'OT {self.numero_orden} - {self.vehiculo.placa} ({self.get_estado_display()})'

    def calcular_totales(self):
        total_serv = sum(item.subtotal for item in self.servicios.all())
        total_rep = sum(item.subtotal for item in self.repuestos.all())

        self.subtotal_servicios = Decimal(total_serv)
        self.subtotal_repuestos = Decimal(total_rep)

        subtotal_bruto = self.subtotal_servicios + self.subtotal_repuestos
        self.subtotal_neto = max(Decimal('0.00'), subtotal_bruto - self.descuento)

        porcentaje_iva = Decimal('0.15')
        self.monto_iva = self.subtotal_neto * porcentaje_iva
        self.total = self.subtotal_neto + self.monto_iva
        self.save()


class DetalleServicioOrdenTrabajo(models.Model):
    """Líneas de mano de obra aplicadas en la orden."""

    orden_trabajo = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='servicios')
    descripcion = models.CharField(max_length=255, verbose_name='Descripción del Servicio')
    mecanico_asignado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Técnico que ejecutó el servicio'
    )
    horas_aplicadas = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    completado = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        self.subtotal = Decimal(self.horas_aplicadas) * Decimal(self.precio_unitario)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.descripcion} - {self.orden_trabajo.numero_orden}'


class DetalleRepuestoOrdenTrabajo(models.Model):
    """Líneas de repuestos consumidos en la orden."""

    orden_trabajo = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='repuestos')
    codigo_repuesto = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.CharField(max_length=255, verbose_name='Descripción del Repuesto')
    cantidad = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1.00'))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    contifico_producto_id = models.CharField(max_length=100, blank=True, null=True)

    def save(self, *args, **kwargs):
        self.subtotal = Decimal(self.cantidad) * Decimal(self.precio_unitario)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.descripcion} - {self.orden_trabajo.numero_orden}'


class RecepcionVehiculo(BaseModel):
    """Registro del estado físico y administrativo del vehículo al ingresar al taller.

    Puede existir independientemente de una Orden de Trabajo.
    """

    # --- RELACIONES ---
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

    # --- TIPO Y MOTIVO DE INGRESO ---
    tipo_recepcion = models.CharField(
        max_length=20,
        choices=OrdenTrabajo.TipoTrabajo.choices,
        default=OrdenTrabajo.TipoTrabajo.DIAGNOSTICO,
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

    # --- TESTIGOS DEL TABLERO AL INGRESO ---
    testigo_check_engine = models.BooleanField(default=False, verbose_name='Check Engine')
    testigo_abs = models.BooleanField(default=False, verbose_name='ABS')
    testigo_airbag = models.BooleanField(default=False, verbose_name='Airbag')
    testigo_bateria = models.BooleanField(default=False, verbose_name='Batería')
    testigo_aceite = models.BooleanField(default=False, verbose_name='Presión de Aceite')
    testigo_temperatura = models.BooleanField(default=False, verbose_name='Temperatura / Refrigerante')
    otros_testigos_observaciones = models.CharField(max_length=255, blank=True, null=True, verbose_name='Otros Testigos u Observaciones del Tablero')

    # --- INVENTARIO / CHECKLIST (Columna 1 Hoja Física) ---
    tiene_espejo_izquierdo = models.BooleanField(default=True, verbose_name='Espejo Izquierdo')
    tiene_espejo_derecho = models.BooleanField(default=True, verbose_name='Espejo Derecho')
    tiene_vidrios = models.BooleanField(default=True, verbose_name='Vidrios / Cristales')
    tiene_radio = models.BooleanField(default=True, verbose_name='Radio / Mascarilla')
    tiene_pantalla = models.BooleanField(default=False, verbose_name='Pantalla / Multimedia')
    tiene_encendedor = models.BooleanField(default=False, verbose_name='Encendedor')
    tiene_antena = models.BooleanField(default=True, verbose_name='Antena')
    tiene_control_puertas = models.BooleanField(default=False, verbose_name='Control de Puertas')
    tiene_cargador_celular = models.BooleanField(default=False, verbose_name='Cargador de Celular')
    tiene_triangulos = models.BooleanField(default=False, verbose_name='Triángulos de Seguridad')

    # --- INVENTARIO / CHECKLIST (Columna 2 Hoja Física) ---
    tiene_cubresol = models.BooleanField(default=False, verbose_name='Cubresol')
    tiene_herramientas = models.BooleanField(default=False, verbose_name='Juego de Herramientas')
    tiene_gata_palanca = models.BooleanField(default=True, verbose_name='Gato y Palanca')
    tiene_llanta_repuesto = models.BooleanField(default=True, verbose_name='Llanta de Refacción / Repuesto')
    tiene_faros_lunas = models.BooleanField(default=True, verbose_name='Faros / Lunas')
    tiene_tapa_gasolina = models.BooleanField(default=True, verbose_name='Tapa de Gasolina')
    tiene_placas = models.BooleanField(default=True, verbose_name='Placas de Circulación')
    tiene_tapetes = models.BooleanField(default=True, verbose_name='Tapetes / Alfombras')
    tiene_extintor = models.BooleanField(default=False, verbose_name='Extintor')
    tiene_botiquin = models.BooleanField(default=False, verbose_name='Botiquín')
    tiene_copas_ruedas = models.BooleanField(default=True, verbose_name='Copas / Tapacubos')
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
    firma_receptor = models.TextField(
        blank=True,
        null=True,
        verbose_name='Firma del receptor',
        help_text='Firma digital del empleado que recibió el vehículo (base64)',
    )
    firma_cliente = models.TextField(
        blank=True,
        null=True,
        verbose_name='Firma del cliente',
        help_text='Firma digital del cliente aceptando la recepción (base64)',
    )
    fecha_firma_receptor = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha y hora de firma del receptor',
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

    # --- ESTADO Y ACEPTACIÓN DE LA RECEPCIÓN ---
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente de Firma'),
        ('ACEPTADA', 'Aceptada y Firmada'),
        ('NO_ACEPTADA', 'No Aceptada / Sin Firma'),
    ]
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

    def __str__(self):
        if self.orden_trabajo_id:
            return f'Recepción OT {self.orden_trabajo.numero_orden}'
        return f'Recepción #{self.pk} - Sin OT vinculada'


class InspeccionVehiculo(BaseModel):
    """Registro técnico del diagnóstico y estado mecánico del vehículo.
    Puede existir independientemente de una Orden de Trabajo."""

    # Opciones de selección
    TIPO_CHOICES = [
        ('PREVENTIVO', 'Mantenimiento Preventivo'),
        ('CORRECTIVO', 'Reparación Correctiva'),
        ('DIAGNOSTICO', 'Solo Diagnóstico / Escaneo'),
        ('ESTETICA', 'Enderezada, Pintura o Detailing'),
        ('GARANTIA', 'Revisión por Garantía'),
    ]
    
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente de Revisión'),
        ('EN_PROCESO', 'Diagnóstico en Proceso'),
        ('FINALIZADA', 'Inspección Finalizada'),
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

    # Clasificación y Estado
    tipo_inspeccion = models.CharField(max_length=20, choices=OrdenTrabajo.TipoTrabajo.choices, default=OrdenTrabajo.TipoTrabajo.DIAGNOSTICO,)
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
    otros_testigos_observaciones = models.CharField(max_length=255, blank=True, null=True, verbose_name='Otros Testigos u Observaciones del Tablero')
    class Meta:
        verbose_name = 'Inspección de Vehículo'
        verbose_name_plural = 'Inspecciones de Vehículos'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['recepcion'],
                condition=models.Q(recepcion__isnull=False),
                name='una_inspeccion_por_recepcion'
            )
        ]

    def __str__(self):
        if self.orden_trabajo_id:
            return f'Inspección OT {self.orden_trabajo.numero_orden}'
        if self.recepcion_id:
            return f'Inspección #{self.pk} - Recepción {self.recepcion_id}'
        return f'Inspección #{self.pk} - Sin vínculo'


class FotoRecepcion(models.Model):
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
    descripcion = models.CharField(max_length=100, blank=True, null=True, verbose_name='Ej: Rayón puerta izquierda')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Foto de la Recepción'
        verbose_name_plural = 'Fotos de las Recepciones'
        ordering = ['tipo_vista', 'created_at']

    def __str__(self):
        return f'{self.recepcion_id} - {self.get_tipo_vista_display()}'