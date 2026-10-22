"""Canal de correo para las notificaciones al cliente.

Usa el backend de correo de Django: en local (`config.settings.local`) es el
de consola, así que el mensaje solo se imprime y queda como SIMULADO en la
bitácora; en producción es SMTP con las variables `EMAIL_*` del `.env`.

La respuesta sigue el mismo contrato que `whatsapp.enviar_mensaje`
(`ok`, `simulado`, `proveedor`, `descripcion`...), para que el despachador
trate igual a los dos canales.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from apps.notificaciones.models import RegistroMensajeWhatsApp

logger = logging.getLogger('apps.notificaciones.correo')

# Backends que no entregan el correo a nadie: el envío cuenta como simulado.
_BACKENDS_SIMULADOS = ('console', 'locmem', 'dummy', 'filebased')

ASUNTO_DEFAULT = 'Aviso de tu taller'


class CorreoError(Exception):
    """Error controlado al enviar un correo."""


def _es_simulado():
    backend = (getattr(settings, 'EMAIL_BACKEND', '') or '').lower()
    return any(nombre in backend for nombre in _BACKENDS_SIMULADOS)


def enviar_correo(destinatario, asunto, mensaje):
    """Envía (o simula) un correo de texto plano.

    Raises:
        CorreoError: destinatario inválido o fallo del servidor de correo.
    """
    destinatario = (destinatario or '').strip()
    try:
        validate_email(destinatario)
    except ValidationError as exc:
        raise CorreoError('El correo del destinatario está vacío o no es válido.') from exc

    try:
        send_mail(
            subject=asunto or ASUNTO_DEFAULT,
            message=mensaje,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or None,
            recipient_list=[destinatario],
            fail_silently=False,
        )
    except Exception as exc:
        raise CorreoError(f'No se pudo enviar el correo: {exc}') from exc

    simulado = _es_simulado()
    return {
        'ok': True,
        'simulado': simulado,
        'proveedor': 'correo',
        'id_externo': '',
        'correo': destinatario,
        'descripcion': (
            f'Correo simulado hacia {destinatario} (backend de desarrollo).'
            if simulado else f'Correo enviado a {destinatario}.'
        ),
    }


def correo_enviar_y_registrar(
    *,
    empresa,
    correo,
    asunto,
    mensaje,
    origen='MANUAL',
    vehiculo=None,
    cliente_nombre='',
    user=None,
):
    """Envía el correo y lo deja en la bitácora con `canal='correo'`.

    Returns:
        tuple(dict, RegistroMensajeWhatsApp): respuesta común y la fila creada.
    """
    base = {
        'empresa': empresa,
        'vehiculo': vehiculo,
        'enviado_por': user,
        'cliente_nombre': cliente_nombre or '',
        'correo': (correo or '').strip(),
        'mensaje': mensaje,
        'canal': 'correo',
        'proveedor': 'correo',
        'origen': origen,
        'metadatos': {'asunto': asunto or ASUNTO_DEFAULT},
    }
    try:
        resultado = enviar_correo(correo, asunto, mensaje)
    except CorreoError as exc:
        logger.warning('Correo no enviado a %s: %s', correo, exc)
        registro = RegistroMensajeWhatsApp.objects.create(
            **base,
            estado=RegistroMensajeWhatsApp.EstadoMensaje.ERROR,
            error=str(exc),
        )
        return {
            'ok': False,
            'simulado': False,
            'proveedor': 'correo',
            'correo': base['correo'],
            'estado': RegistroMensajeWhatsApp.EstadoMensaje.ERROR,
            'descripcion': str(exc),
            'error': str(exc),
            'registrado': True,
        }, registro

    estado = (
        RegistroMensajeWhatsApp.EstadoMensaje.SIMULADO
        if resultado['simulado'] else RegistroMensajeWhatsApp.EstadoMensaje.ENVIADO
    )
    registro = RegistroMensajeWhatsApp.objects.create(**base, estado=estado)
    resultado['estado'] = estado
    resultado['registrado'] = True
    return resultado, registro
