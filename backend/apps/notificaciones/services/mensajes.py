"""Plantillas de los mensajes al cliente (mantenimiento, retiro, citas, campañas)."""

from django.utils import timezone

from apps.notificaciones.models import PLANTILLA_MANTENIMIENTO_DEFAULT


def renderizar_plantilla(plantilla, **valores):
    """Reemplaza solo los placeholders conocidos (`{clave}`).

    A diferencia de `str.format`, una llave suelta o un placeholder desconocido
    escritos por el usuario quedan tal cual en vez de lanzar una excepción.
    """
    texto = plantilla or ''
    for clave, valor in valores.items():
        texto = texto.replace('{' + clave + '}', str(valor))
    return texto


def _vehiculo_texto(vehiculo):
    if vehiculo is None:
        return 'tu vehiculo'
    descripcion = f'{vehiculo.marca or ""} {vehiculo.modelo or ""}'.strip()
    return f'tu vehiculo {descripcion} (placa {vehiculo.placa})' if descripcion else f'tu vehiculo placa {vehiculo.placa}'


def construir_mensaje_retiro(orden):
    """Aviso de que el trabajo terminó y el vehículo puede retirarse."""
    return (
        f'Hola {orden.cliente.nombre}, el taller {orden.empresa.nombre_comercial} te informa que '
        f'{_vehiculo_texto(orden.vehiculo)} ya esta listo para ser retirado '
        f'(orden {orden.numero_orden}). Te esperamos. Gracias por tu confianza.'
    )


def construir_mensaje_saldo(orden, saldo):
    """Recordatorio de cobro de una orden entregada a crédito."""
    return (
        f'Hola {orden.cliente.nombre}, el taller {orden.empresa.nombre_comercial} te recuerda que la orden '
        f'{orden.numero_orden} de {_vehiculo_texto(orden.vehiculo)} tiene un saldo pendiente de '
        f'$ {saldo:,.2f}. Si ya realizaste el pago, por favor ignora este mensaje. Gracias por tu confianza.'
    )


def construir_mensaje_recibo(pago, saldo):
    """Confirmación al cliente de un pago o una devolución registrada."""
    orden = pago.orden_trabajo
    fecha = timezone.localtime(pago.fecha)
    if pago.tipo == 'DEVOLUCION':
        cuerpo = f'te confirma la devolucion de $ {pago.monto:,.2f}'
    else:
        cuerpo = f'confirma la recepcion de tu pago de $ {pago.monto:,.2f}'
    detalle = f' ({pago.get_forma_pago_display()}'
    detalle += f', comprobante {pago.referencia})' if pago.referencia else ')'
    cierre = (
        f' Saldo pendiente: $ {saldo:,.2f}.' if saldo > 0 else ' La orden no tiene saldo pendiente.'
    )
    return (
        f'Hola {orden.cliente.nombre}, el taller {orden.empresa.nombre_comercial} {cuerpo}{detalle} '
        f'el {fecha:%d/%m/%Y} para la orden {orden.numero_orden} de {_vehiculo_texto(orden.vehiculo)}.'
        f'{cierre} Gracias por tu confianza.'
    )


def construir_mensaje_cita(cita, horas_antes=None):
    """Recordatorio de una cita agendada (`horas_antes`: 24, 2 o None si es manual)."""
    momento = timezone.localtime(cita.fecha_hora_programada)
    if horas_antes is None:
        cuando = 'del'
    else:
        cuando = 'manana' if horas_antes >= 24 else 'hoy'
    taller = f' ({cita.taller.nombre})' if cita.taller_id and cita.taller else ''
    return (
        f'Hola {cita.cliente.nombre}, te recordamos tu cita {cuando} {momento:%d/%m/%Y} a las '
        f'{momento:%H:%M} en {cita.empresa.nombre_comercial}{taller} para '
        f'{_vehiculo_texto(cita.vehiculo)}. Si no puedes asistir, respondenos este mensaje.'
    )


def construir_mensaje_campana(campana, cliente):
    """Mensaje de una campaña personalizado para un cliente."""
    return renderizar_plantilla(
        campana.mensaje.strip(),
        cliente=cliente.nombre,
        empresa=campana.empresa.nombre_comercial,
    )


def construir_mensaje_mantenimiento(
    *,
    cliente_nombre,
    placa,
    marca,
    modelo,
    kilometraje,
    empresa_nombre,
    plantilla=None,
    motivo_fecha=None,
    motivo_km=None,
):
    """Construye el texto del recordatorio usando la plantilla de la empresa.

    Reemplaza los placeholders {cliente}, {empresa}, {marca}, {modelo},
    {placa} y {kilometraje}. Si se indica `motivo_fecha` se añade una línea con
    la fecha sugerida; `motivo_km` añade una nota de kilometraje.
    """
    plantilla = (plantilla or PLANTILLA_MANTENIMIENTO_DEFAULT).strip()
    texto = renderizar_plantilla(
        plantilla,
        cliente=cliente_nombre or 'cliente',
        empresa=empresa_nombre or 'el taller',
        marca=marca or '',
        modelo=modelo or '',
        placa=placa or '',
        kilometraje=kilometraje if kilometraje is not None else 's/n',
    )

    if motivo_km:
        texto += f' Indicamos que tu vehiculo ya supero o esta cerca de los {motivo_km} km.'
    if motivo_fecha:
        texto += (
            f' La fecha sugerida para tu mantenimiento es el {motivo_fecha:%d/%m/%Y}.'
        )
    return texto


def construir_mensaje_prueba(celular, mensaje=None):
    """Mensaje de prueba para verificar el proveedor configurado."""
    return mensaje or (
        'Mensaje de prueba desde Gonmotor. '
        'Si estas leyendo esto, la integracion de WhatsApp esta funcionando '
        'correctamente. Respondenos para confirmar.'
    )