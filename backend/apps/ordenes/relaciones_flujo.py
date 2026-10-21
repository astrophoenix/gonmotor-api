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
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.authentication.permisos_repo import permisos_en_request
from apps.authentication.utils import get_contexto_desde_request

from apps.core.ids_publicos import id_publico, por_id_publico, referencia_corta

from .models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo

TIPOS_FLUJO = ('cita', 'recepcion', 'inspeccion', 'cotizacion', 'orden')

MODELO_POR_TIPO = {
    'cita': ('citas', 'Cita'),
    'recepcion': ('ordenes', 'RecepcionVehiculo'),
    'inspeccion': ('ordenes', 'InspeccionVehiculo'),
    'cotizacion': ('cotizaciones', 'Cotizacion'),
    'orden': ('ordenes', 'OrdenTrabajo'),
}

# Recurso de permisos de cada tipo del flujo (`permisos_repo.RECURSOS`).
RECURSO_POR_TIPO = {
    'cita': 'citas',
    'recepcion': 'recepciones',
    'inspeccion': 'inspecciones',
    'cotizacion': 'cotizaciones',
    'orden': 'ordenes',
}


def _error(detalle, campo='detail'):
    return serializers.ValidationError({campo: detalle})


# ---------------------------------------------------------------------------
# Serialización de cada tipo de entidad relacionada
# ---------------------------------------------------------------------------


def _etiqueta_cita(cita):
    """Las citas no tienen número de documento: se identifican por su horario."""
    if cita.fecha_cita:
        hora = f" {cita.hora_cita.strftime('%H:%M')}" if cita.hora_cita else ''
        return f"Cita {cita.fecha_cita.strftime('%d/%m/%Y')}{hora}"
    return f'Cita {referencia_corta(cita)}'


def _item_cita(cita):
    return {
        'id': id_publico(cita),
        'label': _etiqueta_cita(cita),
        'numero': _etiqueta_cita(cita),
        'estado': cita.estado,
        'estadoDisplay': cita.get_estado_display(),
        'fecha': cita.fecha_cita.isoformat() if cita.fecha_cita else '',
        'hora': cita.hora_cita.strftime('%H:%M') if cita.hora_cita else '',
        'url': f'/crud/citas/?id={id_publico(cita)}',
        'canDelete': True,
    }


def _item_recepcion(recepcion):
    return {
        'id': id_publico(recepcion),
        'label': recepcion.numero_recepcion,
        'numero': recepcion.numero_recepcion,
        'estado': recepcion.estado,
        'estadoDisplay': recepcion.get_estado_display(),
        'url': f'/crud/recepciones/ver/?id={id_publico(recepcion)}',
        'canDelete': True,
    }


def _item_inspeccion(inspeccion):
    return {
        'id': id_publico(inspeccion),
        'label': inspeccion.numero_inspeccion or referencia_corta(inspeccion),
        'numero': inspeccion.numero_inspeccion or referencia_corta(inspeccion),
        'estado': inspeccion.estado,
        'estadoDisplay': inspeccion.get_estado_display(),
        'url': f'/crud/inspecciones/ver/?id={id_publico(inspeccion)}',
        'canDelete': not bool(inspeccion.orden_trabajo_id),
        'deleteReason': 'La inspección está ligada a una orden.' if inspeccion.orden_trabajo_id else '',
    }


def _item_orden(orden):
    return {
        'id': id_publico(orden),
        'label': orden.numero_orden,
        'numero': orden.numero_orden,
        'estado': orden.estado,
        'estadoDisplay': orden.get_estado_display(),
        'url': f'/crud/ordenes/ver/?id={id_publico(orden)}',
        'canDelete': True,
    }


def _orden_de_cotizacion(cotizacion):
    """Orden ligada a la cotización, en cualquiera de los dos sentidos."""
    if cotizacion.orden_trabajo_aplicada_id:
        return cotizacion.orden_trabajo_aplicada
    if cotizacion.orden_trabajo_origen_id:
        return cotizacion.orden_trabajo_origen
    return OrdenTrabajo.objects.filter(cotizacion_origen=cotizacion).first()


