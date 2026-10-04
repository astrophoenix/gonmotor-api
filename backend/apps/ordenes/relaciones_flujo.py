"""Vínculos del flujo taller compartidos por los editables del módulo.

El flujo no tiene un modelo genérico de relaciones: cada vínculo entre cita,
recepción, inspección, cotización y orden es una FK existente. Este módulo
describe ese grafo en un solo lugar para que los cuatro editables (recepción,
inspección, cotización y orden) muestren y modifiquen exactamente las mismas
relaciones, sin duplicar reglas de negocio ni de validación.
"""

from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.response import Response

from .models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo

TIPOS_FLUJO = ('cita', 'recepcion', 'inspeccion', 'cotizacion', 'orden')

MODELO_POR_TIPO = {
    'cita': ('citas', 'Cita'),
    'recepcion': ('ordenes', 'RecepcionVehiculo'),
    'inspeccion': ('ordenes', 'InspeccionVehiculo'),
    'cotizacion': ('cotizaciones', 'Cotizacion'),
    'orden': ('ordenes', 'OrdenTrabajo'),
}


def _error(detalle, campo='detail'):
    return serializers.ValidationError({campo: detalle})


# ---------------------------------------------------------------------------
# Serialización de cada tipo de entidad relacionada
# ---------------------------------------------------------------------------


def _item_cita(cita):
    return {
        'id': cita.id,
        'label': f'Cita #{cita.id}',
        'numero': f'#{cita.id}',
        'estado': cita.estado,
        'estadoDisplay': cita.get_estado_display(),
        'fecha': cita.fecha_cita.isoformat() if cita.fecha_cita else '',
        'hora': cita.hora_cita.strftime('%H:%M') if cita.hora_cita else '',
        'url': f'/crud/citas/?id={cita.id}',
        'canDelete': True,
    }


def _item_recepcion(recepcion):
    return {
        'id': recepcion.id,
        'label': recepcion.numero_recepcion,
        'numero': recepcion.numero_recepcion,
        'estado': recepcion.estado,
        'estadoDisplay': recepcion.get_estado_display(),
        'url': f'/crud/recepciones/ver/?id={recepcion.id}',
        'canDelete': True,
    }


def _item_inspeccion(inspeccion):
    return {
        'id': inspeccion.id,
        'label': inspeccion.numero_inspeccion or f'#{inspeccion.id}',
        'numero': inspeccion.numero_inspeccion or f'#{inspeccion.id}',
        'estado': inspeccion.estado,
        'estadoDisplay': inspeccion.get_estado_display(),
        'url': f'/crud/inspecciones/ver/?id={inspeccion.id}',
        'canDelete': not bool(inspeccion.orden_trabajo_id),
        'deleteReason': 'La inspección está ligada a una orden.' if inspeccion.orden_trabajo_id else '',
    }


def _item_orden(orden):
    return {
        'id': orden.id,
        'label': orden.numero_orden,
        'numero': orden.numero_orden,
        'estado': orden.estado,
        'estadoDisplay': orden.get_estado_display(),
        'url': f'/crud/ordenes/ver/?id={orden.id}',
        'canDelete': True,
    }


def _orden_de_cotizacion(cotizacion):
    """Orden ligada a la cotización, en cualquiera de los dos sentidos."""
    if cotizacion.orden_trabajo_origen_id:
        return cotizacion.orden_trabajo_origen
    return OrdenTrabajo.objects.filter(cotizacion_origen=cotizacion).first()


def _item_cotizacion(cotizacion):
    orden = _orden_de_cotizacion(cotizacion)
    convertida = cotizacion.estado == 'CONVERTIDA'
    if convertida:
        motivo = 'La cotización ya fue convertida a una orden.'
    elif orden is not None:
        motivo = 'La cotización ya generó una orden de trabajo.'
    else:
        motivo = ''
    return {
        'id': cotizacion.id,
        'label': cotizacion.numero_cotizacion or f'#{cotizacion.id}',
        'numero': cotizacion.numero_cotizacion or f'#{cotizacion.id}',
        'estado': cotizacion.estado,
        'estadoDisplay': cotizacion.get_estado_display(),
        'created_at': cotizacion.created_at.isoformat() if cotizacion.created_at else None,
        'total': str(cotizacion.total or 0),
        'url': f'/crud/cotizaciones/ver/?id={cotizacion.id}',
        'canDelete': not convertida and orden is None,
        'deleteReason': motivo,
    }


