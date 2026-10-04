"""Servicios de secuencias y numeración de documentos por Taller.

Cada taller tiene su propia secuencia (prefijo, contador y dígitos) porque es la
sucursal desde la que se opera: el número solo debe ser único **dentro del
taller**, que es lo que garantizan las restricciones
`UNIQUE (empresa, sucursal, numero_*)` de los documentos. La asignación bloquea
la fila del taller con `select_for_update()` y siempre devuelve el siguiente
número **disponible** de esa secuencia.
"""

from django.db import transaction

from .models import Taller

# Mapeo tipo -> nombre de los campos en el modelo Taller y en el documento.
TIPO_A_CAMPOS = {
    'recepcion': {
        'numero': 'numero_recepcion',
        'prefijo': 'prefijo_recepcion',
        'siguiente': 'siguiente_recepcion',
        'digitos': 'digitos_recepcion',
    },
    'inspeccion': {
        'numero': 'numero_inspeccion',
        'prefijo': 'prefijo_inspeccion',
        'siguiente': 'siguiente_inspeccion',
        'digitos': 'digitos_inspeccion',
    },
    'cotizacion': {
        'numero': 'numero_cotizacion',
        'prefijo': 'prefijo_cotizacion',
        'siguiente': 'siguiente_cotizacion',
        'digitos': 'digitos_cotizacion',
    },
    'ot': {
        'numero': 'numero_orden',
        'prefijo': 'prefijo_ot',
        'siguiente': 'siguiente_ot',
        'digitos': 'digitos_ot',
    },
}

TIPOS_VALIDOS = tuple(TIPO_A_CAMPOS.keys())

_MODELOS_POR_TIPO = None


def _modelo_documento(tipo):
    """Devuelve el modelo del documento asociado al tipo de secuencia."""
    global _MODELOS_POR_TIPO
    if _MODELOS_POR_TIPO is None:
        from apps.cotizaciones.models import Cotizacion
        from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo

        _MODELOS_POR_TIPO = {
            'recepcion': RecepcionVehiculo,
            'inspeccion': InspeccionVehiculo,
            'cotizacion': Cotizacion,
            'ot': OrdenTrabajo,
        }
    return _MODELOS_POR_TIPO[tipo]


def resolver_taller(empresa, sucursal=None):
    """Resuelve el taller que emite el documento.

    - Si viene `sucursal` (instancia o pk), se usa directamente.
    - Si no, se usa el primer taller activo de la empresa (carga distribuida).
    """
    if sucursal is not None:
        if isinstance(sucursal, Taller):
            return sucursal
        return Taller.objects.filter(pk=sucursal, is_active=True).first()
    if not empresa:
        return None
    empresa_id = empresa.pk if hasattr(empresa, 'pk') else empresa
    return Taller.objects.filter(
        empresa_id=empresa_id, is_active=True
    ).order_by('id').first()


def _siguiente_numero_libre(tipo, campos, taller, prefijo, digitos, contador):
    """Siguiente número **disponible** del taller para el documento.

    Parte del contador del taller, nunca por detrás del último número emitido en
    esa sucursal, y avanza mientras el código ya exista. Así se respetan
    contadores atrasados (taller nuevo, editados a mano) y huecos sin usar.
    """
    siguiente = max(int(contador or 1), ultimo_numero_emitido(taller, tipo) + 1, 1)
    modelo = _modelo_documento(tipo)
    while modelo.objects.filter(
        sucursal=taller,
        **{campos['numero']: f'{prefijo}{siguiente:0{digitos}d}'},
    ).exists():
        siguiente += 1
    return siguiente


def generar_codigo_secuencial(taller, tipo):
    """Asigna el siguiente número disponible del taller para el documento.

    Ejemplo: prefijo `OT-`, siguiente `1500`, dígitos `5` -> `OT-01500`.

    La secuencia es **por taller** (la sucursal desde la que se opera): el número
    solo tiene que ser único dentro de esa sucursal, que es lo que garantizan las
    restricciones `UNIQUE (empresa, sucursal, numero_*)`. Cada llamada bloquea la
    fila del taller con `select_for_update()`, calcula el siguiente número libre
    y avanza el contador, de modo que dos usuarios de la misma sucursal
    simultáneos no obtengan el mismo número.
    """
    campos = TIPO_A_CAMPOS.get(tipo)
    if not campos:
        raise ValueError(f'Tipo de secuencia inválido: {tipo}')
    if taller is None:
        raise ValueError('Se requiere un taller para generar el código secuencial.')

    with transaction.atomic():
        taller_bloqueado = Taller.objects.select_for_update().get(pk=taller.pk)

        prefijo = (getattr(taller_bloqueado, campos['prefijo']) or '').strip()
        digitos = max(int(getattr(taller_bloqueado, campos['digitos']) or 5), 1)
        siguiente = _siguiente_numero_libre(
            tipo,
            campos,
            taller_bloqueado,
            prefijo,
            digitos,
            getattr(taller_bloqueado, campos['siguiente']),
        )

        setattr(taller_bloqueado, campos['siguiente'], siguiente + 1)
        taller_bloqueado.save(update_fields=[campos['siguiente']])

    return f'{prefijo}{siguiente:0{digitos}d}'


def ultimo_numero_emitido(taller, tipo):
    """Número secuencial máximo ya usado por el taller (0 si no hay ninguno).

    Escanea los códigos del documento que usan el prefijo vigente del taller,
    incluidos los de baja lógica: el número también queda reservado para ellos.
    """
    campos = TIPO_A_CAMPOS.get(tipo)
    if not campos or taller is None:
        return 0

    modelo = _modelo_documento(tipo)
    prefijo = (getattr(taller, campos['prefijo']) or '').strip()
    codigos = modelo.objects.filter(
        sucursal=taller,
        **{f'{campos["numero"]}__isnull': False},
    ).values_list(campos['numero'], flat=True)

    maximo = 0
    for codigo in codigos:
        if not codigo:
            continue
        parte = codigo[len(prefijo):] if prefijo else codigo
        if parte.isdigit():
            maximo = max(maximo, int(parte))
    return maximo