from django.db import migrations


def _indexes():
    """(nombre, tabla, columna) de los índices trigram por búsqueda `icontains`.

    Django compila `icontains` en Postgres como `UPPER("col") LIKE UPPER(%s)`,
    por lo que el índice GIN trigram debe ser funcional sobre `upper(col)`.
    """
    return [
        ('idx_clientes_nombre_trgm', 'clientes_cliente', 'nombre'),
        ('idx_clientes_identificacion_trgm', 'clientes_cliente', 'identificacion'),
        ('idx_vehiculos_placa_trgm', 'vehiculos_vehiculo', 'placa'),
        ('idx_repuestos_codigo_trgm', 'inventario_repuesto', 'codigo'),
        ('idx_repuestos_nombre_trgm', 'inventario_repuesto', 'nombre'),
        ('idx_servicios_codigo_trgm', 'inventario_servicio', 'codigo'),
        ('idx_servicios_nombre_trgm', 'inventario_servicio', 'nombre'),
        ('idx_ordentrabajo_numero_trgm', 'ordenes_ordentrabajo', 'numero_orden'),
        ('idx_cotizacion_numero_trgm', 'cotizaciones_cotizacion', 'numero_cotizacion'),
    ]


def create_trigram_indexes(apps, schema_editor):
    statements = ['CREATE EXTENSION IF NOT EXISTS pg_trgm;']
    for name, table, column in _indexes():
        statements.append(
            f'CREATE INDEX IF NOT EXISTS {name} '
            f'ON {table} USING gin (upper({column}) gin_trgm_ops);'
        )
    schema_editor.execute('\n'.join(statements))


def drop_trigram_indexes(apps, schema_editor):
    statements = [f'DROP INDEX IF EXISTS {name};' for name, _, _ in _indexes()]
    schema_editor.execute('\n'.join(statements))


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_remove_asset_asset_type_remove_asset_client_and_more'),
        ('clientes', '0005_alter_cliente_identificacion'),
        ('vehiculos', '0008_vehiculo_proxima_mantenimiento_fecha_and_more'),
        ('inventario', '0001_initial'),
        ('ordenes', '0036_inspeccionvehiculo_responsable'),
        ('cotizaciones', '0008_detalleserviciocotizacion_codigo'),
    ]

    operations = [
        migrations.RunPython(create_trigram_indexes, reverse_code=drop_trigram_indexes),
    ]