# ---------------------------------------------------------------------------
# Lectura del grafo
# ---------------------------------------------------------------------------


def _citas_de_recepcion(recepcion):
    if recepcion is None:
        return []
    citas = apps.get_model('citas', 'Cita').objects.filter(recepcion_generada=recepcion)
    return [_item_cita(cita) for cita in citas.order_by('fecha_cita', 'hora_cita', 'id')]


def _cotizaciones_de_inspeccion(inspeccion):
    cotizaciones = (
        apps.get_model('cotizaciones', 'Cotizacion')
        .objects.filter(inspeccion_origen=inspeccion)
        .select_related('orden_trabajo_origen')
        .order_by('created_at', 'id')
    )
    return [_item_cotizacion(cotizacion) for cotizacion in cotizaciones]


def _relaciones_de_recepcion(recepcion):
    modelo_cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    inspecciones = list(
        recepcion.inspecciones.select_related('orden_trabajo').order_by('created_at', 'id')
    )
    cotizaciones = list(
        modelo_cotizacion.objects.filter(
            Q(recepcion_origen=recepcion) | Q(inspeccion_origen__recepcion=recepcion)
        )
        .select_related('orden_trabajo_origen')
        .distinct()
        .order_by('created_at', 'id')
    )
    orden = recepcion.orden_trabajo if recepcion.orden_trabajo_id else None
    return {
        'cita': (_citas_de_recepcion(recepcion), True),
        'inspeccion': ([_item_inspeccion(i) for i in inspecciones], not inspecciones),
        'cotizacion': ([_item_cotizacion(c) for c in cotizaciones], True),
        'orden': ([_item_orden(orden)] if orden else [], orden is None),
    }


def _relaciones_de_inspeccion(inspeccion):
    recepcion = inspeccion.recepcion if inspeccion.recepcion_id else None
    orden = inspeccion.orden_trabajo if inspeccion.orden_trabajo_id else None
    return {
        'cita': (_citas_de_recepcion(recepcion), recepcion is not None),
        'recepcion': ([_item_recepcion(recepcion)] if recepcion else [], recepcion is None),
        'cotizacion': (_cotizaciones_de_inspeccion(inspeccion), True),
        'orden': ([_item_orden(orden)] if orden else [], orden is None),
    }


def _relaciones_de_cotizacion(cotizacion):
    modelo_cita = apps.get_model('citas', 'Cita')
    inspeccion = cotizacion.inspeccion_origen if cotizacion.inspeccion_origen_id else None
    orden = _orden_de_cotizacion(cotizacion)
    recepcion = cotizacion.recepcion_origen if cotizacion.recepcion_origen_id else None
    # Sin FK directa la recepción se deduce de la inspección de origen: se muestra
    # para dar contexto, pero el vínculo real se edita desde la inspección.
    recepcion_indirecta = recepcion is None and inspeccion is not None and inspeccion.recepcion_id
    if recepcion_indirecta:
        recepcion = inspeccion.recepcion
    citas = (
        modelo_cita.objects.filter(recepcion_generada=recepcion).order_by('fecha_cita', 'hora_cita', 'id')
        if recepcion and not recepcion_indirecta
        else modelo_cita.objects.none()
    )
    item_recepcion = _item_recepcion(recepcion) if recepcion else None
    if item_recepcion and recepcion_indirecta:
        item_recepcion['canDelete'] = False
        item_recepcion['deleteReason'] = 'La cotización llega a esta recepción por su inspección de origen.'
    return {
        'cita': ([_item_cita(cita) for cita in citas], recepcion is not None and not recepcion_indirecta),
        'recepcion': ([item_recepcion] if item_recepcion else [], recepcion is None and not recepcion_indirecta),
        'inspeccion': ([_item_inspeccion(inspeccion)] if inspeccion else [], inspeccion is None),
        'orden': ([_item_orden(orden)] if orden else [], orden is None),
    }


