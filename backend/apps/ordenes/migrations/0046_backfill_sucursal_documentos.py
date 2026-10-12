from django.db import migrations


def _taller_por_defecto(Taller, empresa_id):
    """Primer taller activo de la empresa (mismo criterio que `resolver_taller`)."""
    if not empresa_id:
        return None
    return (
        Taller.objects.filter(empresa_id=empresa_id, is_active=True)
        .order_by('id')
        .first()
    )


def asignar_sucursal(apps, schema_editor):
    """Vincula al taller los documentos que quedaron sin `sucursal`.

    Durante un tiempo los documentos creados vía API recibían su número
    secuencial pero quedaban con `sucursal = NULL` (un `setdefault` no
    sobrescribía el `None` que inyecta DRF). Como el filtrado por taller es
    estricto, esos registros quedaban inaccesibles. Aquí se les asigna el taller
    por defecto de su empresa; las inspecciones lo heredan de su recepción cuando
    existe (criterio exacto). No se tocan los números ya emitidos.
    """
    Taller = apps.get_model('empresas', 'Taller')
    RecepcionVehiculo = apps.get_model('ordenes', 'RecepcionVehiculo')
    InspeccionVehiculo = apps.get_model('ordenes', 'InspeccionVehiculo')
    OrdenTrabajo = apps.get_model('ordenes', 'OrdenTrabajo')

    def taller_por_id(taller_id):
        if not taller_id:
            return None
        return Taller.objects.filter(pk=taller_id, is_active=True).first()

    # Recepciones sin taller -> taller por defecto de su empresa.
    for recepcion in RecepcionVehiculo.objects.filter(
        sucursal__isnull=True
    ).order_by('created_at', 'id'):
        taller = taller_por_id(recepcion.sucursal_id) or _taller_por_defecto(
            Taller, recepcion.empresa_id
        )
        if taller is not None:
            recepcion.sucursal_id = taller.pk
            recepcion.save(update_fields=['sucursal'])

    # Inspecciones sin taller -> el de su recepción; si no, el por defecto.
    for inspeccion in InspeccionVehiculo.objects.filter(
        sucursal__isnull=True
    ).select_related('recepcion').order_by('created_at', 'id'):
        taller = taller_por_id(
            inspeccion.recepcion.sucursal_id if inspeccion.recepcion_id else None
        )
        if taller is None:
            taller = _taller_por_defecto(Taller, inspeccion.empresa_id)
        if taller is not None:
            inspeccion.sucursal_id = taller.pk
            inspeccion.save(update_fields=['sucursal'])

    # Órdenes de trabajo sin taller -> taller por defecto de su empresa.
    for orden in OrdenTrabajo.objects.filter(
        sucursal__isnull=True
    ).order_by('created_at', 'id'):
        taller = taller_por_id(orden.sucursal_id) or _taller_por_defecto(
            Taller, orden.empresa_id
        )
        if taller is not None:
            orden.sucursal_id = taller.pk
            orden.save(update_fields=['sucursal'])


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0045_alter_recepcionvehiculo_tiene_antena_and_more'),
    ]

    operations = [
        migrations.RunPython(asignar_sucursal, reverse_code=migrations.RunPython.noop),
    ]
