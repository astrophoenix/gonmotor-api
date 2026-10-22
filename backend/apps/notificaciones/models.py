from django.conf import settings
from django.db import models

from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import BaseModel


PLANTILLA_MANTENIMIENTO_DEFAULT = (
    'Hola {cliente}, el taller {empresa} quiere recordarte que tu vehiculo '
    '{marca} {modelo} (placa {placa}) esta proximo a su mantenimiento preventivo. '
    'Te esperamos para que lo revises a tiempo. '
    'Respondenos este mensaje para agendar tu cita. Gracias.'
)


class PreferenciaMantenimiento(UuidPublicoModel, BaseModel):
    """Configuración por empresa para las alertas de mantenimiento preventivo.

    Viene con valores por defecto "amigables para demo" (intervalo de 5000 km y
    7 días de antelación) para que el equipo de 3 personas pueda probar el módulo
    de WhatsApp sin configuración previa.
    """

    empresa = models.OneToOneField(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='preferencia_mantenimiento',
        verbose_name='Empresa',
    )
    intervalo_km = models.PositiveIntegerField(
        verbose_name='Intervalo de mantenimiento (km)',
        default=5000,
        help_text='Kilometraje máximo sugerido entre mantenimientos preventivos.',
    )
    dias_antelacion = models.PositiveIntegerField(
        verbose_name='Antelación (días)',
        default=7,
        help_text='Días antes de la fecha límite en que se envía el recordatorio.',
    )
    frecuencia_dias = models.PositiveIntegerField(
        verbose_name='Reintento (días)',
        default=30,
        help_text='El recordatorio manual no se reenvía al mismo vehículo antes de este número de días.',
    )
    notificaciones_activas = models.BooleanField(
        verbose_name='Notificaciones activas',
        default=True,
        help_text='Si está desactivado, al entregar una orden no se programa el próximo mantenimiento.',
    )
    intervalo_meses = models.PositiveIntegerField(
        verbose_name='Intervalo de mantenimiento (meses)',
        default=6,
        help_text='Tope por tiempo desde la entrega de la orden, para vehículos que recorren poco.',
    )
    recordatorios_cita_activos = models.BooleanField(
        verbose_name='Recordatorios de cita',
        default=True,
        help_text='Programa avisos 24 h y 2 h antes de cada cita agendada.',
    )
    mensaje_plantilla = models.TextField(
        verbose_name='Plantilla del mensaje',
        default=PLANTILLA_MANTENIMIENTO_DEFAULT,
        help_text=(
            'Placeholders disponibles: {cliente}, {empresa}, {marca}, {modelo}, '
            '{placa}, {kilometraje}.'
        ),
    )

    class Meta:
        verbose_name = 'Preferencia de mantenimiento'
        verbose_name_plural = 'Preferencias de mantenimiento'

    def __str__(self):
        return f'Preferencias {self.empresa_id} (cada {self.intervalo_km} km)'