def _o(*condiciones):
    """Une condiciones Q alternando OR sin depender de reduce."""
    combined = condiciones[0]
    for condicion in condiciones[1:]:
        combined = combined | condicion
    return combined


def _recepciones_de_orden(orden):
    condiciones = [
        Q(orden_trabajo=orden),
        Q(inspecciones__orden_trabajo=orden),
        Q(cotizaciones_generadas__orden_trabajo_origen=orden),
    ]
    # Filtrar `pk=None` en un LEFT JOIN devolvería todas las recepciones sin
    # cotizaciones, así que la cotización de origen solo se busca cuando existe.
    if orden.cotizacion_origen_id:
        condiciones.append(Q(cotizaciones_generadas__pk=orden.cotizacion_origen_id))
    return (
        RecepcionVehiculo.objects.filter(_o(*condiciones))
        .distinct()
        .order_by('created_at', 'id')
    )


def _inspeccion_de_orden(orden):
    return getattr(orden, 'inspeccion', None)


def _relaciones_de_orden(orden):
    modelo_cita = apps.get_model('citas', 'Cita')
    modelo_cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    recepciones = list(_recepciones_de_orden(orden))
    inspeccion = _inspeccion_de_orden(orden)
    cotizaciones = list(
        modelo_cotizacion.objects.filter(
            Q(orden_trabajo_origen=orden) | Q(pk=orden.cotizacion_origen_id)
        )
        .select_related('orden_trabajo_origen')
        .distinct()
        .order_by('created_at', 'id')
    )
    citas = modelo_cita.objects.filter(recepcion_generada__in=recepciones).order_by(
        'fecha_cita', 'hora_cita', 'id'
    )
    return {
        'cita': ([_item_cita(cita) for cita in citas], bool(recepciones)),
        'recepcion': ([_item_recepcion(r) for r in recepciones], True),
        'inspeccion': ([_item_inspeccion(inspeccion)] if inspeccion else [], inspeccion is None),
        'cotizacion': (
            [_item_cotizacion(c) for c in cotizaciones],
            orden.cotizacion_origen_id is None,
        ),
    }


LECTORES = {
    'recepcion': _relaciones_de_recepcion,
    'inspeccion': _relaciones_de_inspeccion,
    'cotizacion': _relaciones_de_cotizacion,
    'orden': _relaciones_de_orden,
}


def serializar_relaciones(entidad, tipo_entidad):
    grupos = LECTORES[tipo_entidad](entidad)
    tipos = [tipo for tipo in TIPOS_FLUJO if tipo != tipo_entidad]
    return {
        'relaciones': {tipo: grupos[tipo][0] for tipo in tipos},
        'puede_agregar': {tipo: grupos[tipo][1] for tipo in tipos},
    }


# ---------------------------------------------------------------------------
# Validaciones comunes
# ---------------------------------------------------------------------------


def _validar_misma_visita(entidad, relacionada):
    if getattr(relacionada, 'empresa_id', None) != entidad.empresa_id:
        raise _error('La entidad no pertenece a la empresa actual.')
    if getattr(relacionada, 'cliente_id', None) not in (None, entidad.cliente_id):
        raise _error('La entidad pertenece a otro cliente.')
    if getattr(relacionada, 'vehiculo_id', None) not in (None, entidad.vehiculo_id):
        raise _error('La entidad pertenece a otro vehículo.')


def _validar_cita(cita, destino_id):
    if cita.estado in ('CANCELADA', 'NO_ASISTIO'):
        raise _error('No se puede relacionar una cita cancelada o no asistida.')
    if cita.recepcion_generada_id not in (None, destino_id):
        raise _error('La cita ya está vinculada a otra recepción.')
    return cita


