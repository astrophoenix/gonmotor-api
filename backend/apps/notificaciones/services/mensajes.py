"""Plantillas de mensajes para recordatorios de mantenimiento."""

from apps.notificaciones.models import PLANTILLA_MANTENIMIENTO_DEFAULT


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
    texto = plantilla.format(
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