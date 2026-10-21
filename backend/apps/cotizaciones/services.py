"""Conexión entre la cotización y el flujo operativo del taller.

Una cotización puede nacer sin origen (el cliente pide precio por teléfono o
WhatsApp antes de traer el vehículo). Cuando el auto entra al taller y se
crea la recepción, se vincula automáticamente a esa pieza del flujo la
cotización vigente más reciente del vehículo.

La inspección no se siembra con los ítems de la cotización: sus servicios y
repuestos se capturan directamente en la propia inspección.
"""


def buscar_cotizacion_vigente(vehiculo, empresa=None):
    """Devuelve la cotización vigente más reciente del vehículo, si existe."""
    from apps.cotizaciones.models import Cotizacion

    if vehiculo is None:
        return None
    consulta = Cotizacion.objects.filter(
        vehiculo=vehiculo,
        estado__in=Cotizacion.ESTADOS_VIGENTES,
    )
    if empresa is not None:
        consulta = consulta.filter(empresa_id=getattr(empresa, 'id', empresa))
    return consulta.order_by('-created_at').first()


def vincular_cotizacion(cotizacion, recepcion=None, inspeccion=None):
    """Asigna los orígenes que estén libres. Nunca pisa un vínculo existente."""
    campos = []
    if recepcion is not None and cotizacion.recepcion_origen_id is None:
        cotizacion.recepcion_origen = recepcion
        campos.append('recepcion_origen')
    if inspeccion is not None and cotizacion.inspeccion_origen_id is None:
        cotizacion.inspeccion_origen = inspeccion
        campos.append('inspeccion_origen')
    if campos:
        cotizacion.save(update_fields=[*campos, 'updated_at'])
    return cotizacion


def conectar_recepcion(recepcion, empresa=None):
    """Vincula la recepción con la cotización vigente más reciente del vehículo."""
    cotizacion = buscar_cotizacion_vigente(recepcion.vehiculo, empresa=empresa or recepcion.empresa)
    if cotizacion is None:
        return None
    return vincular_cotizacion(cotizacion, recepcion=recepcion)


def _contexto_operativo(cotizacion):
    inspeccion = cotizacion.inspeccion_origen
    if inspeccion is None and cotizacion.recepcion_origen_id:
        inspeccion = cotizacion.recepcion_origen.inspecciones.order_by('pk').first()
    recepcion = cotizacion.recepcion_origen or (inspeccion.recepcion if inspeccion else None)
    vehiculo = cotizacion.vehiculo or (recepcion.vehiculo if recepcion else None)
    taller = cotizacion._resolver_taller()
    contexto = (cotizacion.empresa_id, taller.pk, cotizacion.cliente_id, getattr(vehiculo, 'pk', None))
    if contexto[-1] is None:
        raise ValueError('No se puede generar la orden de trabajo: falta vehículo.')
    if (cotizacion.cliente.empresa_id != cotizacion.empresa_id
            or taller.empresa_id != cotizacion.empresa_id
            or not vehiculo.vinculo_de(cotizacion.empresa_id)):
        raise ValueError('El cliente, vehículo y taller deben pertenecer a la misma empresa de la cotización.')
    for origen in (recepcion, inspeccion):
        if origen is None:
            continue
        for campo, esperado in zip(('empresa_id', 'sucursal_id', 'cliente_id', 'vehiculo_id'), contexto):
            valor = getattr(origen, campo, None)
            if valor is not None and valor != esperado:
                raise ValueError('El origen de la cotización no corresponde al mismo contexto operativo.')
    return contexto, inspeccion, recepcion


def _visita(inspeccion, recepcion):
    """Visita operativa de un origen: su recepción o, sin ella, la inspección suelta."""
    if recepcion is not None:
        return ('recepcion', recepcion.pk)
    if inspeccion is not None:
        return ('inspeccion', inspeccion.pk)
    return None


def _visitas_de_orden(orden):
    from apps.ordenes.models import InspeccionVehiculo, RecepcionVehiculo

    visitas = {('recepcion', pk) for pk in RecepcionVehiculo.objects.filter(orden_trabajo=orden).values_list('pk', flat=True)}
    inspeccion = InspeccionVehiculo.objects.filter(orden_trabajo=orden).first()
    if inspeccion is not None:
        visitas.add(_visita(inspeccion, inspeccion.recepcion))
    return visitas


