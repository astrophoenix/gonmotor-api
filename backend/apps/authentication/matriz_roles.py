"""Matriz declarativa de permisos por rol.

Un permiso es la pareja (recurso, acción). En lugar de repartir `if rol == ...`
por toda la API, aquí los roles viven como datos revisables en un solo archivo:
cambiar quién puede facturar es editar este diccionario, y los tests verifican
que la matriz cubra todos los roles y recursos.

Este módulo es una hoja de dependencias: no importa de `utils` ni de
`permissions`, para que ambos puedan consumirlo sin ciclos.
"""

ACCIONES = ('ver', 'modificar')

SOLO_VER = ('ver',)

RECURSOS = (
    'empresa',          # datos del tenant (RUC, razón social, logo)
    'talleres',         # sucursales / configuración de talleres
    'usuarios',         # cuentas y roles
    'empleados',        # personal de la empresa
    'clientes',
    'vehiculos',
    'proveedores',
    'citas',
    'recepciones',
    'inspecciones',
    'cotizaciones',
    'ordenes',
    'inventario',       # repuestos y servicios
    'facturacion',      # cobros y caja
    'reportes',
)

# Roles que ven TODOS los talleres activos de la empresa: el M2M
# `UsuarioEmpresa.talleres` no los restringe. Los demás roles solo operan en
# los talleres asignados (lista vacía = sin acceso a ningún taller).
ROLES_TODOS_TALLERES = frozenset({'ADMIN_SISTEMA', 'ADMIN_EMPRESA'})

_TODOS = ACCIONES

MATRIZ_ROLES = {
    'ADMIN_SISTEMA': {recurso: _TODOS for recurso in RECURSOS},
    'ADMIN_EMPRESA': {recurso: _TODOS for recurso in RECURSOS},
    # Gerente de taller: opera su sucursal, no la configuración global de la
    # empresa ni las cuentas de los usuarios.
    'ADMIN_TALLER': {
        recurso: _TODOS
        for recurso in RECURSOS
        if recurso not in ('empresa', 'usuarios')
    },
    # Asesor de servicio: recibe, cotiza y acompaña la orden. No cobra.
    'ASESOR': {
        'talleres': SOLO_VER,
        'empleados': SOLO_VER,
        'clientes': _TODOS,
        'vehiculos': _TODOS,
        'proveedores': SOLO_VER,
        'citas': _TODOS,
        'recepciones': _TODOS,
        'inspecciones': _TODOS,
        'cotizaciones': _TODOS,
        'ordenes': _TODOS,
        'inventario': SOLO_VER,
        'reportes': SOLO_VER,
    },
    # Técnico: ejecuta el trabajo y consume repuestos. No cotiza ni cobra.
    'MECANICO': {
        'talleres': SOLO_VER,
        'clientes': SOLO_VER,
        'vehiculos': SOLO_VER,
        'citas': SOLO_VER,
        'recepciones': SOLO_VER,
        'inspecciones': _TODOS,
        'cotizaciones': SOLO_VER,
        'ordenes': _TODOS,
        'inventario': _TODOS,
    },
    # Caja: cobra y consulta. No modifica órdenes ni inventario.
    'CAJERO': {
        'talleres': SOLO_VER,
        'clientes': _TODOS,
        'vehiculos': SOLO_VER,
        'proveedores': SOLO_VER,
        'citas': SOLO_VER,
        'recepciones': SOLO_VER,
        'inspecciones': SOLO_VER,
        'cotizaciones': SOLO_VER,
        'ordenes': SOLO_VER,
        'inventario': SOLO_VER,
        'facturacion': _TODOS,
        'reportes': SOLO_VER,
    },
}


def acciones_de(rol):
    """Copia de los recursos y acciones que `rol` tiene habilitados."""
    return dict(MATRIZ_ROLES.get(rol, {}))


# Nivel jerárquico de cada rol, usado para impedir que un usuario otorgue o
# reciba un rol igual o superior al suyo (no escalación de privilegios).
NIVELES_ROLES = {
    'ADMIN_SISTEMA': 4,
    'ADMIN_EMPRESA': 3,
    'ADMIN_TALLER': 2,
    'ASESOR': 1,
    'MECANICO': 1,
    'CAJERO': 1,
}


def puede_otorgar(rol_emisor, rol_otorgado):
    """True si quien tiene `rol_emisor` puede asignar `rol_otorgado`.

    La regla es: solo se otorgan roles de nivel igual o inferior al propio.
    Un rol desconocido tiene nivel 999 (nadie puede otorgarlo).
    """
    return NIVELES_ROLES.get(rol_otorgado, 999) <= NIVELES_ROLES.get(rol_emisor, 0)


def puede(rol, recurso, accion='modificar'):
    """True si `rol` puede ejecutar `accion` sobre `recurso`.

    Rol o recurso desconocido => False (se niega por defecto). Una acción fuera
    de `ACCIONES` es un error de programación, no un "no permitido".
    """
    if accion not in ACCIONES:
        raise ValueError(
            f'Acción inválida {accion!r}: se espera una de {ACCIONES}.'
        )
    return accion in MATRIZ_ROLES.get(rol, {}).get(recurso, ())
