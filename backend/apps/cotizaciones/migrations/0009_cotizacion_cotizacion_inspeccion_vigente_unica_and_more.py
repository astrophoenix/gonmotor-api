from django.conf import settings
from django.db import migrations, models

ESTADOS_VIGENTES = ['BORRADOR', 'ENVIADA', 'ACEPTADA']


def desligar_cotizaciones_vigentes_duplicadas(apps, schema_editor):
    """Deja una sola cotización vigente por inspección y por recepción.

    Antes de esta migración no existía ninguna restricción: la misma
    inspección podía acumular cotizaciones ACEPTADAS (caso de una
    recepción con cuatro presupuestos aceptados y una sola orden de
    trabajo). Para poder aplicar los UniqueConstraint parciales, las
    cotizaciones sobrantes se desligan de su origen y quedan como
    cotizaciones directas, tal como se crean cuando el cliente consulta
    por teléfono sin inspección previa.

    Se conserva el vínculo de la cotización que sí generó la orden de
    trabajo; en su defecto, la más antigua del grupo.
    """
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    OrdenTrabajo = apps.get_model('ordenes', 'OrdenTrabajo')
    db_alias = schema_editor.connection.alias

    con_ot = set(
        OrdenTrabajo.objects.using(db_alias)
        .exclude(cotizacion_origen_id=None)
        .values_list('cotizacion_origen_id', flat=True)
    )

    for campo in ('inspeccion_origen_id', 'recepcion_origen_id'):
        grupos = (
            Cotizacion.objects.using(db_alias)
            .filter(estado__in=ESTADOS_VIGENTES)
            .exclude(**{campo: None})
            .values_list(campo)
            .annotate(total=models.Count('id'))
            .filter(total__gt=1)
            .values_list(campo, flat=True)
        )
        for origen_id in grupos:
            cotizaciones = list(
                Cotizacion.objects.using(db_alias)
                .filter(estado__in=ESTADOS_VIGENTES, **{campo: origen_id})
                .order_by('created_at', 'id')
            )
            conservada = next(
                (c for c in cotizaciones if c.id in con_ot),
                cotizaciones[0],
            )
            sobrantes = [c for c in cotizaciones if c.id != conservada.id]
            for cotizacion in sobrantes:
                Cotizacion.objects.using(db_alias).filter(pk=cotizacion.pk).update(
                    **{campo: None}
                )

    # Los UPDATE dejan eventos de trigger diferidos (FK verificadas al final de
    # la transacción) y Postgres no permite crear el índice único hasta que se
    # resuelven; sin esto, el AddConstraint falla con "pending trigger events".
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0005_alter_cliente_identificacion'),
        ('cotizaciones', '0008_detalleserviciocotizacion_codigo'),
        ('empresas', '0006_taller_digitos_cotizacion_taller_digitos_inspeccion_and_more'),
        ('ordenes', '0036_inspeccionvehiculo_responsable'),
        ('vehiculos', '0008_vehiculo_proxima_mantenimiento_fecha_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            desligar_cotizaciones_vigentes_duplicadas,
            # El Estados histórico de las cotizaciones desligadas no se restaura.
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='cotizacion',
            constraint=models.UniqueConstraint(condition=models.Q(('estado__in', ['BORRADOR', 'ENVIADA', 'ACEPTADA'])), fields=('inspeccion_origen',), name='cotizacion_inspeccion_vigente_unica'),
        ),
        migrations.AddConstraint(
            model_name='cotizacion',
            constraint=models.UniqueConstraint(condition=models.Q(('estado__in', ['BORRADOR', 'ENVIADA', 'ACEPTADA'])), fields=('recepcion_origen',), name='cotizacion_recepcion_vigente_unica'),
        ),
    ]
