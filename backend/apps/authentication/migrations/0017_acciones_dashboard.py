"""Acciones especiales del panel de inicio para las bases ya sembradas.

En una base nueva las crea `sembrar_permisos_si_vacio` (señal post_migrate),
por eso aquí no se hace nada si aún no hay recursos. Idempotente: no duplica
acciones ni concesiones existentes. Los valores se copian (no se importan) para
que la migración no cambie si la semilla evoluciona.
"""

from django.db import migrations

ACCIONES = (
    (
        'dashboard_tendencia_financiera',
        'Ver tendencia financiera y operativa',
        'Muestra en el panel de inicio el gráfico de ingresos y órdenes del periodo.',
        'reportes',
    ),
    (
        'dashboard_facturacion',
        'Ver facturación del mes',
        'Muestra en el panel de inicio la facturación, su variación y el ticket promedio.',
        'reportes',
    ),
    (
        'dashboard_cotizaciones_por_aprobar',
        'Ver cotizaciones por aprobar',
        'Muestra en el panel de inicio las cotizaciones enviadas que esperan respuesta y su monto.',
        'cotizaciones',
    ),
)

ROLES_CON_ACCIONES = ('ADMIN_SISTEMA', 'ADMIN_EMPRESA', 'ADMIN_TALLER', 'ASESOR', 'CAJERO')


def crear_acciones(apps, schema_editor):
    Recurso = apps.get_model('authentication', 'Recurso')
    AccionEspecial = apps.get_model('authentication', 'AccionEspecial')
    PermisoEspecial = apps.get_model('authentication', 'PermisoEspecial')
    Rol = apps.get_model('authentication', 'Rol')

    recursos = {r.codigo: r for r in Recurso.objects.all()}
    if not recursos:
        return
    roles = list(Rol.objects.filter(codigo__in=ROLES_CON_ACCIONES))
    for codigo, nombre, descripcion, recurso in ACCIONES:
        if recurso not in recursos:
            continue
        accion, _ = AccionEspecial.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion, 'recurso': recursos[recurso]},
        )
        for rol in roles:
            PermisoEspecial.objects.get_or_create(rol=rol, accion=accion)


def quitar_acciones(apps, schema_editor):
    AccionEspecial = apps.get_model('authentication', 'AccionEspecial')
    AccionEspecial.objects.filter(codigo__in=[codigo for codigo, *_ in ACCIONES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0016_rol_uuid_publico'),
    ]

    operations = [
        migrations.RunPython(crear_acciones, quitar_acciones),
    ]
