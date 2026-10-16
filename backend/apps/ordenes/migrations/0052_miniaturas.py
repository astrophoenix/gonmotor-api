"""Miniaturas de las fotos y firma del cliente como archivo PNG.

La firma viajaba en base64 dentro de cada recepción (también en los listados
y en las recepciones anidadas de la orden). Se pasa a un archivo: el campo
nuevo se rellena decodificando cada data URL y reemplaza al de texto.
"""
import base64
import binascii
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations, models


CARPETA_FIRMAS = 'ordenes/firmas/'


def firmas_a_archivo(apps, schema_editor):
    Recepcion = apps.get_model('ordenes', 'RecepcionVehiculo')
    pendientes = (
        Recepcion._base_manager.exclude(firma_cliente__isnull=True).exclude(firma_cliente='')
        .values_list('pk', 'firma_cliente')
    )
    for pk, data_url in pendientes.iterator():
        try:
            _, contenido = data_url.split(',', 1)
            datos = base64.b64decode(contenido, validate=True)
        except (ValueError, binascii.Error):
            # Firma ilegible: se descarta (la recepción conserva su estado y fecha).
            continue
        nombre = default_storage.save(f'{CARPETA_FIRMAS}{uuid.uuid4().hex}.png', ContentFile(datos))
        Recepcion._base_manager.filter(pk=pk).update(firma_archivo=nombre)


def firmas_a_texto(apps, schema_editor):
    Recepcion = apps.get_model('ordenes', 'RecepcionVehiculo')
    pendientes = (
        Recepcion._base_manager.exclude(firma_archivo__isnull=True).exclude(firma_archivo='')
        .values_list('pk', 'firma_archivo')
    )
    for pk, nombre in pendientes.iterator():
        try:
            with default_storage.open(nombre, 'rb') as archivo:
                datos = archivo.read()
        except OSError:
            continue
        data_url = 'data:image/png;base64,' + base64.b64encode(datos).decode()
        Recepcion._base_manager.filter(pk=pk).update(firma_cliente=data_url)


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0051_uuid_publico'),
    ]

    operations = [
        migrations.AddField(
            model_name='fotoinspeccion',
            name='miniatura',
            field=models.ImageField(blank=True, null=True, upload_to='ordenes/inspeccion_fotos/miniaturas/'),
        ),
        migrations.AddField(
            model_name='fotoordentrabajo',
            name='miniatura',
            field=models.ImageField(blank=True, null=True, upload_to='ordenes/orden_fotos/miniaturas/'),
        ),
        migrations.AddField(
            model_name='fotorecepcion',
            name='miniatura',
            field=models.ImageField(blank=True, null=True, upload_to='ordenes/recepcion_fotos/miniaturas/'),
        ),
        migrations.AddField(
            model_name='recepcionvehiculo',
            name='firma_archivo',
            field=models.ImageField(blank=True, null=True, upload_to=CARPETA_FIRMAS),
        ),
        migrations.RunPython(firmas_a_archivo, firmas_a_texto),
        migrations.RemoveField(
            model_name='recepcionvehiculo',
            name='firma_cliente',
        ),
        migrations.RenameField(
            model_name='recepcionvehiculo',
            old_name='firma_archivo',
            new_name='firma_cliente',
        ),
        migrations.AlterField(
            model_name='recepcionvehiculo',
            name='firma_cliente',
            field=models.ImageField(blank=True, help_text='Firma digital del cliente aceptando la recepción (PNG)', null=True, upload_to='ordenes/firmas/', verbose_name='Firma del cliente'),
        ),
    ]
