"""Servicios de secuencias y numeración de documentos por Taller.

La asignación de códigos es atómica: cada llamada a `generar_codigo_secuencial`
bloquea la fila del taller con `select_for_update()` dentro de una transacción,
incrementa el contador y devuelve el código formateado. Esto evita condiciones
de carrera cuando varios usuarios crean documentos del mismo taller
simultáneamente.
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


def generar_codigo_secuencial(taller, tipo):
    """Asigna atómicamente el siguiente código del taller para el documento.

    Ejemplo: prefijo `OT-`, siguiente `1500`, dígitos `5` -> `OT-01500`.

    Devuelve el código formateado. El contador del taller queda incrementado.
    """
    campos = TIPO_A_CAMPOS.get(tipo)
    if not campos:
        raise ValueError(f'Tipo de secuencia inválido: {tipo}')
    if taller is None:
        raise ValueError('Se requiere un taller para generar el código secuencial.')

    with transaction.atomic():
        taller_bloqueado = Taller.objects.select_for_update().get(pk=taller.pk)
        prefijo = (getattr(taller_bloqueado, campos['prefijo']) or '').strip()
        siguiente = max(int(getattr(taller_bloqueado, campos['siguiente']) or 1), 1)
        digitos = max(int(getattr(taller_bloqueado, campos['digitos']) or 5), 1)

        codigo = f'{prefijo}{siguiente:0{digitos}d}'

        setattr(taller_bloqueado, campos['siguiente'], siguiente + 1)
        taller_bloqueado.save(update_fields=[campos['siguiente']])

    return codigo


def ultimo_numero_emitido(taller, tipo):
    """Devuelve el número secuencial máximo realmente emitido en el taller.

    Escanea los códigos existentes del documento que usan el prefijo actual
    del taller y devuelve el mayor valor numérico (0 si no existe ninguno).
    """
    campos = TIPO_A_CAMPOS.get(tipo)
    if not campos or taller is None:
        return 0

    modelo = _modelo_documento(tipo)
    prefijo = (getattr(taller, campos['prefijo']) or '').strip()
    codigos = modelo.objects.filter(
        sucursal=taller,
        is_active=True,
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