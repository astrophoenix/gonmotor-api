"""Despachador de una `NotificacionProgramada` hacia WhatsApp o correo.

Regla de canal: se intenta primero el canal preferido de la notificación; si
el cliente no tiene dato de contacto para ese canal, se usa el otro. Si no
tiene ninguno, el envío falla sin reintento (`reintentable=False`).
"""

from .correo import correo_enviar_y_registrar
from .telefonos import normalizar_celular
from .whatsapp import enviar_y_registrar_con_registro

WHATSAPP = 'whatsapp'
CORREO = 'correo'


def canales_disponibles(cliente):
    """Datos de contacto utilizables del cliente: {'whatsapp': ..., 'correo': ...}."""
    if cliente is None:
        return {}
    disponibles = {}
    celular = normalizar_celular(getattr(cliente, 'telefono', ''))
    if celular:
        disponibles[WHATSAPP] = celular
    correo = (getattr(cliente, 'email', '') or '').strip()
    if correo:
        disponibles[CORREO] = correo
    return disponibles


def elegir_canal(preferido, cliente):
    """Devuelve (canal, destino) o (None, None) si el cliente no tiene contacto."""
    disponibles = canales_disponibles(cliente)
    orden = [preferido] + [c for c in (WHATSAPP, CORREO) if c != preferido]
    for canal in orden:
        if canal in disponibles:
            return canal, disponibles[canal]
    return None, None


def enviar_notificacion(notificacion, *, user=None, origen='PROGRAMADO'):
    """Envía la notificación por el canal que corresponda y la registra.

    No cambia el estado de `notificacion` (eso lo hace la cola).

    Returns:
        tuple(dict, RegistroMensajeWhatsApp | None): respuesta común (con
        `canal` y `reintentable`) y la fila de la bitácora.
    """
    cliente = notificacion.cliente
    canal, destino = elegir_canal(notificacion.canal, cliente)
    if canal is None:
        return {
            'ok': False,
            'canal': None,
            'reintentable': False,
            'error': 'El cliente no tiene teléfono ni correo registrados.',
        }, None

    comunes = {
        'empresa': notificacion.empresa,
        'mensaje': notificacion.mensaje,
        'origen': origen,
        'vehiculo': notificacion.vehiculo,
        'cliente_nombre': cliente.nombre,
        'user': user,
    }
    if canal == WHATSAPP:
        resultado, registro = enviar_y_registrar_con_registro(
            celular=destino,
            # Las campañas salen con la plantilla de marketing de Meta.
            categoria='promocion' if notificacion.tipo == 'MASIVA' else 'aviso',
            **comunes,
        )
    else:
        resultado, registro = correo_enviar_y_registrar(
            correo=destino,
            asunto=notificacion.asunto,
            **comunes,
        )
    resultado['canal'] = canal
    resultado.setdefault('reintentable', not resultado.get('ok'))
    return resultado, registro
