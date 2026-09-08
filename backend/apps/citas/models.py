from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_time

from apps.core.models import BaseModel


class Cita(BaseModel):
    """Gestión de citas programadas para el ingreso de vehículos al taller.

    Una cita agrupa empresa, cliente y vehículo con una fecha/hora acordada
    y un motivo de ingreso. Cuando el vehículo llega, la cita puede
    convertirse en una Recepción de Vehículo (evitando duplicar datos) y a
    partir de ahí continuar el flujo operativo existente.
    """

    class EstadoCita(models.TextChoices):
        PROGRAMADA = 'PROGRAMADA', 'Programada'
        CONFIRMADA = 'CONFIRMADA', 'Confirmada'
        EN_PROGRESO = 'EN_PROGRESO', 'En Progreso'
        COMPLETADA = 'COMPLETADA', 'Completada'
        CANCELADA = 'CANCELADA', 'Cancelada'
        NO_ASISTIO = 'NO_ASISTIO', 'No Asistió'

    class MotivoCita(models.TextChoices):
        MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento Preventivo'
        REPARACION = 'REPARACION', 'Reparación / Falla Reportada'
        DIAGNOSTICO = 'DIAGNOSTICO', 'Diagnóstico / Escaneo'
        ESTETICA = 'ESTETICA', 'Enderezada, Pintura o Detailing'
        GARANTIA = 'GARANTIA', 'Garantía / Retorno'
        OTRO = 'OTRO', 'Otro'

    # --- RELACIONES ---
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='citas',
        verbose_name='Empresa',
    )
    taller = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='citas',
        verbose_name='Taller',
        help_text='Taller donde se atenderá la cita (opcional)',
    )
    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.PROTECT,
        related_name='citas',
        verbose_name='Cliente',
    )
    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo',
        on_delete=models.PROTECT,
        related_name='citas',
        verbose_name='Vehículo',
    )
    asesor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='citas_atendidas',
        verbose_name='Asesor',
        help_text='Asesor o recepcionista que atiende la cita',
    )

    # --- DATOS DE LA CITA ---
    fecha_cita = models.DateField(verbose_name='Fecha de la cita')
    hora_cita = models.TimeField(verbose_name='Hora de la cita')
    fecha_hora_programada = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha y hora programada',
        help_text='Se resuelve automáticamente a partir de fecha_cita + hora_cita.',
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoCita.choices,
        default=EstadoCita.PROGRAMADA,
        verbose_name='Estado',
    )
    motivo = models.CharField(
        max_length=20,
        choices=MotivoCita.choices,
        default=MotivoCita.MANTENIMIENTO,
        verbose_name='Motivo',
    )
    motivo_descripcion = models.TextField(
        blank=True,
        null=True,
        verbose_name='Descripción del motivo',
        help_text='Detalle de la falla o servicio solicitado por el cliente',
    )
    kilometraje_aproximado = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Kilometraje aproximado',
        help_text='Kilometraje reportado por el cliente al agendar',
    )

    # --- CONVERSIÓN A RECEPCIÓN ---
    recepcion_generada = models.ForeignKey(
        'ordenes.RecepcionVehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cita_origen',
        verbose_name='Recepción generada',
        help_text='Recepción de vehículo creada a partir de esta cita',
    )
    fecha_conversion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de conversión a recepción',
    )

    # --- NOTAS ---
    notas_internas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas internas',
        help_text='Observaciones para el equipo del taller (no visibles para el cliente)',
    )

    class Meta:
        verbose_name = 'Cita'
        verbose_name_plural = 'Citas'
        ordering = ['-fecha_cita', '-hora_cita']

    def __str__(self):
        vehiculo = getattr(self, 'vehiculo', None)
        placa = vehiculo.placa if vehiculo else '?'
        return f'Cita {self.fecha_cita} {self.hora_cita} - {placa} ({self.get_estado_display()})'

    def save(self, *args, **kwargs):
        if self.fecha_cita and self.hora_cita:
            hora = self.hora_cita
            if isinstance(hora, str):
                hora = parse_time(hora)
            dt = timezone.make_aware(
                timezone.datetime.combine(self.fecha_cita, hora),
                timezone.get_current_timezone(),
            )
            self.fecha_hora_programada = dt
        super().save(*args, **kwargs)

    def convertir_a_recepcion(self, usuario=None):
        """Crea una RecepcionVehiculo a partir de esta cita sin duplicar datos.

        Reutiliza cliente, vehículo y los datos operativos de la cita
        (motivo de ingreso, sucursal, kilometraje). Genera el número de
        recepción de forma secuencial por taller, marca la cita como
        COMPLETADA y deja vinculada la recepción creada.
        """
        if self.estado in (self.EstadoCita.COMPLETADA, self.EstadoCita.CANCELADA, self.EstadoCita.NO_ASISTIO):
            raise ValueError(
                f'Una cita en estado "{self.get_estado_display()}" no puede convertirse en recepción.'
            )
        if self.recepcion_generada_id:
            raise ValueError('Esta cita ya generó una recepción de vehículo.')

        from apps.empresas.services import generar_codigo_secuencial, resolver_taller
        from apps.ordenes.models import RecepcionVehiculo

        taller = resolver_taller(self.empresa_id, self.taller)
        numero_recepcion = None
        if taller is not None:
            numero_recepcion = generar_codigo_secuencial(taller, 'recepcion')

        recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller or taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            recibido_por=usuario or self.asesor,
            numero_recepcion=numero_recepcion,
            tipo_recepcion=self._tipo_recepcion_desde_motivo(),
            motivo_ingreso=self.motivo_descripcion or self.get_motivo_display(),
            kilometraje_ingreso=self.kilometraje_aproximado,
        )

        self.recepcion_generada = recepcion
        self.estado = self.EstadoCita.COMPLETADA
        self.fecha_conversion = timezone.now()
        self.save(update_fields=['recepcion_generada', 'estado', 'fecha_conversion', 'updated_at'])

        return recepcion

    def _tipo_recepcion_desde_motivo(self):
        from apps.ordenes.models import OrdenTrabajo

        mapping = {
            self.MotivoCita.MANTENIMIENTO: OrdenTrabajo.TipoTrabajo.PREVENTIVO,
            self.MotivoCita.REPARACION: OrdenTrabajo.TipoTrabajo.CORRECTIVO,
            self.MotivoCita.DIAGNOSTICO: OrdenTrabajo.TipoTrabajo.DIAGNOSTICO,
            self.MotivoCita.ESTETICA: OrdenTrabajo.TipoTrabajo.ESTETICA,
            self.MotivoCita.GARANTIA: OrdenTrabajo.TipoTrabajo.GARANTIA,
        }
        return mapping.get(self.motivo, OrdenTrabajo.TipoTrabajo.DIAGNOSTICO)