class RegistroMensajeWhatsApp(UuidPublicoModel, BaseModel):
    """Bitácora de mensajes enviados o simulados (WhatsApp y correo, ver `canal`).

    El nombre se conserva por compatibilidad: la tabla ya guarda ambos canales.

    En modo `mock` (desarrollo local) los mensajes no salen realmente por
    WhatsApp: quedan registrados aquí con estado `SIMULADO` para poder validar
    el flujo sin costo alguno.
    """

    class EstadoMensaje(models.TextChoices):
        ENVIADO = 'ENVIADO', 'Enviado'
        ERROR = 'ERROR', 'Error'
        SIMULADO = 'SIMULADO', 'Simulado (entorno de prueba)'

    class OrigenMensaje(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual desde la interfaz'
        PROGRAMADO = 'PROGRAMADO', 'Proceso automático'
        PRUEBA = 'PRUEBA', 'Mensaje de prueba'

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='registros_whatsapp',
        verbose_name='Empresa',
    )
    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='registros_whatsapp',
        verbose_name='Vehículo',
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mensajes_whatsapp_enviados',
        verbose_name='Enviado por',
    )
    cliente_nombre = models.CharField(
        verbose_name='Cliente',
        max_length=200,
        blank=True,
        default='',
    )
    celular = models.CharField(
        verbose_name='Teléfono / WhatsApp',
        max_length=30,
        blank=True,
        default='',
    )
    correo = models.EmailField(
        verbose_name='Correo',
        blank=True,
        default='',
        help_text='Destinatario cuando el canal es correo.',
    )
    mensaje = models.TextField(
        verbose_name='Mensaje',
        blank=True,
        default='',
    )
    canal = models.CharField(
        verbose_name='Canal',
        max_length=20,
        default='whatsapp',
    )
    proveedor = models.CharField(
        verbose_name='Proveedor usado',
        max_length=30,
        default='mock',
        help_text='mock, twilio_sandbox, meta_api o correo.',
    )
    estado = models.CharField(
        verbose_name='Estado',
        max_length=20,
        choices=EstadoMensaje.choices,
        default=EstadoMensaje.SIMULADO,
    )
    origen = models.CharField(
        verbose_name='Origen',
        max_length=20,
        choices=OrigenMensaje.choices,
        default=OrigenMensaje.MANUAL,
    )
    id_externo = models.CharField(
        verbose_name='ID externo (proveedor)',
        max_length=100,
        blank=True,
        default='',
        help_text='SID de Twilio o ID de mensaje de Meta Cloud API.',
    )
    error = models.TextField(
        verbose_name='Error',
        blank=True,
        default='',
    )
    metadatos = models.JSONField(
        verbose_name='Metadatos',
        default=dict,
        blank=True,
    )

    class Meta:
        verbose_name = 'Registro de mensaje WhatsApp'
        verbose_name_plural = 'Registros de mensajes WhatsApp'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.estado} → {self.celular or self.correo} ({self.proveedor})'

class Campana(UuidPublicoModel, BaseModel):
    """Mensaje masivo o promocional hacia un grupo de clientes.

    Al lanzarla se crea una `NotificacionProgramada` (tipo MASIVA) por cada
    cliente del segmento que aceptó promociones y tiene algún contacto; la cola
    las envía desde `fecha_programada`.

    `segmento` (JSON, todos opcionales):
        marca            texto contenido en la marca de algún vehículo del cliente
        sin_visita_dias  sin orden entregada en los últimos N días (o nunca)
        con_visita_dias  con alguna orden entregada en los últimos N días
        clientes         lista de IDs públicos (UUID) de clientes
    """

    class Estado(models.TextChoices):
        BORRADOR = 'BORRADOR', 'Borrador'
        LANZADA = 'LANZADA', 'Lanzada'
        CANCELADA = 'CANCELADA', 'Cancelada'

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='campanas',
        verbose_name='Empresa',
    )
    nombre = models.CharField(verbose_name='Nombre', max_length=150)
    asunto = models.CharField(verbose_name='Asunto (correo)', max_length=200, blank=True, default='')
    mensaje = models.TextField(
        verbose_name='Mensaje',
        help_text='Placeholders disponibles: {cliente}, {empresa}.',
    )
    canal = models.CharField(
        verbose_name='Canal preferido',
        max_length=20,
        choices=[('whatsapp', 'WhatsApp'), ('correo', 'Correo')],
        default='whatsapp',
    )
    segmento = models.JSONField(verbose_name='Segmento', default=dict, blank=True)
    estado = models.CharField(
        verbose_name='Estado',
        max_length=20,
        choices=Estado.choices,
        default=Estado.BORRADOR,
    )
    fecha_programada = models.DateTimeField(
        verbose_name='Enviar a partir de',
        null=True,
        blank=True,
        help_text='Vacío = en cuanto se lance.',
    )
    fecha_lanzamiento = models.DateTimeField(verbose_name='Lanzada el', null=True, blank=True)
    total_destinatarios = models.PositiveIntegerField(verbose_name='Destinatarios', default=0)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='campanas_creadas',
        verbose_name='Creado por',
    )

    class Meta:
        verbose_name = 'Campaña'
        verbose_name_plural = 'Campañas'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.nombre} ({self.get_estado_display()})'


