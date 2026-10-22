"""Servicio de integración con WhatsApp (estrategia 100% gratuita / sandbox).

Este módulo es un wrapper genérico con 3 proveedores intercambiables que usan
condfiguración vía variables de entorno. Ninguno requiere tarjeta de crédito y
todos tienen cuota gratuita para desarrollo/equipo pequeño.

╔═══════════════════════════════════════════════════════════════════════╗
║  CREDENCIALES DE PRUEBA (variables en .env)                          ║
╠═══════════════════════════════════════════════════════════════════════╣
║  WHATSAPP_MODE=mock            (por defecto, sin credenciales)        ║
║  ───────────────────────────────────────────────────────────────────── ║
║  PROVEEDOR 1 · mock  (DESARROLLO LOCAL, recomendado para el equipo)   ║
║    No hace llamadas HTTP ni requiere cuenta. Registra cada mensaje     ║
║    en consola y en la tabla RegistroMensajeWhatsApp con estado         ║
║    SIMULADO, e imprime el enlace wa.me listo para abrir en el móvil.   ║
║  ───────────────────────────────────────────────────────────────────── ║
║  PROVEEDOR 2 · twilio_sandbox  (SANDBOX GRATUITO DE TWILIO)           ║
║    - Crear cuenta en https://www.twilio.com (crédito de prueba $15).   ║
║    - En la consola: Messaging > Settings > WhatsApp sender, usar el    ║
║      número sandbox (ej. whatsapp:+14155238886).                       ║
║    - Registrar el móvil de cada tester enviando el código "join ..."   ║
║      al número sandbox (así "activan" la conversación de prueba).      ║
║    Variables:                                                          ║
║      WHATSAPP_MODE=twilio_sandbox                                      ║
║      WHATSAPP_TWILIO_ACCOUNT_SID=AC...  (consola Twilio)               ║
║      WHATSAPP_TWILIO_AUTH_TOKEN=...     (consola Twilio)               ║
║      WHATSAPP_TWILIO_FROM_NUMBER=whatsapp:+14155238886                 ║
║  ───────────────────────────────────────────────────────────────────── ║
║  PROVEEDOR 3 · meta_api  (WHATSAPP CLOUD API, MODO PRUEBA)            ║
║    - Crear app en https://developers.facebook.com > WhatsApp >         ║
║      "API de pruebas". Usar un Business de prueba en Meta.             ║
║    - Copiar el token y el Phone Number ID del entorno de pruebas       ║
║      (nunca caducan en test mode).                                     ║
║    Variables:                                                          ║
║      WHATSAPP_MODE=meta_api                                            ║
║      WHATSAPP_META_TOKEN=EAAG...                                       ║
║      WHATSAPP_META_PHONE_NUMBER_ID=10...                               ║
║    Plantillas (ver «Plantillas de Meta» más abajo):                    ║
║      WHATSAPP_META_PLANTILLA_AVISO=aviso_taller                        ║
║      WHATSAPP_META_PLANTILLA_PROMOCION=promocion_taller                ║
║      WHATSAPP_META_IDIOMA=es                                           ║
╚═══════════════════════════════════════════════════════════════════════╝

Plantillas de Meta
------------------
Meta solo acepta texto libre si el cliente escribió al número en las últimas
24 h. Todo aviso que inicia el taller (mantenimiento, retiro, citas,
campañas) debe salir con una plantilla aprobada en WhatsApp Manager. Con
`WHATSAPP_META_PLANTILLA_*` configuradas, el modo `meta_api` envía el texto
del aviso como variable de la plantilla; vacías, envía texto libre (solo sirve
en pruebas con números que escribieron antes).

Crear dos plantillas, idioma Español (`es`), con dos variables de cuerpo:

    aviso_taller       categoría UTILITY    cuerpo: «Aviso de {{1}}: {{2}}»
    promocion_taller   categoría MARKETING  cuerpo: «Novedades de {{1}}: {{2}}»

    {{1}} = nombre comercial de la empresa, {{2}} = texto completo del aviso
    (el mismo que se ve en la cola; se envía en una sola línea).

Los avisos usan `aviso_taller`; las campañas (`MASIVA`), `promocion_taller`.

Respuesta común de cualquier proveedor:

    {
        "ok": True,
        "simulado": bool,          # True si no se hizo una llamada real
        "proveedor": "mock",
        "id_externo": str,         # SID / message id si el proveedor lo da
        "celular": str,            # número normalizado (E.164 sin '+')
        "descripcion": str,        # texto amigable para toasts
    }

Modo de uso:

    from apps.notificaciones.services.whatsapp import enviar_mensaje
    resultado = enviar_mensaje(celular="+593991234567", mensaje="Hola")

O con contexto de negocio (persiste la bitácora):

    resultado = whatsapp_enviar_y_registrar(
        empresa=empresa, vehiculo=vehiculo, cliente=cliente,
        celular=celular, mensaje=mensaje, origen="MANUAL", user=request.user
    )
"""