def orden_destino_id(cotizacion):
    """OT existente a la que pertenece una cotización: la que la tiene como
    cotización inicial, la OT de diagnóstico de la que surgió o la de su visita."""
    propia = getattr(cotizacion, 'orden_trabajo', None)
    if propia is not None:
        return propia.pk
    if cotizacion.orden_trabajo_origen_id:
        return cotizacion.orden_trabajo_origen_id
    inspeccion = cotizacion.inspeccion_origen
    if inspeccion is not None and inspeccion.orden_trabajo_id:
        return inspeccion.orden_trabajo_id
    recepcion = cotizacion.recepcion_origen or (inspeccion.recepcion if inspeccion else None)
    return recepcion.orden_trabajo_id if recepcion else None


def _incorporar_detalles(orden, servicios, repuestos):
    """Copia líneas individuales; permite seleccionar ítems en una futura aceptación parcial."""
    from apps.ordenes.models import DetalleServicioOrdenTrabajo, DetalleRepuestoOrdenTrabajo

    for detalle in servicios:
        DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=orden, detalle_cotizacion_origen=detalle,
            servicio_codigo=detalle.codigo, descripcion=detalle.descripcion,
            horas_aplicadas=detalle.horas_estimadas, precio_unitario=detalle.precio_unitario,
            descuento=detalle.descuento, iva_porcentaje=detalle.iva_porcentaje,
        )
    for detalle in repuestos:
        DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=orden, detalle_cotizacion_origen=detalle,
            codigo_repuesto=detalle.codigo_repuesto, descripcion=detalle.descripcion,
            cantidad=detalle.cantidad, precio_unitario=detalle.precio_unitario_referencial,
            descuento=detalle.descuento, iva_porcentaje=detalle.iva_porcentaje,
        )


