from django.db import migrations


def asignar_sucursal(apps, schema_editor):
    """Vincula las cotizaciones sin taller al taller de su documento de origen.

    Mismo criterio que `Cotizacion._resolver_taller`: recepción origen ->
    inspección origen (o su recepción) -> orden de trabajo origen -> taller por
    defecto de la empresa. No se toca `numero_cotizacion`.
    """
    Taller = apps.get_model('empresas', 'Taller')
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')

    def taller_por_id(taller_id):
        if not taller_id:
            return None
        return Taller.objects.filter(pk=taller_id, is_active=True).first()

    def taller_por_defecto(empresa_id):
        if not empresa_id:
            return None
        return (
            Taller.objects.filter(empresa_id=empresa_id, is_active=True)
            .order_by('id')
            .first()
        )

    for cotizacion in Cotizacion.objects.filter(
        sucursal__isnull=True
    ).select_related(
        'recepcion_origen', 'inspeccion_origen__recepcion', 'orden_trabajo_origen'
    ).order_by('created_at', 'id'):
        taller = None

        if cotizacion.recepcion_origen_id:
            taller = taller_por_id(cotizacion.recepcion_origen.sucursal_id)
        if taller is None and cotizacion.inspeccion_origen_id:
            inspeccion = cotizacion.inspeccion_origen
            taller = taller_por_id(inspeccion.sucursal_id)
            if taller is None and inspeccion.recepcion_id:
                taller = taller_por_id(inspeccion.recepcion.sucursal_id)
        if taller is None and cotizacion.orden_trabajo_origen_id:
            taller = taller_por_id(cotizacion.orden_trabajo_origen.sucursal_id)
        if taller is None:
            taller = taller_por_defecto(cotizacion.empresa_id)

        if taller is not None:
            cotizacion.sucursal_id = taller.pk
            cotizacion.save(update_fields=['sucursal'])


class Migration(migrations.Migration):

    dependencies = [
        # Depende del backfill de órdenes para heredar el taller de la recepción
        # origen ya corregida.
        ('ordenes', '0046_backfill_sucursal_documentos'),
        ('cotizaciones', '0018_alter_cotizacion_estado'),
    ]

    operations = [
        migrations.RunPython(asignar_sucursal, reverse_code=migrations.RunPython.noop),
    ]
