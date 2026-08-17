from decimal import Decimal

from django.conf import settings
from django.db import models

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
    cotizacion_origen = models.ForeignKey(
        'cotizaciones.Cotizacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_derivadas',
        help_text="Cotización que dio origen o autorizó los trabajos de esta OT"
    )

    numero_orden = models.CharField(max_length=20, unique=True, verbose_name='Número de OT')
    estado = models.CharField(max_length=20, choices=EstadoOrden.choices, default=EstadoOrden.INGRESADO)
    prioridad = models.CharField(max_length=10, choices=Prioridad.choices, default=Prioridad.MEDIA)
    tipo_trabajo = models.CharField(max_length=20, choices=TipoTrabajo.choices, default=TipoTrabajo.PREVENTIVO)

    kilometraje_ingreso = models.PositiveIntegerField(verbose_name='Kilometraje de Ingreso')
    nivel_combustible = models.CharField(
        max_length=10,
        choices=[
            ('RESERVA', 'Reserva'),
            ('1/4', '1/4'),
            ('1/2', '1/2'),
            ('3/4', '3/4'),
            ('LLENO', 'Lleno'),
        ],
        default='1/4'
    )

    falla_reportada = models.TextField(verbose_name='Síntomas o Falla Reportada por el Cliente')
    diagnostico_tecnico = models.TextField(blank=True, null=True, verbose_name='Diagnóstico Realizado por el Mecánico')
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


class RecepcionVehiculo(models.Model):
    """Registro detallado del estado del vehículo al ingresar al taller."""

    orden_trabajo = models.OneToOneField(
        'ordenes.OrdenTrabajo',
        on_delete=models.CASCADE,
        related_name='recepcion',
        primary_key=True
    )

    ingreso_en_grua = models.BooleanField(default=False, verbose_name='¿Ingresó en grúa?')
    datos_grua = models.CharField(max_length=150, blank=True, null=True, verbose_name='Datos de la grúa / Chófer')

    tiene_radio = models.BooleanField(default=True, verbose_name='Radio / Mascarilla')
    tiene_llanta_repuesto = models.BooleanField(default=True, verbose_name='Llanta de Repuesto')
    tiene_gata_palanca = models.BooleanField(default=True, verbose_name='Gata y Palanca')
    tiene_extintor = models.BooleanField(default=False, verbose_name='Extintor')
    tiene_botiquin = models.BooleanField(default=False, verbose_name='Botiquín')
    tiene_antena = models.BooleanField(default=True, verbose_name='Antena')
    tiene_copas_ruedas = models.BooleanField(default=True, verbose_name='Copas / Tapacubos')
    tiene_herramientas = models.BooleanField(default=False, verbose_name='Juego de Herramientas')

    testigo_check_engine = models.BooleanField(default=False, verbose_name='Check Engine')
    testigo_abs = models.BooleanField(default=False, verbose_name='ABS')
    testigo_airbag = models.BooleanField(default=False, verbose_name='Airbag')
    testigo_bateria = models.BooleanField(default=False, verbose_name='Batería')
    testigo_aceite = models.BooleanField(default=False, verbose_name='Presión de Aceite')
    otros_testigos_observaciones = models.CharField(max_length=255, blank=True, null=True, verbose_name='Otros Testigos u Observaciones del Tablero')
    detalles_carroceria = models.TextField(blank=True, null=True, verbose_name='Descripción de Golpes, Rayones o Estado de Pintura')

    def __str__(self):
        return f'Recepción de OT - {self.orden_trabajo.numero_orden}'


class FotoRecepcion(models.Model):
    """Adjunta múltiples fotos de la recepción del vehículo."""

    recepcion = models.ForeignKey(RecepcionVehiculo, on_delete=models.CASCADE, related_name='fotos')
    imagen = models.ImageField(upload_to='ordenes/recepcion_fotos/')
    descripcion = models.CharField(max_length=100, blank=True, null=True, verbose_name='Ej: Rayón puerta izquierda')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.recepcion.orden_trabajo.numero_orden} - {self.descripcion or "Foto"}'