def _item_cotizacion(cotizacion):
    convertida = bool(cotizacion.orden_trabajo_aplicada_id) or OrdenTrabajo.objects.filter(cotizacion_origen=cotizacion).exists()
    motivo = 'La cotización ya está incorporada a una orden de trabajo.' if convertida else ''
    return {
        'id': id_publico(cotizacion),
        'label': cotizacion.numero_cotizacion or referencia_corta(cotizacion),
        'numero': cotizacion.numero_cotizacion or referencia_corta(cotizacion),
        'estado': cotizacion.estado,
        'estadoDisplay': cotizacion.get_estado_display(),
        'created_at': cotizacion.created_at.isoformat() if cotizacion.created_at else None,
        'total': str(cotizacion.total or 0),
        'url': f'/crud/cotizaciones/ver/?id={id_publico(cotizacion)}',
        'canDelete': not convertida,
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
        .select_related('orden_trabajo_origen', 'orden_trabajo_aplicada')
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
        .select_related('orden_trabajo_origen', 'orden_trabajo_aplicada')
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
        'inspeccion': ([dict(_item_inspeccion(inspeccion), canDelete=True, deleteReason='')] if inspeccion else [], inspeccion is None),
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


def recepciones_de_orden(orden):
    """Recepciones conectadas a la orden por cualquier camino del flujo."""
    return _recepciones_de_orden(orden)


def _inspeccion_de_orden(orden):
    return getattr(orden, 'inspeccion', None)


def _relaciones_de_orden(orden):
    modelo_cita = apps.get_model('citas', 'Cita')
    modelo_cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    recepciones = list(_recepciones_de_orden(orden))
    inspeccion = _inspeccion_de_orden(orden)
    cotizaciones = list(
        modelo_cotizacion.objects.filter(
            Q(orden_trabajo_origen=orden) | Q(orden_trabajo_aplicada=orden) | Q(pk=orden.cotizacion_origen_id)
        )
        .select_related('orden_trabajo_origen', 'orden_trabajo_aplicada')
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


# Solo se editan las FKs propias de estos documentos. La incorporación a una
# OT pertenece exclusivamente al servicio de consolidación.
VINCULOS_EDITABLES = {
    'recepcion': {'cita'},
    'inspeccion': {'recepcion'},
    'cotizacion': {'recepcion', 'inspeccion', 'orden'},
    'orden': set(),
}


def _motivo_bloqueo(entidad, tipo):
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    if tipo == 'orden':
        return 'Gestiona las cotizaciones desde la ficha de la orden de trabajo.'
    if tipo == 'cotizacion':
        if entidad.orden_trabajo_aplicada_id or OrdenTrabajo.objects.filter(cotizacion_origen=entidad).exists():
            return 'Una cotización incorporada no permite modificar sus relaciones.'
        if entidad.estado not in ('PENDIENTE', 'ENVIADA'):
            return 'Las relaciones solo se pueden corregir mientras la cotización sea editable.'
    if tipo == 'inspeccion':
        if entidad.estado == 'FINALIZADA' or entidad.orden_trabajo_id:
            return 'La inspección finalizada o asociada a una OT conserva sus relaciones.'
        if entidad.recepcion_id and entidad.recepcion.orden_trabajo_id:
            return 'La recepción ya está asociada a una orden de trabajo.'
        if Cotizacion.objects.filter(inspeccion_origen=entidad).filter(
            ~Q(estado__in=('PENDIENTE', 'ENVIADA')) | Q(orden_trabajo_aplicada__isnull=False)
        ).exists():
            return 'La inspección tiene cotizaciones comprometidas y conserva su recepción.'
    if tipo == 'recepcion':
        if entidad.orden_trabajo_id or entidad.estado == 'NO_ACEPTADA' or (
            entidad.aceptacion_condiciones and entidad.fecha_firma_cliente
        ):
            return 'La recepción confirmada o asociada a una OT conserva sus relaciones.'
    return ''


def serializar_relaciones(entidad, tipo_entidad, request):
    grupos = LECTORES[tipo_entidad](entidad)
    contexto = get_contexto_desde_request(request)
    permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
    motivo = _motivo_bloqueo(entidad, tipo_entidad)
    relaciones, agregar, editar = {}, {}, {}
    for tipo in TIPOS_FLUJO:
        if tipo == tipo_entidad:
            continue
        acciones = permisos.get(RECURSO_POR_TIPO[tipo], ())
        modelo = apps.get_model(*MODELO_POR_TIPO[tipo])
        visibles = {str(id_publico(obj)) for obj in modelo.objects.para_request(request).filter(
            uuid__in=[item['id'] for item in grupos[tipo][0]]
        )} if 'ver' in acciones else set()
        editar[tipo] = bool(not motivo and 'modificar' in permisos.get(RECURSO_POR_TIPO[tipo_entidad], ())
                            and tipo in VINCULOS_EDITABLES[tipo_entidad]
                            and 'modificar' in acciones)
        relaciones[tipo] = [dict(item, canDelete=item['canDelete'] and editar[tipo])
                            for item in grupos[tipo][0] if str(item['id']) in visibles]
        agregar[tipo] = editar[tipo] and grupos[tipo][1]
    recepcion = getattr(entidad, 'recepcion_origen', None) or getattr(entidad, 'recepcion', None)
    inspeccion = getattr(entidad, 'inspeccion_origen', None)
    if recepcion is None and inspeccion is not None:
        recepcion = inspeccion.recepcion
    return {'relaciones': relaciones, 'puede_agregar': agregar, 'puede_editar': editar,
            'motivo_bloqueo': motivo,
            'contexto': {campo: id_publico(getattr(entidad, campo, None))
                         for campo in ('empresa', 'sucursal', 'cliente', 'vehiculo')} | {'recepcion': id_publico(recepcion)}}


# ---------------------------------------------------------------------------
# Validaciones comunes
# ---------------------------------------------------------------------------


def _validar_misma_visita(entidad, relacionada):
    if getattr(relacionada, 'empresa_id', None) != entidad.empresa_id:
        raise _error('La entidad no pertenece a la empresa actual.')
    taller = getattr(entidad, 'sucursal_id', None)
    taller_relacionado = getattr(relacionada, 'sucursal_id', getattr(relacionada, 'taller_id', None))
    if taller != taller_relacionado:
        raise _error('La entidad pertenece a otro taller.')
    if entidad.cliente_id and getattr(relacionada, 'cliente_id', None) not in (None, entidad.cliente_id):
        raise _error('La entidad pertenece a otro cliente.')
    if entidad.vehiculo_id and getattr(relacionada, 'vehiculo_id', None) not in (None, entidad.vehiculo_id):
        raise _error('La entidad pertenece a otro vehículo.')


def _validar_cita(cita, destino_id):
    if cita.estado in ('CANCELADA', 'NO_ASISTIO'):
        raise _error('No se puede relacionar una cita cancelada o no asistida.')
    if cita.recepcion_generada_id not in (None, destino_id):
        raise _error('La cita ya está vinculada a otra recepción.')
    return cita


def _buscar_relacionada(tipo, entidad_id, request):
    """Entidad del flujo por su ID público (UUID) dentro del alcance del usuario.

    Empresa activa y talleres permitidos (`para_request`), igual que los
    listados: no se puede enlazar lo que el usuario no puede ver.
    """
    app_label, model_name = MODELO_POR_TIPO[tipo]
    modelo = apps.get_model(app_label, model_name)
    return por_id_publico(modelo.objects.para_request(request), entidad_id).first()


def _exigir_modificar(request, tipo):
    """Un vínculo cambia los dos documentos: hay que poder modificar el relacionado."""
    contexto = get_contexto_desde_request(request)
    permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
    if 'modificar' not in permisos.get(RECURSO_POR_TIPO[tipo], ()):
        raise PermissionDenied('No tienes permiso para modificar el documento relacionado.')


def _vincular_desde_recepcion(recepcion, tipo, cita):
    _validar_cita(cita, recepcion.pk)
    cita.recepcion_generada = recepcion
    cita.save(update_fields=['recepcion_generada', 'updated_at'])


def _desvincular_desde_recepcion(recepcion, tipo, cita):
    if cita.recepcion_generada_id != recepcion.pk:
        raise _error('La cita no está vinculada a esta recepción.')
    cita.recepcion_generada = None
    cita.save(update_fields=['recepcion_generada', 'updated_at'])


def _vincular_desde_inspeccion(inspeccion, tipo, recepcion):
    if inspeccion.recepcion_id not in (None, recepcion.pk):
        raise _error('La inspección ya está vinculada a otra recepción.')
    if recepcion.estado == 'NO_ACEPTADA':
        raise _error('No se puede vincular una recepción no aceptada.')
    if recepcion.orden_trabajo_id:
        raise _error('La recepción ya está asociada a una orden de trabajo.')
    if recepcion.inspecciones.exclude(pk=inspeccion.pk).exists():
        raise _error('La recepción ya tiene una inspección registrada.')
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    from apps.cotizaciones.services import _visitas_de_orden

    for cotizacion in Cotizacion.objects.filter(inspeccion_origen=inspeccion):
        _validar_misma_visita(cotizacion, recepcion)
        if cotizacion.recepcion_origen_id not in (None, recepcion.pk):
            raise _error('La recepción no coincide con la de las cotizaciones vinculadas.')
        if cotizacion.orden_trabajo_origen_id:
            visitas = _visitas_de_orden(cotizacion.orden_trabajo_origen)
            if visitas and ('recepcion', recepcion.pk) not in visitas:
                raise _error('La recepción pertenece a otra visita que la OT vinculada.')
    inspeccion.recepcion = recepcion
    inspeccion.save(update_fields=['recepcion', 'updated_at'])


def _desvincular_desde_inspeccion(inspeccion, tipo, recepcion):
    if inspeccion.recepcion_id != recepcion.pk:
        raise _error('La recepción no está vinculada a esta inspección.')
    inspeccion.recepcion = None
    inspeccion.save(update_fields=['recepcion', 'updated_at'])


CAMPO_COTIZACION = {'recepcion': 'recepcion_origen', 'inspeccion': 'inspeccion_origen', 'orden': 'orden_trabajo_origen'}


def _vincular_desde_cotizacion(cotizacion, tipo, entidad):
    campo = CAMPO_COTIZACION[tipo]
    if getattr(cotizacion, f'{campo}_id') not in (None, entidad.pk):
        raise _error('La cotización ya tiene otro documento vinculado. Quita primero el vínculo editable.')
    if tipo == 'recepcion' and cotizacion.inspeccion_origen_id and cotizacion.inspeccion_origen.recepcion_id not in (None, entidad.pk):
        raise _error('La inspección de origen pertenece a otra recepción.')
    if tipo == 'inspeccion' and entidad.recepcion_id and cotizacion.recepcion_origen_id not in (None, entidad.recepcion_id):
        raise _error('La recepción de la cotización no coincide con la de la inspección.')
    setattr(cotizacion, campo, entidad)
    cotizacion.save(update_fields=[campo, 'updated_at'])


def _desvincular_desde_cotizacion(cotizacion, tipo, entidad):
    campo = CAMPO_COTIZACION[tipo]
    if getattr(cotizacion, f'{campo}_id') != entidad.pk:
        raise _error('El documento no está vinculado directamente a esta cotización.')
    setattr(cotizacion, campo, None)
    cotizacion.save(update_fields=[campo, 'updated_at'])


OPERACIONES = {
    'recepcion': (_vincular_desde_recepcion, _desvincular_desde_recepcion),
    'inspeccion': (_vincular_desde_inspeccion, _desvincular_desde_inspeccion),
    'cotizacion': (_vincular_desde_cotizacion, _desvincular_desde_cotizacion),
}


def _validar_contexto_cotizacion(cotizacion):
    from apps.cotizaciones.services import _visita, _visitas_de_orden

    inspeccion = cotizacion.inspeccion_origen
    recepcion = cotizacion.recepcion_origen or (inspeccion.recepcion if inspeccion else None)
    for origen in (inspeccion, recepcion):
        if origen is not None:
            _validar_misma_visita(cotizacion, origen)
    destinos = {obj.orden_trabajo_id for obj in (inspeccion, recepcion) if obj and obj.orden_trabajo_id}
    if cotizacion.orden_trabajo_origen_id:
        destinos.add(cotizacion.orden_trabajo_origen_id)
    if len(destinos) > 1:
        raise _error('Los documentos pertenecen a distintas órdenes de trabajo.')
    if destinos:
        orden = OrdenTrabajo.objects.get(pk=next(iter(destinos)))
        _validar_misma_visita(cotizacion, orden)
        if orden.estado not in ('PENDIENTE', 'EN_ESPERA', 'EN_PROCESO'):
            raise _error('La orden no admite presupuestos adicionales en su estado actual.')
        visita, visitas = _visita(inspeccion, recepcion), _visitas_de_orden(orden)
        if visita and visitas and visita not in visitas:
            raise _error('Los documentos pertenecen a distintas visitas.')


def actualizar_relacion(entidad, tipo_entidad, tipo, entidad_id, accion, request):
    _exigir_modificar(request, tipo_entidad)
    _exigir_modificar(request, tipo)
    if tipo not in VINCULOS_EDITABLES[tipo_entidad]:
        raise _error('Este vínculo es de consulta. Modifica la relación desde su documento de origen; para incorporar cotizaciones utiliza el flujo de la OT.')
    relacionada = _buscar_relacionada(tipo, entidad_id, request)
    if relacionada is None:
        raise _error('No se encontró la entidad en la empresa y taller actuales.')
    # Mismo orden que la consolidación: cotización, inspección, recepción, OT.
    pares = {tipo_entidad: entidad, tipo: relacionada}
    for clase in ('cotizacion', 'inspeccion', 'recepcion', 'orden', 'cita'):
        if clase in pares:
            obj = pares[clase]
            pares[clase] = type(obj).objects.para_request(request).select_for_update().filter(pk=obj.pk).first()
            if pares[clase] is None:
                raise _error('El documento ya no está disponible.')
    entidad, relacionada = pares[tipo_entidad], pares[tipo]
    motivo = _motivo_bloqueo(entidad, tipo_entidad)
    if motivo:
        raise _error(motivo)
    if accion == 'vincular':
        _validar_misma_visita(entidad, relacionada)
    vincular, desvincular = OPERACIONES[tipo_entidad]
    (vincular if accion == 'vincular' else desvincular)(entidad, tipo, relacionada)
    if tipo_entidad == 'cotizacion':
        _validar_contexto_cotizacion(entidad)


def aplicar_relacion(entidad, tipo_entidad, data, request):
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
            actualizar_relacion(entidad, tipo_entidad, tipo, entidad_id, accion, request)
    except IntegrityError as error:
        raise _error(
            'La relación ya existe o entra en conflicto con otra relación del flujo.'
        ) from error
    return {'ok': True, 'tipo': tipo, 'id': entidad_id, 'accion': accion}


def responder_relaciones(request, entidad, tipo_entidad):
    if request.method == 'GET':
        return Response(serializar_relaciones(entidad, tipo_entidad, request))
    return Response(aplicar_relacion(entidad, tipo_entidad, request.data, request))