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
