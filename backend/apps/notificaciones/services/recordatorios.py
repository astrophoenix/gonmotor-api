"""Lógica para detectar vehículos con mantenimiento próximo y enviar alertas.

Reglas aplicadas:

1. Un vehículo está "próximo a mantenimiento" si:
   - Fecha: tiene `proxima_mantenimiento_fecha` y cae dentro del rango de
     `dias_antelacion` (o ya pasó), o
   - Km: tiene `proximo_mantenimiento_km` y `kilometraje_actual` lo alcanza, o
   - Sin programar: no tiene ninguno de los dos valores y la preferencia de la
     empresa tiene activo `notificar_vehiculos_sin_programar` (demo).
2. Solo se consideran vehículos con un propietario activo (`es_actual=True`)
   cuyo cliente tenga teléfono.
3. No se reenvía el mismo vehículo dentro de `frecuencia_dias` días (se deduce
   de la bitácora `RegistroMensajeWhatsApp`).
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.vehiculos.models import Vehiculo, VehiculoPropietario
from .mensajes import construir_mensaje_mantenimiento, construir_mensaje_prueba
from .telefonos import normalizar_celular
from .whatsapp import whatsapp_enviar_y_registrar


def get_preferencia(empresa):
    """Devuelve (creándola si hace falta) la preferencia de mantenimiento."""
    from apps.notificaciones.models import PreferenciaMantenimiento

    preferencia, _ = PreferenciaMantenimiento.objects.get_or_create(empresa=empresa)
    return preferencia


def _propietario_actual(vehiculo):
    return (
        VehiculoPropietario.objects.filter(
            vehiculo=vehiculo, es_actual=True
        ).select_related('cliente').first()
    )


def _condicion_mantenimiento(vehiculo, preferencia):
    """Devuelve (debe_notificar, motivo, dato) para un vehículo."""
    hoy = timezone.localdate()

    fecha = vehiculo.proxima_mantenimiento_fecha
    if fecha and fecha <= hoy + timedelta(days=preferencia.dias_antelacion):
        return True, 'fecha', fecha.isoformat()

    km = vehiculo.proximo_mantenimiento_km
    if km and vehiculo.kilometraje_actual >= km:
        return True, 'km', km

    if (
        not fecha
        and not km
        and preferencia.notificar_vehiculos_sin_programar
    ):
        return True, 'sin_programar', None

    return False, None, None


def _ya_notificado_recientemente(vehiculo, empresa, frecuencia_dias):
    """Evita duplicar recordatorios según la preferencia de reintento."""
    from apps.notificaciones.models import RegistroMensajeWhatsApp

    desde = timezone.now() - timedelta(days=frecuencia_dias)
    return RegistroMensajeWhatsApp.objects.filter(
        empresa=empresa,
        vehiculo=vehiculo,
        created_at__gte=desde,
    ).exclude(estado=RegistroMensajeWhatsApp.EstadoMensaje.ERROR).exists()


def vehiculos_proximos_mantenimiento(empresa_id, preferencia):
    """Lista los vehículos de una empresa que deberían recibir recordatorio.

    Returns:
        list[dict]: vehículo, propietario/cliente y motivo de la alerta.
    """
    vehiculos = []
    base_query = (
        Vehiculo.objects
        .filter(
            empresas=empresa_id,
            is_active=True,
        )
        .filter(
            Q(propietarios__es_actual=True),
            Q(propietarios__cliente__telefono__isnull=False),
        )
        .exclude(propietarios__cliente__telefono='')
        .distinct()
    )

    for vehiculo in base_query.iterator(chunk_size=200):
        debe, motivo, dato = _condicion_mantenimiento(vehiculo, preferencia)
        if not debe:
            continue
        if _ya_notificado_recientemente(
            vehiculo,
            vehiculo.empresas.filter(pk=empresa_id).first(),
            preferencia.frecuencia_dias,
        ):
            continue

        propietario = _propietario_actual(vehiculo)
        if not propietario or not normalizar_celular(propietario.cliente.telefono):
            continue

        vehiculos.append({
            'vehiculo': vehiculo,
            'cliente': propietario.cliente,
            'motivo': motivo,
            'dato': dato,
            'celular': normalizar_celular(propietario.cliente.telefono),
        })
    return vehiculos


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


def ejecutar_para_empresa(*, empresa, preferencia, preview=False, user=None, vehiculos_ids=None):
    """Recorre los vehículos próximos a mantenimiento y dispara la alerta.

    Args:
        preview: si es True, NO envía nada; solo devuelve los candidatos.
        vehiculos_ids: lista opcional de ids para acotar el envío.

    Returns:
        dict con pendientes, enviados, errores y modo de simulación.
    """
    from .whatsapp import resumen_configuracion

    if not preferencia.notificaciones_activas and not preview:
        return {
            'preview': preview,
            'detenido': True,
            'pendientes': 0,
            'enviados': 0,
            'errores': 0,
            'mensaje': 'Las notificaciones están desactivadas en las preferencias.',
        }

    candidatos = vehiculos_proximos_mantenimiento(empresa.pk, preferencia)
    if vehiculos_ids:
        ids_set = set(int(i) for i in vehiculos_ids)
        candidatos = [c for c in candidatos if c['vehiculo'].pk in ids_set]

    if preview:
        return {
            'preview': True,
            'pendientes': len(candidatos),
            'enviados': 0,
            'errores': 0,
            'simulado': True,
            'modo': resumen_configuracion()['modo'],
            'candidatos': [
                {
                    'vehiculo_id': c['vehiculo'].pk,
                    'placa': c['vehiculo'].placa,
                    'vehiculo': f"{c['vehiculo'].marca} {c['vehiculo'].modelo}",
                    'cliente': c['cliente'].nombre,
                    'celular': f"+{c['celular']}",
                    'motivo': c['motivo'],
                }
                for c in candidatos
            ],
        }

    enviados = err = 0
    errores = []
    for c in candidatos:
        resultado = enviar_recordatorio_vehiculo(
            vehiculo=c['vehiculo'],
            empresa=empresa,
            preferencia=preferencia,
            cliente=c['cliente'],
            celular=c['celular'],
            origen='PROGRAMADO',
            user=user,
        )
        if resultado.get('ok'):
            enviados += 1
        else:
            err += 1
            errores.append({
                'placa': c['vehiculo'].placa,
                'error': resultado.get('error') or resultado.get('descripcion'),
            })

    return {
        'preview': False,
        'pendientes': len(candidatos),
        'enviados': enviados,
        'errores': err,
        'errores_detalle': errores,
        'simulado': resumen_configuracion()['modo'] == 'mock' and enviados > 0,
        'modo': resumen_configuracion()['modo'],
    }


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