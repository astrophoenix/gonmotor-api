from django.db import migrations, models

ESTADOS_VIGENTES = ['PENDIENTE', 'ENVIADA', 'ACEPTADA']


def cerrar_vigentes_duplicados(apps, schema_editor):
    """Deja una sola cotización vigente por vehículo antes de crear el índice.

    Antes de esta migración no existía ninguna restricción por vehículo, así
    que es posible que un mismo auto tenga varias cotizaciones vigentes
    (típico de las creadas directamente por el asesor, sin recepción ni
    inspección de origen). Se conserva la más reciente y las demás pasan a
    VENCIDA para liberar el vehículo, dejando registro en la salida del
    comando de migración.
    """
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')

    vehiculos = (
        Cotizacion.objects.filter(estado__in=ESTADOS_VIGENTES)
        .exclude(vehiculo_id=None)
        .values_list('vehiculo_id', flat=True)
        .distinct()
    )
    for vehiculo_id in vehiculos:
        vigentes = list(
            Cotizacion.objects.filter(vehiculo_id=vehiculo_id, estado__in=ESTADOS_VIGENTES)
            .order_by('-created_at', '-pk')
        )
        if len(vigentes) < 2:
            continue
        conservar = vigentes[0]
        cerrar = vigentes[1:]
        Cotizacion.objects.filter(pk__in=[cot.pk for cot in cerrar]).update(estado='VENCIDA')
        print(
            f'[migración 0014] Vehículo {vehiculo_id}: se conserva la cotización '
            f'{conservar.numero_cotizacion} y se marcan como VENCIDA: '
            f'{", ".join(cot.numero_cotizacion for cot in cerrar)}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0013_cotizacion_asesor'),
    ]

    operations = [
        migrations.RunPython(cerrar_vigentes_duplicados, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='cotizacion',
            constraint=models.UniqueConstraint(
                condition=models.Q(estado__in=['PENDIENTE', 'ENVIADA', 'ACEPTADA']),
                fields=('empresa', 'vehiculo'),
                name='cotizacion_vehiculo_vigente_unica',
            ),
        ),
    ]