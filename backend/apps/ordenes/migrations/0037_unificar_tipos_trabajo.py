"""Unifica la taxonomía de tipos de Recepción, Inspección y Orden de Trabajo.

Antes cada modelo tenía su propio vocabulario para la misma pregunta de negocio
("¿por qué entra el vehículo?"):

- Recepción:    MANTENIMIENTO, 'REPARACIÓN' (con tilde), DIAGNOSTICO, ESTETICA, GARANTIA, SINIESTRO, OTRO
- Inspección:   PREVENTIVO, CORRECTIVO, DIAGNOSTICO, ESTETICA, GARANTIA
- OrdenTrabajo: PREVENTIVO, CORRECTIVO, DIAGNOSTICO, ESTETICA, GARANTIA

Ahora los tres usan `TipoTrabajo` con 7 valores en ASCII. Esta migración convierte
los datos existentes y ajusta defaults (incluido el inválido 'PENDIENTE' que
`tipo_recepcion` tenía por error, copiado de ESTADO_CHOICES).
"""

from django.db import migrations, models

# 'REPARACIÓN' con la Ó descompuesta (NFD: 'O' + U+0301), por si algún registro
# se guardó con esa normalización.
REPARACION_NFD = 'REPARACIO' + '\u0301' + 'N'

CANONICOS = [
    ('MANTENIMIENTO', 'Mantenimiento'),
    ('REPARACION', 'Reparación'),
    ('DIAGNOSTICO', 'Diagnóstico'),
    ('ESTETICA', 'Estética'),
    ('GARANTIA', 'Garantía'),
    ('SINIESTRO', 'Siniestro'),
    ('OTRO', 'Otro'),
]

# Valores legacy -> valor canónico, para los tres modelos.
CONVERSION = {
    'PREVENTIVO': 'MANTENIMIENTO',
    'CORRECTIVO': 'REPARACION',
    'REPARACIÓN': 'REPARACION',
    REPARACION_NFD: 'REPARACION',
    'REPARACION': 'REPARACION',
    'MANTENIMIENTO': 'MANTENIMIENTO',
    'PENDIENTE': 'MANTENIMIENTO',  # default inválido heredado de ESTADO_CHOICES
    '': 'MANTENIMIENTO',
}


def normalizar_tipos(apps, schema_editor):
    for modelo, campo in (
        ('RecepcionVehiculo', 'tipo_recepcion'),
        ('InspeccionVehiculo', 'tipo_inspeccion'),
        ('OrdenTrabajo', 'tipo_trabajo'),
    ):
        model = apps.get_model('ordenes', modelo)
        for legacy, canonico in CONVERSION.items():
            model.objects.filter(**{campo: legacy}).update(**{campo: canonico})
        # Cualquier valor que no sea canónico (basura previa) vuelve al default.
        validos = [valor for valor, _ in CANONICOS]
        model.objects.exclude(**{f'{campo}__in': validos}).update(
            **{campo: 'MANTENIMIENTO'}
        )


def normalizar_tipos_reversa(apps, schema_editor):
    """Revierte a los vocabularios previos (pérdida inevitable: SINIESTRO y OTRO)."""
    ordenes = apps.get_model('ordenes', 'OrdenTrabajo')
    ordenes.objects.filter(tipo_trabajo='MANTENIMIENTO').update(tipo_trabajo='PREVENTIVO')
    ordenes.objects.filter(tipo_trabajo='REPARACION').update(tipo_trabajo='CORRECTIVO')

    inspecciones = apps.get_model('ordenes', 'InspeccionVehiculo')
    inspecciones.objects.filter(tipo_inspeccion='MANTENIMIENTO').update(tipo_inspeccion='PREVENTIVO')
    inspecciones.objects.filter(tipo_inspeccion='REPARACION').update(tipo_inspeccion='CORRECTIVO')
    inspecciones.objects.filter(tipo_inspeccion__in=['SINIESTRO', 'OTRO']).update(tipo_inspeccion='DIAGNOSTICO')

    recepciones = apps.get_model('ordenes', 'RecepcionVehiculo')
    recepciones.objects.filter(tipo_recepcion='REPARACION').update(tipo_recepcion='REPARACIÓN')
    recepciones.objects.filter(tipo_recepcion='SINIESTRO').update(tipo_recepcion='OTRO')


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0036_inspeccionvehiculo_responsable'),
    ]

    operations = [
        migrations.RunPython(normalizar_tipos, normalizar_tipos_reversa),
        migrations.AlterField(
            model_name='recepcionvehiculo',
            name='tipo_recepcion',
            field=models.CharField(
                choices=CANONICOS,
                default='MANTENIMIENTO',
                max_length=20,
                verbose_name='Tipo de Recepción',
            ),
        ),
        migrations.AlterField(
            model_name='inspeccionvehiculo',
            name='tipo_inspeccion',
            field=models.CharField(choices=CANONICOS, default='DIAGNOSTICO', max_length=20),
        ),
        migrations.AlterField(
            model_name='ordentrabajo',
            name='tipo_trabajo',
            field=models.CharField(choices=CANONICOS, default='MANTENIMIENTO', max_length=20),
        ),
    ]