def aplicar_cotizaciones(cotizaciones, orden=None, usuario=None, metodo_aceptacion=None, request=None):
    """Crea una OT o incorpora presupuestos íntegros, sin modificar su ejecución."""
    from django.db import transaction
    from django.utils import timezone
    from .models import Cotizacion
    from apps.ordenes.models import OrdenTrabajo, InspeccionVehiculo, RecepcionVehiculo

    ids = list(dict.fromkeys(c.pk for c in cotizaciones))
    if not ids:
        raise ValueError('Selecciona al menos una cotización.')
    if metodo_aceptacion is not None and (
        not isinstance(metodo_aceptacion, str)
        or metodo_aceptacion not in dict(Cotizacion._meta.get_field('metodo_aceptacion').choices)
    ):
        raise ValueError('Método de aceptación no válido.')

    with transaction.atomic():
        # Siempre bloquear cotizaciones antes que orígenes y OT, en orden de PK.
        bloqueadas = list(Cotizacion.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
        if len(bloqueadas) != len(ids):
            raise ValueError('No se encontraron todas las cotizaciones seleccionadas.')
        por_pk = {c.pk: c for c in bloqueadas}
        seleccionadas = [por_pk[pk] for pk in ids]
        if request is not None:
            accesibles = set(Cotizacion.objects.para_request(request).filter(pk__in=ids).values_list('pk', flat=True))
            if accesibles != set(ids):
                raise ValueError('Las cotizaciones no pertenecen a tu empresa o talleres permitidos.')

        contextos = [_contexto_operativo(c) for c in seleccionadas]
        inspecciones = {i.pk for _, i, _ in contextos if i is not None}
        ins_por_pk = {
            i.pk: i for i in InspeccionVehiculo.all_objects.select_for_update()
            .filter(pk__in=inspecciones).order_by('pk')
        }
        for cotizacion in seleccionadas:
            if cotizacion.inspeccion_origen_id:
                cotizacion.inspeccion_origen = ins_por_pk[cotizacion.inspeccion_origen_id]
        contextos = [_contexto_operativo(c) for c in seleccionadas]
        recepciones = {r.pk for _, _, r in contextos if r is not None}
        rec_por_pk = {
            r.pk: r for r in RecepcionVehiculo.all_objects.select_for_update()
            .filter(pk__in=recepciones).order_by('pk')
        }
        # Releer el contexto de los orígenes ya bloqueados.
        for cotizacion in seleccionadas:
            if cotizacion.inspeccion_origen_id:
                inspeccion = cotizacion.inspeccion_origen
                if inspeccion.recepcion_id:
                    inspeccion.recepcion = rec_por_pk[inspeccion.recepcion_id]
            if cotizacion.recepcion_origen_id:
                cotizacion.recepcion_origen = rec_por_pk[cotizacion.recepcion_origen_id]
        contextos = [_contexto_operativo(c) for c in seleccionadas]
        esperados = contextos[0][0]
        if any(ctx != esperados for ctx, _, _ in contextos):
            raise ValueError('Las cotizaciones deben tener la misma empresa, taller, cliente y vehículo.')

        # Una petición repetida sin destino explícito recupera la OT ya creada.
        aplicadas = {c.orden_trabajo_aplicada_id for c in seleccionadas if c.orden_trabajo_aplicada_id}
        if orden is None and aplicadas:
            if len(aplicadas) != 1 or any(c.orden_trabajo_aplicada_id is None for c in seleccionadas):
                raise ValueError('Indica la OT existente para agregar cotizaciones adicionales.')
            orden = OrdenTrabajo.all_objects.get(pk=aplicadas.pop())
        if orden is not None:
            orden = OrdenTrabajo.all_objects.select_for_update().get(pk=orden.pk)
            if orden.deleted_at is not None or not orden.is_active:
                raise ValueError('La orden de trabajo no está activa.')
            if request is not None and not OrdenTrabajo.objects.para_request(request).filter(pk=orden.pk).exists():
                raise ValueError('La orden no pertenece a tu empresa o talleres permitidos.')
            if (orden.empresa_id, orden.sucursal_id, orden.cliente_id, orden.vehiculo_id) != esperados:
                raise ValueError('La orden y las cotizaciones deben tener la misma empresa, taller, cliente y vehículo.')

        pendientes = []
        for cotizacion, (_, inspeccion, recepcion) in zip(seleccionadas, contextos):
            if cotizacion.orden_trabajo_aplicada_id:
                if orden is None or cotizacion.orden_trabajo_aplicada_id != orden.pk:
                    raise ValueError('La cotización ya está aplicada a otra orden de trabajo.')
                continue
            # Cotización inicial sin trazabilidad (flujo anterior o vínculo manual):
            # solo se incorpora a su propia OT y solo si consta que nada se copió.
            propia = OrdenTrabajo.all_objects.filter(cotizacion_origen=cotizacion).first()
            if propia is not None and orden is None:
                raise ValueError(
                    f'La cotización ya generó la orden {propia.numero_orden}: indica esa OT como destino '
                    'para incorporar sus ítems.'
                )
            if propia is not None and propia.pk != orden.pk:
                raise ValueError(f'La cotización es la cotización inicial de la orden {propia.numero_orden}; no puede aplicarse a otra OT.')
            if propia is not None and (
                orden.servicios.filter(detalle_cotizacion_origen__isnull=True).exists()
                or orden.repuestos.filter(detalle_cotizacion_origen__isnull=True).exists()
            ):
                # Una línea sin procedencia pudo copiarse de esta cotización: no se infiere por sus valores.
                raise ValueError(
                    f'No se puede confirmar si los ítems de la cotización ya se copiaron a la orden {propia.numero_orden}: '
                    'tiene líneas sin cotización de origen. Agrega a la OT los ítems faltantes manualmente.'
                )
            if cotizacion.estado != Cotizacion.EstadoCotizacion.ACEPTADA:
                raise ValueError('La cotización debe estar aceptada para generar una orden.')
            destino = orden_destino_id(cotizacion)
            if orden is None and destino:
                numero = OrdenTrabajo.all_objects.filter(pk=destino).values_list('numero_orden', flat=True).first()
                raise ValueError(
                    f'La cotización pertenece a la orden {numero}: indica esa OT como destino para incorporarla '
                    'en lugar de generar una nueva.'
                )
            for origen in (cotizacion.orden_trabajo_origen,
                           ins_por_pk.get(getattr(inspeccion, 'pk', None)),
                           rec_por_pk.get(getattr(recepcion, 'pk', None))):
                if origen is None:
                    continue
                ot_id = origen.pk if isinstance(origen, OrdenTrabajo) else origen.orden_trabajo_id
                if ot_id and (orden is None or ot_id != orden.pk):
                    raise ValueError('El origen de la cotización está vinculado a otra orden de trabajo.')
            servicios = list(cotizacion.servicios.order_by('pk'))
            repuestos = list(cotizacion.repuestos.order_by('pk'))
            if not servicios and not repuestos:
                raise ValueError('La cotización no tiene servicios ni repuestos.')
            if (cotizacion.servicios.filter(detalle_orden_trabajo__isnull=False).exists()
                    or cotizacion.repuestos.filter(detalle_orden_trabajo__isnull=False).exists()):
                raise ValueError('Algunos ítems de la cotización ya figuran en una orden de trabajo; no se incorporará de nuevo.')
            cotizacion.recalcular_totales()
            if cotizacion.total <= 0:
                raise ValueError('La cotización tiene total 0. Revisa precios y cantidades.')
            pendientes.append((cotizacion, servicios, repuestos, _visita(inspeccion, recepcion)))

        if not pendientes:
            return orden
        if orden is not None and orden.estado not in {'PENDIENTE', 'EN_ESPERA', 'EN_PROCESO'}:
            raise ValueError('Solo se pueden agregar cotizaciones a una OT pendiente, en espera o en proceso.')
        # Una OT corresponde a una sola visita (recepción); las inspecciones de esa visita pueden ser varias.
        visitas = {visita for *_, visita in pendientes if visita is not None}
        if len(visitas) > 1:
            raise ValueError('Las cotizaciones pertenecen a visitas distintas (recepciones diferentes); genera una OT por visita.')
        if orden is not None and visitas and not visitas <= _visitas_de_orden(orden):
            raise ValueError(
                f'La cotización pertenece a una visita distinta de la orden {orden.numero_orden}; '
                'solo se agregan cotizaciones de la misma recepción.'
            )
        creada = orden is None
        if orden is None:
            # La principal es la primera con visita, para que la recepción quede enlazada a la OT.
            principal = next((c for c, *_, visita in pendientes if visita is not None), seleccionadas[0])
            orden = principal._crear_orden_trabajo(usuario=usuario, request=request)
        for cotizacion, servicios, repuestos, _ in pendientes:
            _incorporar_detalles(orden, servicios, repuestos)
            cotizacion.orden_trabajo_aplicada = orden
            cotizacion.fecha_aceptacion = cotizacion.fecha_aceptacion or timezone.now()
            cotizacion.aceptada_por = cotizacion.aceptada_por or usuario
            cotizacion.metodo_aceptacion = cotizacion.metodo_aceptacion or metodo_aceptacion or 'PRESENCIAL'
            cotizacion.save(update_fields=[
                'orden_trabajo_aplicada', 'fecha_aceptacion', 'aceptada_por', 'metodo_aceptacion', 'updated_at',
            ])
        orden.calcular_totales()
        _registrar_aplicacion(request, orden, [c for c, *_ in pendientes], creada)
        return orden


def _registrar_aplicacion(request, orden, cotizaciones, creada):
    """Historial: un evento resumido por cotización, no uno por línea copiada."""
    from apps.core.historial_documentos import Evento, registrar_evento

    if creada:
        numeros = ', '.join(c.numero_cotizacion for c in cotizaciones)
        registrar_evento(request, orden, Evento.CREADO, 'Generada desde cotización', nuevo=numeros)
    for cotizacion in cotizaciones:
        registrar_evento(request, cotizacion, Evento.COTIZACION_APLICADA, 'Aplicada a la orden', nuevo=orden.numero_orden)
        registrar_evento(
            request, orden, Evento.COTIZACION_APLICADA, f'Cotización {cotizacion.numero_cotizacion}',
            nuevo=f'Total {cotizacion.total:.2f}',
        )
