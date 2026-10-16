"""Recursos `gastos` y `notificaciones` con sus permisos de sistema.

La semilla (`sembrar_permisos_si_vacio`) solo corre sobre una base vacía; esta
migración los agrega a las bases existentes. La matriz se copia aquí (no se
importa de `permisos_repo`) para que la migración no cambie si la matriz
evoluciona. Es idempotente: no pisa permisos que ya existan.
"""

from django.db import migrations

RECURSOS_NUEVOS = (
    # (codigo, nombre, orden)
    ('gastos', 'Gastos', 15),
    ('notificaciones', 'Notificaciones', 16),
)

TODOS = {'ver': True, 'modificar': True}

PERMISOS = {
    'ADMIN_SISTEMA': {'gastos': TODOS, 'notificaciones': TODOS},
    'ADMIN_EMPRESA': {'gastos': TODOS, 'notificaciones': TODOS},
    'ADMIN_TALLER': {'gastos': TODOS, 'notificaciones': TODOS},
    'ASESOR': {'notificaciones': TODOS},
}


def agregar(apps, schema_editor):
    Rol = apps.get_model('authentication', 'Rol')
    Recurso = apps.get_model('authentication', 'Recurso')
    Permiso = apps.get_model('authentication', 'Permiso')

    # Base sin sembrar (p. ej. la de tests): la semilla posterior al migrate
    # ya incluye los recursos nuevos.
    if not Rol.objects.exists():
        return

    recursos = {}
    for codigo, nombre, orden in RECURSOS_NUEVOS:
        recursos[codigo], _ = Recurso.objects.get_or_create(
            codigo=codigo, defaults={'nombre': nombre, 'tipo': 'MODULO', 'orden': orden}
        )

    for codigo_rol, por_recurso in PERMISOS.items():
        rol = Rol.objects.filter(codigo=codigo_rol).first()
        if rol is None:
            continue
        for codigo_recurso, acciones in por_recurso.items():
            Permiso.objects.get_or_create(rol=rol, recurso=recursos[codigo_recurso], defaults=acciones)


def quitar(apps, schema_editor):
    Recurso = apps.get_model('authentication', 'Recurso')
    Recurso.objects.filter(codigo__in=[codigo for codigo, _, _ in RECURSOS_NUEVOS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0012_alter_rolpermiso_rol'),
    ]

    operations = [
        migrations.RunPython(agregar, quitar),
    ]