import logging
import os
import re

import requests
from django.conf import settings

from apps.notificaciones.models import RegistroMensajeWhatsApp
from .telefonos import normalizar_celular, celular_para_wa_link

logger = logging.getLogger('apps.notificaciones.whatsapp')


class WhatsAppError(Exception):
    """Error controlado del proveedor de WhatsApp."""


def _modo_activo():
    return (getattr(settings, 'WHATSAPP_MODE', 'mock') or 'mock').strip().lower()


def resumen_configuracion():
    """Resumen seguro (sin secretos) del proveedor activo para exponer por API."""
    modo = _modo_activo()
    info = {
        'modo': modo,
        'proveedor': modo,
        'configurado': False,
        'descripcion': '',
    }
    if modo == 'mock':
        info['descripcion'] = (
            'Modo de desarrollo local: los mensajes se simulan y NO salen por '
            'WhatsApp. Sin costo y sin credenciales.'
        )
        info['configurado'] = True
    elif modo == 'twilio_sandbox':
        info['configurado'] = bool(
            getattr(settings, 'WHATSAPP_TWILIO_ACCOUNT_SID', '')
            and getattr(settings, 'WHATSAPP_TWILIO_AUTH_TOKEN', '')
        )
        info['from'] = getattr(settings, 'WHATSAPP_TWILIO_FROM_NUMBER', '')
        info['descripcion'] = 'Twilio WhatsApp Sandbox (gratuito para pruebas).'
    elif modo == 'meta_api':
        info['configurado'] = bool(
            getattr(settings, 'WHATSAPP_META_TOKEN', '')
            and getattr(settings, 'WHATSAPP_META_PHONE_NUMBER_ID', '')
        )
        info['descripcion'] = 'Meta WhatsApp Cloud API (modo de pruebas).'
    else:
        info['descripcion'] = f'Modo "{modo}" no reconocido; se usará mock.'
    return info


def _enviar_mock(celular, mensaje, plantilla=None):
    logger.warning(
        '[WHATSAPP MOCK] No se envió un mensaje real.\n'
        '  Para:   %s\n'
        '  Link:   %s\n'
        '  Cuerpo: %s',
        celular_para_wa_link(celular),
        f'https://wa.me/{celular_para_wa_link(celular)}?text={mensaje}',
        mensaje,
    )
    archivo = getattr(settings, 'WHATSAPP_MOCK_LOG_FILE', '')
    if archivo:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(archivo)) or '.', exist_ok=True)
            with open(archivo, 'a', encoding='utf-8') as fh:
                fh.write(
                    f'\n[{celular}]\n{celular_para_wa_link(celular)}\n{mensaje}\n'
                    '----\n'
                )
        except OSError as exc:
            logger.warning('No se pudo escribir el log mock: %s', exc)

    return {
        'ok': True,
        'simulado': True,
        'proveedor': 'mock',
        'id_externo': '',
        'celular': celular,
        'wa_link': f'https://wa.me/{celular_para_wa_link(celular)}',
        'descripcion': f'Mensaje simulado hacia {celular_para_wa_link(celular)} '
                       '(modo mock, sin costo).',
    }


