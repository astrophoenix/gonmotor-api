"""Recordatorio manual de mantenimiento de un vehículo y mensaje de prueba.

Los avisos automáticos (al entregar una OT, citas, campañas) viven en la cola
`NotificacionProgramada` (ver `cola.py`, `mantenimiento.py`, `avisos.py`).
Aquí queda el envío manual desde la lista de vehículos, que no reenvía al
mismo vehículo antes de `frecuencia_dias` (se deduce de la bitácora).
"""

from datetime import timedelta

from django.utils import timezone

from apps.vehiculos.models import propietario_actual
from .mensajes import construir_mensaje_mantenimiento, construir_mensaje_prueba
from .whatsapp import whatsapp_enviar_y_registrar


def get_preferencia(empresa):
    """Devuelve (creándola si hace falta) la preferencia de mantenimiento."""
    from apps.notificaciones.models import PreferenciaMantenimiento

    preferencia, _ = PreferenciaMantenimiento.objects.get_or_create(empresa=empresa)
    return preferencia


def _propietario_actual(vehiculo, empresa_id):
    """Dueño actual del vehículo en la empresa que envía (nunca el de otra)."""
    return propietario_actual(vehiculo, empresa_id)


def _ya_notificado_recientemente(vehiculo, empresa, frecuencia_dias):
    """Evita duplicar recordatorios según la preferencia de reintento."""
    from apps.notificaciones.models import RegistroMensajeWhatsApp

    desde = timezone.now() - timedelta(days=frecuencia_dias)
    return RegistroMensajeWhatsApp.objects.filter(
        empresa=empresa,  # instancia o id
        vehiculo=vehiculo,
        created_at__gte=desde,
    ).exclude(estado=RegistroMensajeWhatsApp.EstadoMensaje.ERROR).exists()


def enviar_recordatorio_vehiculo(*, vehiculo, empresa, preferencia, cliente, celular, origen='MANUAL', user=None):
    """Arma el mensaje, lo envía (o simula) y lo registra en la bitácora."""
    mensaje = construir_mensaje_mantenimiento(
        cliente_nombre=cliente.nombre,
        placa=vehiculo.placa,
        marca=vehiculo.marca,
        modelo=vehiculo.modelo,
        kilometraje=vehiculo.kilometraje_actual,
        empresa_nombre=empresa.nombre_comercial,
        plantilla=preferencia.mensaje_plantilla,
        motivo_km=vehiculo.proximo_mantenimiento_km,
        motivo_fecha=vehiculo.proxima_mantenimiento_fecha,
    )
    return whatsapp_enviar_y_registrar(
        empresa=empresa,
        vehiculo=vehiculo,
        celular=celular,
        mensaje=mensaje,
        origen=origen,
        cliente_nombre=cliente.nombre,
        user=user,
    )


def enviar_mensaje_prueba(celular, mensaje=None, *, empresa, user=None):
    """Envía un mensaje de prueba libre hacia cualquier número."""
    cuerpo = construir_mensaje_prueba(celular, mensaje)
    return whatsapp_enviar_y_registrar(
        empresa=empresa,
        celular=celular,
        mensaje=cuerpo,
        origen='PRUEBA',
        cliente_nombre='Prueba',
        user=user,
    )