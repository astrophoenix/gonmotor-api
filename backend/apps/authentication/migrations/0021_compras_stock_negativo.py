"""Recurso `compras` y acción especial `stock_negativo` para las bases existentes.

En una base nueva los crea `sembrar_permisos_si_vacio` (señal post_migrate).
Los valores se copian (no se importan) para que la migración no cambie si la
semilla evoluciona. Idempotente: no pisa permisos ni concesiones existentes.
"""

from django.db import migrations

TODOS = {'ver': True, 'modificar': True}

PERMISOS_COMPRAS = {
    'ADMIN_SISTEMA': TODOS,
    'ADMIN_EMPRESA': TODOS,
    'ADMIN_TALLER': TODOS,
    'CAJERO': TODOS,
}

ACCION = (
    'stock_negativo',
    'Despachar sin stock suficiente',
    'Permite despachar repuestos a una orden aunque el stock del almacén quede en negativo.',
    'inventario',
)
ROLES_ACCION = ('ADMIN_SISTEMA', 'ADMIN_EMPRESA', 'ADMIN_TALLER')


def agregar(apps, schema_editor):
    Rol = apps.get_model('authentication', 'Rol')
    Recurso = apps.get_model('authentication', 'Recurso')
    Permiso = apps.get_model('authentication', 'Permiso')
    AccionEspecial = apps.get_model('authentication', 'AccionEspecial')
    PermisoEspecial = apps.get_model('authentication', 'PermisoEspecial')

    if not Rol.objects.exists():
        return

    compras, _ = Recurso.objects.get_or_create(
        codigo='compras', defaults={'nombre': 'Compras', 'tipo': 'MODULO', 'orden': 19},
    )
    for codigo_rol, acciones in PERMISOS_COMPRAS.items():
        rol = Rol.objects.filter(codigo=codigo_rol).first()
        if rol is not None:
            Permiso.objects.get_or_create(rol=rol, recurso=compras, defaults=acciones)

    codigo, nombre, descripcion, recurso = ACCION
    inventario = Recurso.objects.filter(codigo=recurso).first()
    if inventario is None:
        return
    accion, _ = AccionEspecial.objects.get_or_create(
        codigo=codigo, defaults={'nombre': nombre, 'descripcion': descripcion, 'recurso': inventario},
    )
    for rol in Rol.objects.filter(codigo__in=ROLES_ACCION):
        PermisoEspecial.objects.get_or_create(rol=rol, accion=accion)


def quitar(apps, schema_editor):
    apps.get_model('authentication', 'AccionEspecial').objects.filter(codigo=ACCION[0]).delete()
    apps.get_model('authentication', 'Recurso').objects.filter(codigo='compras').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0020_accion_reabrir_caja'),
    ]

    operations = [
        migrations.RunPython(agregar, quitar),
    ]
