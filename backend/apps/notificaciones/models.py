from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


PLANTILLA_MANTENIMIENTO_DEFAULT = (
    'Hola {cliente}, el taller {empresa} quiere recordarte que tu vehiculo '
    '{marca} {modelo} (placa {placa}) esta proximo a su mantenimiento preventivo. '
    'Te esperamos para que lo revises a tiempo. '
    'Respondenos este mensaje para agendar tu cita. Gracias.'
)


class PreferenciaMantenimiento(BaseModel):
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
        help_text='Evita reenviar recordatorio al mismo vehículo antes de este número de días.',
    )
    notificaciones_activas = models.BooleanField(
        verbose_name='Notificaciones activas',
        default=True,
        help_text='Si está desactivado, el proceso automático no envía recordatorios.',
    )
    notificar_vehiculos_sin_programar = models.BooleanField(
        verbose_name='Notificar sin próximo mantenimiento registrado',
        default=False,
        help_text='Incluye vehículos que aún no tienen próximo mantenimiento (km/fecha) configurado.',
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


class RegistroMensajeWhatsApp(BaseModel):
    """Bitácora de mensajes de WhatsApp enviados o simulados.

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
        help_text='mock, twilio_sandbox o meta_api.',
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
        return f'{self.estado} → {self.celular} ({self.proveedor})'