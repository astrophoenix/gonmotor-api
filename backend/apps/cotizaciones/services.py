"""Conexión entre la cotización y el flujo operativo del taller.

Una cotización puede nacer sin origen (el cliente pide precio por teléfono o
WhatsApp antes de traer el vehículo). Cuando el auto entra al taller y se
crea la recepción y la inspección, se vincula automáticamente a esas piezas
del flujo la cotización vigente más reciente del vehículo.
"""

from decimal import Decimal


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
        campos.append('updated_at')
        cotizacion.save(update_fields=campos)
    return cotizacion


def conectar_recepcion(recepcion, empresa=None):
    """Vincula la recepción con la cotización vigente más reciente del vehículo."""
    cotizacion = buscar_cotizacion_vigente(recepcion.vehiculo, empresa=empresa or recepcion.empresa)
    if cotizacion is None:
        return None
    return vincular_cotizacion(cotizacion, recepcion=recepcion)


def conectar_inspeccion(inspeccion, con_siembra=True):
    """Vincula la inspección con la cotización vigente y siembra sus ítems.

    Los servicios y repuestos de la cotización se copian a la inspección para
    que el técnico parta con el plan de trabajo acordado y agregue encima lo
    que revele el vehículo.
    """
    recepcion = inspeccion.recepcion
    vehiculo = inspeccion.vehiculo or (recepcion.vehiculo if recepcion else None)
    empresa = inspeccion.empresa or (recepcion.empresa if recepcion else None)
    cotizacion = buscar_cotizacion_vigente(vehiculo, empresa=empresa)
    if cotizacion is None:
        return None
    if recepcion is not None:
        vincular_cotizacion(cotizacion, recepcion=recepcion)
    # La siembra solo aplica si esta inspección es el destino del vínculo (una
    # cotización ya atada a otra inspección no se replica).
    if cotizacion.inspeccion_origen_id in (None, inspeccion.pk):
        if con_siembra:
            sembrar_inspeccion_desde_cotizacion(cotizacion, inspeccion)
        vincular_cotizacion(cotizacion, inspeccion=inspeccion)
    return cotizacion


def sembrar_inspeccion_desde_cotizacion(cotizacion, inspeccion):
    """Copia los ítems de la cotización a la inspección. Devuelve cuántos copió.

    La inspección guarda precios referenciales sin fijar el precio comercial, por
    eso el precio unitario de la cotización alimenta `precio_referencial`. Los
    ítems opcionales de la cotización se marcan como sugeridos en la inspección.
    """
    from apps.inventario.models import Repuesto, Servicio
    from apps.ordenes.models import DetalleRepuestoInspeccion, DetalleServicioInspeccion

    servicios_cotizacion = list(cotizacion.servicios.all())
    repuestos_cotizacion = list(cotizacion.repuestos.all())
    if not servicios_cotizacion and not repuestos_cotizacion:
        return 0
    if inspeccion.servicios_detectados.exists() or inspeccion.repuestos_sugeridos.exists():
        return 0

    catalogo_servicios = {
        servicio.codigo: servicio
        for servicio in Servicio.objects.filter(
            codigo__in=[det.codigo for det in servicios_cotizacion if det.codigo]
        )
    }
    for det in servicios_cotizacion:
        DetalleServicioInspeccion.objects.create(
            inspeccion=inspeccion,
            servicio=catalogo_servicios.get(det.codigo),
            descripcion=det.descripcion,
            horas_estimadas=det.horas_estimadas,
            precio_referencial=Decimal(det.precio_unitario or '0.00'),
            es_sugerido=det.es_opcional,
        )

    catalogo_repuestos = {
        repuesto.codigo: repuesto
        for repuesto in Repuesto.objects.filter(
            codigo__in=[det.codigo_repuesto for det in repuestos_cotizacion if det.codigo_repuesto]
        )
    }
    for det in repuestos_cotizacion:
        DetalleRepuestoInspeccion.objects.create(
            inspeccion=inspeccion,
            repuesto=catalogo_repuestos.get(det.codigo_repuesto),
            descripcion=det.descripcion,
            cantidad=det.cantidad,
            precio_referencial=Decimal(det.precio_unitario_referencial or '0.00'),
            es_sugerido=det.es_opcional,
        )

    return len(servicios_cotizacion) + len(repuestos_cotizacion)