def _validar_cotizacion_libre(cotizacion):
    if cotizacion.estado == 'CONVERTIDA' or _orden_de_cotizacion(cotizacion) is not None:
        raise _error('No se puede relacionar una cotización ya convertida en orden.')


def _buscar_relacionada(tipo, entidad_id, empresa_id):
    app_label, model_name = MODELO_POR_TIPO[tipo]
    return (
        apps.get_model(app_label, model_name)
        .objects.filter(pk=entidad_id, empresa_id=empresa_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Recepción
# ---------------------------------------------------------------------------


def _vincular_desde_recepcion(recepcion, tipo, entidad):
    if tipo == 'orden':
        if recepcion.orden_trabajo_id not in (None, entidad.pk):
            raise _error('La recepción ya tiene otra orden relacionada.')
        recepcion.orden_trabajo = entidad
        recepcion.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'cita':
        entidad = _validar_cita(entidad, recepcion.pk)
        setattr(entidad, 'recepcion_generada', recepcion)
        entidad.save(update_fields=['recepcion_generada', 'updated_at'])
        return

    if tipo == 'inspeccion':
        if entidad.recepcion_id not in (None, recepcion.pk):
            raise _error('La entidad ya está vinculada a otra recepción.')
        if entidad.orden_trabajo_id:
            raise _error('La inspección ya está asociada a una orden.')
        if recepcion.inspecciones.exclude(pk=entidad.pk).exists():
            raise _error('La recepción ya tiene una inspección registrada.')
        entidad.recepcion = recepcion
        entidad.save(update_fields=['recepcion', 'updated_at'])
        return

    if getattr(entidad, 'recepcion_origen_id') not in (None, recepcion.pk):
        raise _error('La entidad ya está vinculada a otra recepción.')
    _validar_cotizacion_libre(entidad)
    if entidad.inspeccion_origen_id and entidad.inspeccion_origen.recepcion_id not in (None, recepcion.pk):
        raise _error('La inspección de origen pertenece a otra recepción.')
    entidad.recepcion_origen = recepcion
    entidad.save(update_fields=['recepcion_origen', 'updated_at'])


def _desvincular_desde_recepcion(recepcion, tipo, entidad):
    if tipo == 'orden':
        if recepcion.orden_trabajo_id != entidad.pk:
            raise _error('La orden no está vinculada a esta recepción.')
        recepcion.orden_trabajo = None
        recepcion.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'cotizacion':
        if OrdenTrabajo.objects.filter(cotizacion_origen=entidad).exists():
            raise _error('No se puede desvincular una cotización que ya generó una orden.')
        campos = []
        if entidad.recepcion_origen_id == recepcion.pk:
            entidad.recepcion_origen = None
            campos.append('recepcion_origen')
        if entidad.inspeccion_origen_id and entidad.inspeccion_origen.recepcion_id == recepcion.pk:
            entidad.inspeccion_origen = None
            campos.append('inspeccion_origen')
        if not campos:
            raise _error('La cotización no está relacionada con esta recepción.')
        entidad.save(update_fields=[*campos, 'updated_at'])
        return

    if tipo == 'inspeccion':
        if entidad.recepcion_id != recepcion.pk:
            raise _error('La entidad no está vinculada a esta recepción.')
        if entidad.orden_trabajo_id:
            raise _error('No se puede desvincular una inspección asociada a una orden.')
        entidad.recepcion = None
        entidad.save(update_fields=['recepcion', 'updated_at'])
        return

    if entidad.recepcion_generada_id != recepcion.pk:
        raise _error('La entidad no está vinculada a esta recepción.')
    entidad.recepcion_generada = None
    entidad.save(update_fields=['recepcion_generada', 'updated_at'])


# ---------------------------------------------------------------------------
# Inspección
# ---------------------------------------------------------------------------


def _vincular_desde_inspeccion(inspeccion, tipo, entidad):
    if tipo == 'recepcion':
        if inspeccion.recepcion_id not in (None, entidad.pk):
            raise _error('La inspección ya está vinculada a otra recepción.')
        if entidad.inspecciones.exclude(pk=inspeccion.pk).exists():
            raise _error('La recepción ya tiene una inspección registrada.')
        inspeccion.recepcion = entidad
        inspeccion.save(update_fields=['recepcion', 'updated_at'])
        return

    if tipo == 'orden':
        if inspeccion.orden_trabajo_id not in (None, entidad.pk):
            raise _error('La inspección ya está asociada a otra orden.')
        if entidad.inspeccion_id and entidad.inspeccion_id != inspeccion.pk:
            raise _error('La orden ya está asociada a otra inspección.')
        inspeccion.orden_trabajo = entidad
        inspeccion.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'cotizacion':
        if entidad.inspeccion_origen_id not in (None, inspeccion.pk):
            raise _error('La cotización ya está vinculada a otra inspección.')
        _validar_cotizacion_libre(entidad)
        if entidad.recepcion_origen_id and entidad.recepcion_origen_id != inspeccion.recepcion_id:
            raise _error('La recepción de origen de la cotización pertenece a otra recepción.')
        entidad.inspeccion_origen = inspeccion
        entidad.save(update_fields=['inspeccion_origen', 'updated_at'])
        return

    if not inspeccion.recepcion_id:
        raise _error('La inspección no tiene recepción: vincula primero la recepción.')
    cita = _validar_cita(entidad, inspeccion.recepcion_id)
    cita.recepcion_generada = inspeccion.recepcion
    cita.save(update_fields=['recepcion_generada', 'updated_at'])


def _desvincular_desde_inspeccion(inspeccion, tipo, entidad):
    if tipo == 'recepcion':
        if inspeccion.recepcion_id != entidad.pk:
            raise _error('La recepción no está vinculada a esta inspección.')
        if inspeccion.orden_trabajo_id:
            raise _error('No se puede desvincular la recepción de una inspección ya asociada a una orden.')
        inspeccion.recepcion = None
        inspeccion.save(update_fields=['recepcion', 'updated_at'])
        return

    if tipo == 'orden':
        if inspeccion.orden_trabajo_id != entidad.pk:
            raise _error('La orden no está vinculada a esta inspección.')
        inspeccion.orden_trabajo = None
        inspeccion.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'cotizacion':
        if entidad.inspeccion_origen_id != inspeccion.pk:
            raise _error('La cotización no está vinculada a esta inspección.')
        _validar_cotizacion_libre(entidad)
        entidad.inspeccion_origen = None
        entidad.save(update_fields=['inspeccion_origen', 'updated_at'])
        return

    if not inspeccion.recepcion_id:
        raise _error('La inspección no tiene recepción.')
    if entidad.recepcion_generada_id != inspeccion.recepcion_id:
        raise _error('La cita no está vinculada a esta recepción.')
    entidad.recepcion_generada = None
    entidad.save(update_fields=['recepcion_generada', 'updated_at'])


# ---------------------------------------------------------------------------
# Cotización
# ---------------------------------------------------------------------------


def _vincular_desde_cotizacion(cotizacion, tipo, entidad):
    if tipo == 'orden':
        if cotizacion.orden_trabajo_origen_id not in (None, entidad.pk):
            raise _error('La cotización ya está vinculada a otra orden.')
        if entidad.cotizacion_origen_id not in (None, cotizacion.pk):
            raise _error('La orden ya tiene otra cotización de origen.')
        cotizacion.orden_trabajo_origen = entidad
        entidad.cotizacion_origen = cotizacion
        cotizacion.save(update_fields=['orden_trabajo_origen', 'updated_at'])
        entidad.save(update_fields=['cotizacion_origen', 'updated_at'])
        return

    if tipo == 'recepcion':
        if cotizacion.recepcion_origen_id not in (None, entidad.pk):
            raise _error('La cotización ya está vinculada a otra recepción.')
        cotizacion.recepcion_origen = entidad
        cotizacion.save(update_fields=['recepcion_origen', 'updated_at'])
        return

    if tipo == 'inspeccion':
        if cotizacion.inspeccion_origen_id not in (None, entidad.pk):
            raise _error('La cotización ya está vinculada a otra inspección.')
        if entidad.orden_trabajo_id:
            raise _error('No se puede relacionar una cotización con una inspección ya asociada a una orden.')
        if entidad.recepcion_id and cotizacion.recepcion_origen_id not in (None, entidad.recepcion_id):
            raise _error('La recepción de la cotización no coincide con la de la inspección.')
        cotizacion.inspeccion_origen = entidad
        cotizacion.save(update_fields=['inspeccion_origen', 'updated_at'])
        return

    if not cotizacion.recepcion_origen_id:
        raise _error('La cotización no tiene recepción de origen: vincula primero la recepción.')
    cita = _validar_cita(entidad, cotizacion.recepcion_origen_id)
    cita.recepcion_generada = cotizacion.recepcion_origen
    cita.save(update_fields=['recepcion_generada', 'updated_at'])


def _desvincular_desde_cotizacion(cotizacion, tipo, entidad):
    if tipo == 'recepcion':
        if cotizacion.recepcion_origen_id != entidad.pk:
            raise _error('La recepción no está vinculada a esta cotización.')
        cotizacion.recepcion_origen = None
        cotizacion.save(update_fields=['recepcion_origen', 'updated_at'])
        return

    if tipo == 'inspeccion':
        if cotizacion.inspeccion_origen_id != entidad.pk:
            raise _error('La inspección no está vinculada a esta cotización.')
        cotizacion.inspeccion_origen = None
        cotizacion.save(update_fields=['inspeccion_origen', 'updated_at'])
        return

    if tipo == 'orden':
        if cotizacion.estado == 'CONVERTIDA':
            raise _error('No se puede quitar el vínculo de una cotización convertida.')
        if cotizacion.orden_trabajo_origen_id != entidad.pk and entidad.cotizacion_origen_id != cotizacion.pk:
            raise _error('La orden no está vinculada a esta cotización.')
        if entidad.cotizacion_origen_id == cotizacion.pk:
            entidad.cotizacion_origen = None
            entidad.save(update_fields=['cotizacion_origen', 'updated_at'])
        if cotizacion.orden_trabajo_origen_id == entidad.pk:
            cotizacion.orden_trabajo_origen = None
            cotizacion.save(update_fields=['orden_trabajo_origen', 'updated_at'])
        return

    if not cotizacion.recepcion_origen_id:
        raise _error('La cotización no tiene recepción de origen.')
    if entidad.recepcion_generada_id != cotizacion.recepcion_origen_id:
        raise _error('La cita no está vinculada a esta recepción.')
    entidad.recepcion_generada = None
    entidad.save(update_fields=['recepcion_generada', 'updated_at'])


# ---------------------------------------------------------------------------
# Orden de trabajo
# ---------------------------------------------------------------------------


def _vincular_desde_orden(orden, tipo, entidad):
    if tipo == 'recepcion':
        if entidad.orden_trabajo_id not in (None, orden.pk):
            raise _error('La recepción ya tiene otra orden relacionada.')
        entidad.orden_trabajo = orden
        entidad.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'inspeccion':
        if getattr(entidad, 'orden_trabajo_id') not in (None, orden.pk):
            raise _error('La inspección ya está asociada a otra orden.')
        inspeccion_actual = _inspeccion_de_orden(orden)
        if inspeccion_actual is not None and inspeccion_actual.pk != entidad.pk:
            raise _error('La orden ya está asociada a otra inspección.')
        entidad.orden_trabajo = orden
        entidad.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'cotizacion':
        if orden.cotizacion_origen_id not in (None, entidad.pk):
            raise _error('La orden ya tiene otra cotización de origen.')
        if entidad.orden_trabajo_origen_id not in (None, orden.pk):
            raise _error('La cotización ya está vinculada a otra orden.')
        orden.cotizacion_origen = entidad
        entidad.orden_trabajo_origen = orden
        orden.save(update_fields=['cotizacion_origen', 'updated_at'])
        entidad.save(update_fields=['orden_trabajo_origen', 'updated_at'])
        return

    recepcion = _recepciones_de_orden(orden).first()
    if recepcion is None:
        raise _error('La orden no tiene recepción relacionada: vincula primero la recepción.')
    cita = _validar_cita(entidad, recepcion.pk)
    cita.recepcion_generada = recepcion
    cita.save(update_fields=['recepcion_generada', 'updated_at'])


def _desvincular_desde_orden(orden, tipo, entidad):
    if tipo == 'recepcion':
        if entidad.orden_trabajo_id != orden.pk:
            raise _error('La recepción no está vinculada a esta orden.')
        entidad.orden_trabajo = None
        entidad.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'inspeccion':
        if getattr(entidad, 'orden_trabajo_id') != orden.pk:
            raise _error('La inspección no está vinculada a esta orden.')
        entidad.orden_trabajo = None
        entidad.save(update_fields=['orden_trabajo', 'updated_at'])
        return

    if tipo == 'cotizacion':
        if entidad.estado == 'CONVERTIDA':
            raise _error('No se puede quitar el vínculo de una cotización convertida.')
        if entidad.orden_trabajo_origen_id != orden.pk and orden.cotizacion_origen_id != entidad.pk:
            raise _error('La cotización no está vinculada a esta orden.')
        if orden.cotizacion_origen_id == entidad.pk:
            orden.cotizacion_origen = None
            orden.save(update_fields=['cotizacion_origen', 'updated_at'])
        if entidad.orden_trabajo_origen_id == orden.pk:
            entidad.orden_trabajo_origen = None
            entidad.save(update_fields=['orden_trabajo_origen', 'updated_at'])
        return

    recepcion = _recepciones_de_orden(orden).first()
    if recepcion is None:
        raise _error('La orden no tiene recepción relacionada.')
    if entidad.recepcion_generada_id != recepcion.pk:
        raise _error('La cita no está vinculada a esta recepción.')
    entidad.recepcion_generada = None
    entidad.save(update_fields=['recepcion_generada', 'updated_at'])


OPERACIONES = {
    'recepcion': (_vincular_desde_recepcion, _desvincular_desde_recepcion),
    'inspeccion': (_vincular_desde_inspeccion, _desvincular_desde_inspeccion),
    'cotizacion': (_vincular_desde_cotizacion, _desvincular_desde_cotizacion),
    'orden': (_vincular_desde_orden, _desvincular_desde_orden),
}


def actualizar_relacion(entidad, tipo_entidad, tipo, entidad_id, accion):
    relacionada = _buscar_relacionada(tipo, entidad_id, entidad.empresa_id)
    if relacionada is None:
        raise _error('No se encontró la entidad en la empresa actual.')
    if accion == 'vincular':
        _validar_misma_visita(entidad, relacionada)
    vincular, desvincular = OPERACIONES[tipo_entidad]
    if accion == 'vincular':
        vincular(entidad, tipo, relacionada)
    else:
        desvincular(entidad, tipo, relacionada)


def aplicar_relacion(entidad, tipo_entidad, data):
    tipo = data.get('tipo')
    entidad_id = data.get('id')
    accion = data.get('accion')
    if tipo not in TIPOS_FLUJO or tipo == tipo_entidad:
        raise _error('Tipo de relación no válido.', campo='tipo')
    if accion not in {'vincular', 'desvincular'}:
        raise _error('Acción no válida.', campo='accion')
    if not entidad_id:
        raise _error('Debes indicar la entidad relacionada.', campo='id')
    try:
        with transaction.atomic():
            actualizar_relacion(entidad, tipo_entidad, tipo, entidad_id, accion)
    except IntegrityError as error:
        raise _error(
            'La relación ya existe o entra en conflicto con otra relación del flujo.'
        ) from error
    return {'ok': True, 'tipo': tipo, 'id': entidad_id, 'accion': accion}


def responder_relaciones(request, entidad, tipo_entidad):
    if request.method == 'GET':
        return Response(serializar_relaciones(entidad, tipo_entidad))
    return Response(aplicar_relacion(entidad, tipo_entidad, request.data))