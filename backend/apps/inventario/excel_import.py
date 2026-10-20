"""Importación del catálogo de inventario (repuestos y servicios) desde Excel.

Ambos son catálogos de la empresa identificados por un código único: se
apoyan en `ImportadorCatalogo` (sobre `ImportadorPorClave` del núcleo
`apps.core.importacion`), con estas reglas:

- El código se guarda en mayúsculas (como la pantalla) y define el duplicado.
- Si el código existe, solo cambian las celdas con valor; uno desactivado no
  se reactiva (la fila sale con su motivo).
- Repuestos: el stock solo se toma al CREAR. En uno existente no se pisa (lo
  cambia el inventario, no un Excel que puede estar desactualizado) y la fila
  avisa de la diferencia.
"""

from decimal import Decimal

from apps.core.importacion import Columna, ImportadorPorClave, limpiar

from .models import Repuesto, Servicio

# Tarifas de IVA que admite la web (`shared/utils/impuestos.js`).
TARIFAS_IVA = (('15', '15%'), ('0', '0%'))
_IVA_DECIMAL = {'15': Decimal('0.1500'), '0': Decimal('0.0000')}


def a_iva(valor):
    """Tarifa de IVA desde «15», «15%», «0.15», «0» o «0%»."""
    texto = limpiar(valor).replace('%', '').replace(',', '.').strip()
    if not texto:
        return None
    try:
        numero = Decimal(texto)
    except Exception:
        numero = None
    if numero is not None and numero <= 1:
        numero *= 100  # 0.15 → 15
    clave = str(int(numero)) if numero is not None and numero == int(numero) else None
    if clave not in _IVA_DECIMAL:
        raise ValueError(f'IVA: "{limpiar(valor)}" no es válido. Usa 15 o 0.')
    return _IVA_DECIMAL[clave]


class ImportadorCatalogo(ImportadorPorClave):
    """Catálogo de inventario: código único por empresa y tarifa de IVA propia."""

    campo_clave = 'codigo'
    nombre_clave = 'el código'
    obligatorias = ('codigo', 'nombre')

    def convertir(self, tipo, valor, columna):
        if tipo == 'iva':
            return a_iva(valor)
        return super().convertir(tipo, valor, columna)


class ImportadorRepuestos(ImportadorCatalogo):
    modelo = Repuesto
    hoja = 'Importación Repuestos'
    entidad = 'repuestos'
    columnas = [
        Columna('codigo', 'Código', alias=('codigo del repuesto', 'sku')),
        Columna('nombre', 'Nombre', alias=('nombre del repuesto', 'descripcion corta')),
        Columna('categoria', 'Categoría', opciones=tuple(Repuesto.Categoria.choices)),
        Columna('marca', 'Marca', alias=('fabricante', 'marca / fabricante')),
        Columna('numero_parte', 'Número de Parte', alias=('n de parte', 'numero de parte original')),
        Columna('unidad_medida', 'Unidad', alias=('unidad de medida',), opciones=tuple(Repuesto.UnidadMedida.choices)),
        Columna('costo_referencial', 'Costo', alias=('costo referencial', 'costo de compra')),
        Columna('precio_venta', 'Precio de Venta', alias=('precio', 'pvp')),
        Columna('iva_porcentaje_defecto', 'IVA', alias=('iva %', 'iva (%)'), opciones=TARIFAS_IVA),
        Columna('stock_actual', 'Stock', alias=('stock actual', 'existencias')),
        Columna('stock_minimo', 'Stock Mínimo'),
        Columna('ubicacion', 'Ubicación', alias=('estante', 'bodega')),
        Columna('proveedor', 'Proveedor', alias=('proveedor habitual',)),
        Columna('descripcion', 'Descripción'),
        Columna('contifico_producto_id', 'ID Contífico'),
        Columna('contifico_cuenta_contable', 'Cuenta Contable Contífico'),
    ]
    tipos = {
        'categoria': Repuesto.Categoria.choices,
        'unidad_medida': Repuesto.UnidadMedida.choices,
        'costo_referencial': 'decimal',
        'precio_venta': 'decimal',
        'iva_porcentaje_defecto': 'iva',
        'stock_actual': 'decimal',
        'stock_minimo': 'decimal',
    }
    instrucciones = [
        'Obligatorios: Código y Nombre. El resto es opcional.',
        'El código identifica al repuesto: si ya existe, solo se actualizan las celdas con valor.',
        'El Stock solo se usa al crear el repuesto; en uno existente no se cambia (ajústalo desde el inventario).',
        'Precios y stock con punto o coma decimal (12.50 o 12,50). IVA: 15 o 0.',
    ]
    ejemplos = [
        ['FIL-001', 'Filtro de aceite', 'FILTROS', 'Bosch', '0451103316', 'UNIDAD',
         '4.50', '8.00', '15', '20', '5', 'Estante A1', 'Repuestos del Valle', '', '', ''],
    ]

    def ajustar_existente(self, existente, datos):
        stock = datos.pop('stock_actual', None)
        if stock is not None and stock != existente.stock_actual:
            return (
                f'El stock no se cambió: el registrado es {existente.stock_actual} '
                f'(el archivo dice {stock}). Ajústalo desde el inventario.'
            )
        return ''


class ImportadorServicios(ImportadorCatalogo):
    modelo = Servicio
    hoja = 'Importación Servicios'
    entidad = 'servicios'
    columnas = [
        Columna('codigo', 'Código', alias=('codigo del servicio',)),
        Columna('nombre', 'Nombre', alias=('nombre del servicio',)),
        Columna('categoria', 'Categoría', opciones=tuple(Servicio.Categoria.choices)),
        Columna('tiempo_estimado_minutos', 'Tiempo (minutos)', alias=('tiempo estimado', 'tiempo', 'minutos')),
        Columna('precio_referencial', 'Precio', alias=('precio referencial', 'precio de mano de obra')),
        Columna('costo_referencial', 'Costo', alias=('costo referencial',)),
        Columna('iva_porcentaje_defecto', 'IVA', alias=('iva %', 'iva (%)'), opciones=TARIFAS_IVA),
        Columna('tareas_estandar', 'Tareas Incluidas', alias=('tareas estandar', 'tareas')),
        Columna('descripcion', 'Descripción'),
        Columna('contifico_producto_id', 'ID Contífico'),
    ]
    tipos = {
        'categoria': Servicio.Categoria.choices,
        'tiempo_estimado_minutos': 'entero',
        'precio_referencial': 'decimal',
        'costo_referencial': 'decimal',
        'iva_porcentaje_defecto': 'iva',
    }
    instrucciones = [
        'Obligatorios: Código y Nombre. El resto es opcional.',
        'El código identifica al servicio: si ya existe, solo se actualizan las celdas con valor.',
        'Precios con punto o coma decimal (25.00 o 25,00). Tiempo en minutos. IVA: 15 o 0.',
    ]
    ejemplos = [
        ['MO-001', 'Cambio de aceite y filtro', 'MANTENIMIENTO', '45', '15.00', '8.00', '15',
         'Drenar aceite, cambiar filtro, revisar niveles', '', ''],
    ]