def _enviar_twilio_sandbox(celular, mensaje, plantilla=None):
    sid = getattr(settings, 'WHATSAPP_TWILIO_ACCOUNT_SID', '')
    token = getattr(settings, 'WHATSAPP_TWILIO_AUTH_TOKEN', '')
    de = getattr(settings, 'WHATSAPP_TWILIO_FROM_NUMBER', 'whatsapp:+14155238886')
    if not sid or not token:
        raise WhatsAppError(
            'Faltan WHATSAPP_TWILIO_ACCOUNT_SID / WHATSAPP_TWILIO_AUTH_TOKEN '
            'para usar el modo twilio_sandbox.'
        )

    url = f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json'
    data = {
        'To': f'whatsapp:{celular}',
        'From': de,
        'Body': mensaje,
    }
    try:
        resp = requests.post(url, data=data, auth=(sid, token), timeout=20)
    except requests.RequestException as exc:
        raise WhatsAppError(f'No se pudo contactar a Twilio: {exc}') from exc

    if resp.status_code != 201:
        raise WhatsAppError(
            f'Twilio respondió {resp.status_code}: {resp.text[:500]}'
        )
    payload = resp.json()
    return {
        'ok': True,
        'simulado': False,
        'proveedor': 'twilio_sandbox',
        'id_externo': payload.get('sid', ''),
        'celular': celular,
        'descripcion': f'Mensaje enviado vía Twilio Sandbox a '
                       f'+{celular} (SID {payload.get("sid", "")[:8]}...)',
    }


def _enviar_meta_api(celular, mensaje, plantilla=None):
    token = getattr(settings, 'WHATSAPP_META_TOKEN', '')
    phone_number_id = getattr(settings, 'WHATSAPP_META_PHONE_NUMBER_ID', '')
    if not token or not phone_number_id:
        raise WhatsAppError(
            'Faltan WHATSAPP_META_TOKEN / WHATSAPP_META_PHONE_NUMBER_ID '
            'para usar el modo meta_api.'
        )

    url = f'https://graph.facebook.com/v21.0/{phone_number_id}/messages'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    if plantilla:
        payload = {
            'messaging_product': 'whatsapp',
            'to': celular,
            'type': 'template',
            'template': {
                'name': plantilla['nombre'],
                'language': {'code': plantilla['idioma']},
                'components': [{
                    'type': 'body',
                    'parameters': [{'type': 'text', 'text': valor} for valor in plantilla['parametros']],
                }],
            },
        }
    else:
        payload = {
            'messaging_product': 'whatsapp',
            'to': celular,
            'type': 'text',
            'text': {'body': mensaje},
        }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
    except requests.RequestException as exc:
        raise WhatsAppError(f'No se pudo contactar a Meta Graph API: {exc}') from exc

    if resp.status_code != 200:
        raise WhatsAppError(
            f'Meta respondió {resp.status_code}: {resp.text[:500]}'
        )
    data = resp.json()
    return {
        'ok': True,
        'simulado': False,
        'proveedor': 'meta_api',
        'id_externo': (data.get('messages') or [{}])[0].get('id', ''),
        'celular': celular,
        'descripcion': f'Mensaje enviado vía Meta Cloud API a +{celular}.',
    }


# Ruta de despacho según WHATSAPP_MODE
_DESPACHADORES = {
    'mock': _enviar_mock,
    'twilio_sandbox': _enviar_twilio_sandbox,
    'meta_api': _enviar_meta_api,
}


# Límites de Meta para una variable de plantilla: sin saltos de línea ni
# tabulaciones ni más de 4 espacios seguidos; el cuerpo entero ≤ 1024.
_MAX_PARAMETRO_META = 900


def _parametro_meta(texto):
    limpio = re.sub(r'\s+', ' ', str(texto or '')).strip()
    if len(limpio) > _MAX_PARAMETRO_META:
        limpio = limpio[:_MAX_PARAMETRO_META - 1].rstrip() + '…'
    return limpio or '-'


def plantilla_meta(categoria, *, empresa_nombre, mensaje):
    """Plantilla de Meta para un aviso (`categoria`: 'aviso' | 'promocion').

    Devuelve None si el modo no es `meta_api` o la plantilla no está
    configurada: entonces se envía texto libre.
    """
    if _modo_activo() != 'meta_api':
        return None
    clave = 'WHATSAPP_META_PLANTILLA_PROMOCION' if categoria == 'promocion' else 'WHATSAPP_META_PLANTILLA_AVISO'
    nombre = (getattr(settings, clave, '') or '').strip()
    if not nombre:
        return None
    return {
        'nombre': nombre,
        'idioma': getattr(settings, 'WHATSAPP_META_IDIOMA', 'es') or 'es',
        'parametros': [_parametro_meta(empresa_nombre), _parametro_meta(mensaje)],
    }