class NotificacionProgramada(UuidPublicoModel, BaseModel):
    """Cola de avisos al cliente: cada fila es un envío pendiente o ya resuelto.

    La crean los disparadores (cierre de OT, citas, campañas) o el usuario a
    mano; `procesar_notificaciones` envía las PENDIENTES cuya
    `fecha_programada` ya llegó y deja el resultado en `registro`.
    """

    class Tipo(models.TextChoices):
        RETIRO = 'RETIRO', 'Vehículo listo para retiro'
        MANTENIMIENTO = 'MANTENIMIENTO', 'Próximo mantenimiento'
        CITA_24H = 'CITA_24H', 'Recordatorio de cita (24 h)'
        CITA_2H = 'CITA_2H', 'Recordatorio de cita (2 h)'
        CITA = 'CITA', 'Aviso de cita (manual)'
        SALDO = 'SALDO', 'Recordatorio de saldo pendiente'
        MASIVA = 'MASIVA', 'Mensaje masivo / promocional'

    class Canal(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        CORREO = 'correo', 'Correo'

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        ENVIADA = 'ENVIADA', 'Enviada'
        ERROR = 'ERROR', 'Error'
        CANCELADA = 'CANCELADA', 'Cancelada'

    class Origen(models.TextChoices):
        AUTO = 'AUTO', 'Automática'
        MANUAL = 'MANUAL', 'Manual'

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='notificaciones_programadas',
        verbose_name='Empresa',
    )
    tipo = models.CharField(verbose_name='Tipo', max_length=20, choices=Tipo.choices)
    canal = models.CharField(
        verbose_name='Canal preferido',
        max_length=20,
        choices=Canal.choices,
        default=Canal.WHATSAPP,
        help_text='Si el cliente no tiene dato de contacto para este canal, se usa el otro.',
    )
    estado = models.CharField(
        verbose_name='Estado',
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    origen = models.CharField(
        verbose_name='Origen',
        max_length=10,
        choices=Origen.choices,
        default=Origen.AUTO,
    )
    fecha_programada = models.DateTimeField(verbose_name='Enviar a partir de')
    km_objetivo = models.PositiveIntegerField(
        verbose_name='Kilometraje objetivo',
        null=True,
        blank=True,
    )
    fecha_tope = models.DateField(
        verbose_name='Fecha tope',
        null=True,
        blank=True,
        help_text='Fecha límite del mantenimiento (por tiempo).',
    )
    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones_programadas',
        verbose_name='Cliente',
    )
    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones_programadas',
        verbose_name='Vehículo',
    )
    orden = models.ForeignKey(
        'ordenes.OrdenTrabajo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones_programadas',
        verbose_name='Orden de trabajo',
    )
    cita = models.ForeignKey(
        'citas.Cita',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones_programadas',
        verbose_name='Cita',
    )
    campana = models.ForeignKey(
        'notificaciones.Campana',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones',
        verbose_name='Campaña',
    )
    asunto = models.CharField(
        verbose_name='Asunto (correo)',
        max_length=200,
        blank=True,
        default='',
    )
    mensaje = models.TextField(verbose_name='Mensaje')
    intentos = models.PositiveSmallIntegerField(verbose_name='Intentos', default=0)
    ultimo_error = models.TextField(verbose_name='Último error', blank=True, default='')
    fecha_envio = models.DateTimeField(verbose_name='Fecha de envío', null=True, blank=True)
    registro = models.ForeignKey(
        RegistroMensajeWhatsApp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones',
        verbose_name='Registro del envío',
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones_programadas',
        verbose_name='Creado por',
    )

    class Meta:
        verbose_name = 'Notificación programada'
        verbose_name_plural = 'Notificaciones programadas'
        ordering = ['fecha_programada']
        indexes = [
            models.Index(fields=['estado', 'fecha_programada'], name='notif_cola_idx'),
            models.Index(fields=['empresa', 'estado', 'fecha_programada'], name='notif_empresa_idx'),
        ]
        constraints = [
            # Un vehículo tiene a lo sumo un próximo mantenimiento pendiente por
            # empresa (el vehículo es global: otra empresa lleva el suyo).
            models.UniqueConstraint(
                fields=['empresa', 'vehiculo'],
                condition=models.Q(tipo='MANTENIMIENTO', estado='PENDIENTE'),
                name='notif_mant_pendiente_unico',
            ),
            # Una cita tiene a lo sumo un recordatorio pendiente de cada tipo.
            models.UniqueConstraint(
                fields=['cita', 'tipo'],
                condition=models.Q(estado='PENDIENTE', cita__isnull=False),
                name='notif_cita_pendiente_unico',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.estado} · {self.fecha_programada:%Y-%m-%d %H:%M}'
