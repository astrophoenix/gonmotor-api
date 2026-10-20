"""Importación de proveedores desde Excel.

Proveedor = registro de la empresa con identificación única: usa
`ImportadorPorClave` del núcleo (`apps.core.importacion`), con las mismas
reglas que la pantalla: identificación en mayúsculas, si existe solo cambian
las celdas con valor y uno desactivado no se reactiva.
"""

from apps.core.importacion import Columna, ImportadorPorClave

from .models import Proveedor


class ImportadorProveedores(ImportadorPorClave):
    modelo = Proveedor
    hoja = 'Importación Proveedores'
    entidad = 'proveedores'
    campo_clave = 'identificacion'
    nombre_clave = 'la identificación'
    obligatorias = ('identificacion', 'nombre')
    columnas = [
        Columna('tipo_identificacion', 'Tipo Identificación', opciones=tuple(Proveedor.TIPO_IDENTIFICACION)),
        Columna('identificacion', 'Identificación', alias=('ruc', 'cedula', 'documento')),
        Columna('nombre', 'Nombre', alias=('razon social', 'nombre o razon social', 'razon social / nombre')),
        Columna('email', 'Email', alias=('correo', 'correo electronico')),
        Columna('telefono', 'Teléfono', alias=('telefono / whatsapp', 'whatsapp', 'celular')),
        Columna('direccion', 'Dirección'),
        Columna('contacto', 'Persona de Contacto', alias=('contacto',)),
    ]
    tipos = {
        'tipo_identificacion': Proveedor.TIPO_IDENTIFICACION,
        'email': 'email',
    }
    # Igual que el modelo: un proveedor suele tener RUC.
    por_defecto = {'tipo_identificacion': 'R'}
    instrucciones = [
        'Obligatorios: Identificación y Nombre. El resto es opcional.',
        'La identificación identifica al proveedor: si ya existe, solo se actualizan las celdas con valor.',
        'Tipo Identificación vacío = RUC.',
    ]
    ejemplos = [
        ['R', '1790012345001', 'Repuestos del Valle S.A.', 'ventas@delvalle.ec', '022345678',
         'Av. Maldonado S12-34', 'María Torres'],
    ]