def enviar_mensaje(celular, mensaje, *, plantilla=None):
    """Envía (o simula) un mensaje de WhatsApp por el proveedor activo.

    Args:
        celular: número local o internacional (se normaliza solo).
        mensaje: texto del mensaje.
        plantilla: plantilla de Meta (`plantilla_meta`); solo la usa `meta_api`.

    Returns:
        dict con la respuesta común (ver docstring del módulo).

    Raises:
        WhatsAppError: si el proveedor rechaza o hay error de configuración.
    """
    celular_normalizado = normalizar_celular(celular)
    if not celular_normalizado:
        raise WhatsAppError('El teléfono está vacío o no se pudo normalizar.')

    modo = _modo_activo()
    despachador = _DESPACHADORES.get(modo) or _enviar_mock
    resultado = despachador(celular_normalizado, mensaje, plantilla)
    resultado['celular_normalizado'] = celular_normalizado
    return resultado


def whatsapp_enviar_y_registrar(
    *,
    empresa,
    celular,
    mensaje,
    origen='MANUAL',
    vehiculo=None,
    cliente_nombre='',
    user=None,
):
    """Envía el mensaje y persiste el registro en la bitácora.

    Es la función que consumen las vistas y el comando programado para mantener
    un historial consistente en `RegistroMensajeWhatsApp`.
    """
    resultado, _registro = enviar_y_registrar_con_registro(
        empresa=empresa,
        celular=celular,
        mensaje=mensaje,
        origen=origen,
        vehiculo=vehiculo,
        cliente_nombre=cliente_nombre,
        user=user,
    )
    return resultado


def enviar_y_registrar_con_registro(
    *,
    empresa,
    celular,
    mensaje,
    origen='MANUAL',
    vehiculo=None,
    cliente_nombre='',
    user=None,
    categoria='aviso',
):
    """Como `whatsapp_enviar_y_registrar`, pero devuelve también el registro.

    `categoria` ('aviso' | 'promocion') elige la plantilla de Meta.

    Returns:
        tuple(dict, RegistroMensajeWhatsApp | None): respuesta común y la fila
        de la bitácora (None solo ante un error inesperado).
    """
    plantilla = plantilla_meta(
        categoria,
        empresa_nombre=getattr(empresa, 'nombre_comercial', ''),
        mensaje=mensaje,
    )
    try:
        resultado = enviar_mensaje(celular, mensaje, plantilla=plantilla)
        estado = RegistroMensajeWhatsApp.EstadoMensaje.ENVIADO
        id_externo = resultado.get('id_externo', '')
        if resultado.get('simulado'):
            estado = RegistroMensajeWhatsApp.EstadoMensaje.SIMULADO
        registro = RegistroMensajeWhatsApp.objects.create(
            empresa=empresa,
            vehiculo=vehiculo,
            enviado_por=user,
            cliente_nombre=cliente_nombre or '',
            celular=resultado.get('celular_normalizado') or normalizar_celular(celular),
            mensaje=mensaje,
            canal='whatsapp',
            proveedor=resultado.get('proveedor', 'mock'),
            estado=estado,
            origen=origen,
            id_externo=id_externo,
            metadatos={
                'wa_link': resultado.get('wa_link', ''),
                'plantilla': plantilla['nombre'] if plantilla else '',
            },
        )
        result = dict(resultado)
        result['estado'] = estado
        result['registrado'] = True
        return result, registro

    except WhatsAppError as exc:
        registro = RegistroMensajeWhatsApp.objects.create(
            empresa=empresa,
            vehiculo=vehiculo,
            enviado_por=user,
            cliente_nombre=cliente_nombre or '',
            celular=normalizar_celular(celular),
            mensaje=mensaje,
            canal='whatsapp',
            proveedor=_modo_activo(),
            estado=RegistroMensajeWhatsApp.EstadoMensaje.ERROR,
            origen=origen,
            error=str(exc),
        )
        result = {
            'ok': False,
            'simulado': False,
            'proveedor': _modo_activo(),
            'celular': normalizar_celular(celular),
            'id_externo': '',
            'estado': RegistroMensajeWhatsApp.EstadoMensaje.ERROR,
            'descripcion': str(exc),
            'error': str(exc),
            'registrado': True,
        }
        return result, registro
    except Exception as exc:  # pragma: no cover - red de seguridad
        logger.exception('Error inesperado enviando WhatsApp')
        result = {
            'ok': False,
            'simulado': False,
            'proveedor': _modo_activo(),
            'celular': normalizar_celular(celular),
            'id_externo': '',
            'estado': RegistroMensajeWhatsApp.EstadoMensaje.ERROR,
            'descripcion': f'Error inesperado: {exc}',
            'error': str(exc),
            'registrado': False,
        }
        return result, None
