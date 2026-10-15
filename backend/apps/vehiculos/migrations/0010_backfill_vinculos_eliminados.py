# Un vehículo desactivado hasta ahora lo estaba para TODAS sus empresas (el
# estado era global). Con el borrado por empresa, ese estado pasa a sus
# vínculos (eliminados) y el vehículo físico vuelve a quedar activo.

from django.db import migrations


def mover_estado_a_vinculos(apps, schema_editor):
    Vehiculo = apps.get_model('vehiculos', 'Vehiculo')
    VehiculoEmpresa = apps.get_model('vehiculos', 'VehiculoEmpresa')
    inactivos = Vehiculo.objects.filter(is_active=False, deleted_at__isnull=True)
    for vehiculo_id, actualizado in inactivos.values_list('id', 'updated_at'):
        VehiculoEmpresa.objects.filter(vehiculo_id=vehiculo_id, deleted_at__isnull=True).update(
            is_active=False, deleted_at=actualizado
        )
    inactivos.update(is_active=True)


def revertir(apps, schema_editor):
    Vehiculo = apps.get_model('vehiculos', 'Vehiculo')
    VehiculoEmpresa = apps.get_model('vehiculos', 'VehiculoEmpresa')
    ids = VehiculoEmpresa.objects.filter(deleted_at__isnull=False).values('vehiculo_id')
    Vehiculo.objects.filter(pk__in=ids).update(is_active=False)
    VehiculoEmpresa.objects.update(is_active=True, deleted_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('vehiculos', '0009_vehiculo_deleted_at_alter_vehiculo_placa_and_more'),
    ]

    operations = [
        migrations.RunPython(mover_estado_a_vinculos, revertir),
    ]
