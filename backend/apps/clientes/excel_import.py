"""Importación de clientes y sus vehículos desde Excel.

Se apoya en el núcleo `apps.core.importacion`: aquí solo están las columnas,
las validaciones de cliente y vehículo, y cómo se guardan. Reglas (las mismas
de la pantalla):

- Una fila por vehículo; las filas se agrupan por la identificación del cliente.
- Si el cliente o el vehículo existen, solo cambian las celdas con valor.
- Un cliente o vehículo desactivado no se reactiva; el kilometraje no retrocede.
- El dueño actual es por empresa (`asignar_propietario_actual`).
- Las filas con vehículo exigen además `vehiculos/modificar`.
"""

from collections import OrderedDict

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction

from apps.authentication.permisos_repo import permisos_en_request
from apps.authentication.utils import get_contexto_desde_request
from apps.clientes.models import Cliente
from apps.core.importacion import (
    Columna,
    FilaInvalida,
    Importador,
    a_entero,
    a_opcion,
    limpiar,
    validar_longitudes,
)
from apps.vehiculos.models import Vehiculo, asignar_propietario_actual, vincular_empresa

COLUMNAS = [
    Columna('tipo_identificacion', 'Tipo Identificación', opciones=tuple(Cliente.TIPO_IDENTIFICACION)),
    Columna('identificacion', 'Identificación', alias=('identificacion (cedula/ruc)', 'cedula', 'documento', 'ruc')),
    Columna('nombre', 'Nombre', alias=('nombre o razon social', 'razon social')),
    Columna('email', 'Email'),
    Columna('telefono', 'Teléfono'),
    Columna('direccion', 'Dirección'),
    Columna('contifico_id', 'ID Contífico'),
    Columna('placa', 'Placa'),
    Columna('vin', 'Chasis/VIN', alias=('chasis', 'chasis / vin')),
    Columna('numero_motor', 'Nº Motor', alias=('numero de motor', 'n de motor', 'motor')),
    Columna('marca', 'Marca'),
    Columna('modelo', 'Modelo'),
    Columna('anio', 'Año'),
    Columna('color', 'Color'),
    Columna('transmision', 'Transmisión', opciones=tuple(Vehiculo.TipoTransmision.choices)),
    Columna('combustible', 'Combustible', alias=('tipo de combustible',), opciones=tuple(Vehiculo.TipoCombustible.choices)),
    Columna('tipo', 'Tipo', alias=('tipo de vehiculo',), opciones=tuple(Vehiculo.TipoVehiculo.choices)),
    Columna('pais_origen', 'País de Origen', alias=('pais',)),
    Columna('kilometraje_actual', 'Kilometraje'),
]

# (clave, encabezado) en el orden de la plantilla.
CLIENT_COLUMNS = [(c.clave, c.encabezado) for c in COLUMNAS]

# Campos que, si la celda viene vacía, NO tocan el valor guardado: el Excel
# solo agrega o corrige datos, nunca los borra.
CLIENTE_OPCIONALES = ['tipo_identificacion', 'email', 'telefono', 'direccion', 'contifico_id']
VEHICULO_OPCIONALES = [
    'vin', 'numero_motor', 'marca', 'modelo', 'anio', 'color', 'transmision',
    'combustible', 'tipo', 'pais_origen', 'kilometraje_actual',
]

# Valores al CREAR un vehículo cuando la celda viene vacía (los mismos del modelo).
VEHICULO_POR_DEFECTO = {
    'tipo': Vehiculo.TipoVehiculo.AUTOMOVIL,
    'transmision': Vehiculo.TipoTransmision.MANUAL,
    'combustible': Vehiculo.TipoCombustible.GASOLINA,
    'pais_origen': 'EC',
    'kilometraje_actual': 0,
}

OPCIONES_VEHICULO = (
    ('tipo', Vehiculo.TipoVehiculo.choices, 'Tipo de Vehículo'),
    ('transmision', Vehiculo.TipoTransmision.choices, 'Transmisión'),
    ('combustible', Vehiculo.TipoCombustible.choices, 'Combustible'),
)


def _resumen_vehiculo(registro):
    placa = limpiar(registro.get('placa')).upper()
    if not placa:
        return ''
    detalle = ' '.join(x for x in [limpiar(registro.get('marca')), limpiar(registro.get('modelo'))] if x)
    return f'{placa} · {detalle}' if detalle else placa


class ImportadorClientes(Importador):
    columnas = COLUMNAS
    hoja = 'Importación Clientes'
    entidad = 'clientes'
    instrucciones = [
        'Obligatorios: Identificación y Nombre. El resto es opcional.',
        'Una fila por vehículo: si un cliente tiene varios, repite su identificación en cada fila.',
        'Si el cliente o el vehículo ya existen, solo se actualizan las celdas que tengan valor.',
        'El kilometraje de un vehículo existente no puede ser menor al registrado.',
        'País de Origen: código ISO de 2 letras (EC, CO, US…). Vacío = EC.',
    ]
    ejemplos = [
        ['C', '1712345678', 'Juan Pérez', 'juan@correo.com', '0991234567', 'Av. Siempre Viva 123',
         '', 'PBA1234', 'VIN123456789', 'MOTOR123', 'Toyota', 'Corolla', '2020', 'Rojo',
         'M', 'GAS', 'AUTO', 'EC', '45000'],
        ['C', '1712345678', 'Juan Pérez', '', '', '',
         '', 'PBA5678', 'VIN987654321', '', 'Toyota', 'Hilux', '2019', 'Plata',
         'M', 'DIE', 'CAMN', 'EC', '82000'],
    ]

    def __init__(self, empresa_id, request=None, puede_vehiculos=None):
        super().__init__(empresa_id, request)
        if puede_vehiculos is None:
            # Las filas con vehículo exigen además `vehiculos/modificar`: la
            # importación no es una puerta lateral para editar vehículos.
            puede_vehiculos = True
            if request is not None:
                contexto = get_contexto_desde_request(request)
                permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
                puede_vehiculos = 'modificar' in permisos.get('vehiculos', ())
        self.puede_vehiculos = puede_vehiculos

    def contadores_extra(self):
        return {'vehiculos': 0}

    def resumen_fila(self, numero, registro):
        return {
            'identificacion': limpiar(registro.get('identificacion')),
            'nombre': limpiar(registro.get('nombre')),
            'vehiculo': _resumen_vehiculo(registro),
        }

    # --- Validación ---------------------------------------------------------
    def validar_cliente(self, registro):
        """Datos normalizados del cliente: solo incluye los opcionales que traen valor."""
        nombre = limpiar(registro.get('nombre'))
        # Igual que la pantalla: la identificación se guarda en mayúsculas.
        identificacion = limpiar(registro.get('identificacion')).upper()

        errores = []
        if not nombre:
            errores.append('Falta el nombre o razón social.')
        if not identificacion:
            errores.append('Falta la identificación (cédula/RUC).')

        datos = {'identificacion': identificacion, 'nombre': nombre}
        datos.update({c: limpiar(registro.get(c)) for c in CLIENTE_OPCIONALES if limpiar(registro.get(c))})

        if 'tipo_identificacion' in datos:
            try:
                datos['tipo_identificacion'] = a_opcion(
                    datos['tipo_identificacion'], Cliente.TIPO_IDENTIFICACION, 'Tipo Identificación',
                )
            except ValueError as error:
                errores.append(str(error))
        if 'email' in datos:
            try:
                validate_email(datos['email'])
            except DjangoValidationError:
                errores.append(f'El correo electrónico "{datos["email"]}" no es válido.')

        errores += validar_longitudes(Cliente, datos, self.nombres)
        if errores:
            raise FilaInvalida(' '.join(errores))
        return datos

    def validar_vehiculo(self, registro):
        """Datos del vehículo (solo opcionales con valor), o None si la fila no trae vehículo."""
        con_valor = [c for c in ['placa', *VEHICULO_OPCIONALES] if limpiar(registro.get(c))]
        # Transmisión y combustible solos no bastan para considerar que hay vehículo.
        if not [c for c in con_valor if c not in ('transmision', 'combustible')]:
            return None

        placa = limpiar(registro.get('placa')).upper()
        errores = [] if placa else ['Falta la placa del vehículo.']
        datos = {'placa': placa}
        datos.update({c: limpiar(registro.get(c)) for c in VEHICULO_OPCIONALES if limpiar(registro.get(c))})

        for campo in ('anio', 'kilometraje_actual'):
            if campo in datos:
                try:
                    datos[campo] = a_entero(datos[campo])
                except ValueError as error:
                    errores.append(str(error))
        for campo, choices, etiqueta in OPCIONES_VEHICULO:
            if campo in datos:
                try:
                    datos[campo] = a_opcion(datos[campo], choices, etiqueta)
                except ValueError as error:
                    errores.append(str(error))
        if 'pais_origen' in datos:
            from django_countries.data import COUNTRIES
            datos['pais_origen'] = datos['pais_origen'].upper()
            if datos['pais_origen'] not in COUNTRIES:
                errores.append(f'"{datos["pais_origen"]}" no es un código de país válido (ISO 3166).')

        errores += validar_longitudes(Vehiculo, datos, self.nombres)
        if errores:
            raise FilaInvalida(' '.join(errores))
        return datos

    # --- Guardado ---------------------------------------------------------------
    def guardar_cliente(self, datos):
        """Crea o actualiza el cliente. Devuelve (cliente, creado)."""
        existente = Cliente.objects.filter(
            empresa_id=self.empresa_id, identificacion=datos['identificacion'],
        ).first()
        if existente is None:
            return Cliente.objects.create(
                empresa_id=self.empresa_id,
                tipo_identificacion=datos.get('tipo_identificacion', 'C'),
                **{k: v for k, v in datos.items() if k != 'tipo_identificacion'},
            ), True
        if not existente.is_active:
            raise FilaInvalida(
                f'Ya existe un cliente desactivado con la identificación {datos["identificacion"]}. '
                'Actívalo desde el listado de clientes para poder actualizarlo.'
            )
        for campo, valor in datos.items():
            setattr(existente, campo, valor)
        existente.save(update_fields=[*datos.keys(), 'updated_at'])
        return existente, False

    def guardar_vehiculo(self, datos, cliente):
        """Crea o actualiza el vehículo, lo vincula a la empresa y lo asigna al cliente."""
        existente = Vehiculo.objects.filter(placa=datos['placa']).first()
        if existente is None:
            vehiculo = Vehiculo.objects.create(**{**VEHICULO_POR_DEFECTO, **datos})
        else:
            vinculo = existente.vinculo_de(self.empresa_id)
            if vinculo is not None and not vinculo.is_active:
                raise FilaInvalida(
                    f'El vehículo {datos["placa"]} está desactivado en tu empresa. '
                    'Actívalo desde el listado de vehículos para poder actualizarlo.'
                )
            km = datos.get('kilometraje_actual')
            if km is not None and km < (existente.kilometraje_actual or 0):
                raise FilaInvalida(
                    f'El kilometraje ({km} km) es menor al registrado para {datos["placa"]} '
                    f'({existente.kilometraje_actual} km): el odómetro no retrocede.'
                )
            for campo, valor in datos.items():
                setattr(existente, campo, valor)
            existente.save(update_fields=[*datos.keys(), 'updated_at'])
            vehiculo = existente
        vincular_empresa(vehiculo, self.empresa_id, activar=False)
        asignar_propietario_actual(vehiculo, cliente)
        return vehiculo

    # --- Proceso ------------------------------------------------------------
    def procesar(self, filas):
        grupos = OrderedDict()
        for numero, registro in filas:
            grupos.setdefault(limpiar(registro.get('identificacion')).upper(), []).append((numero, registro))

        for identificacion, filas_grupo in grupos.items():
            try:
                datos_cliente = self.validar_cliente(filas_grupo[0][1])
                with transaction.atomic():
                    cliente, creado = self.guardar_cliente(datos_cliente)
            except Exception as error:
                # Todas las filas del grupo quedan fuera: se reportan todas para
                # que el Excel de errores no pierda ninguna.
                motivo = self.motivo_de(error)
                for numero, registro in filas_grupo:
                    self.registrar_error(numero, identificacion, registro, motivo)
                continue

            estado = 'nuevo' if creado else 'actualiza'
            for numero, registro in filas_grupo:
                try:
                    datos_vehiculo = self.validar_vehiculo(registro)
                    if datos_vehiculo:
                        if not self.puede_vehiculos:
                            raise FilaInvalida(
                                'Tu rol no permite registrar ni modificar vehículos: '
                                'el cliente se importó sin este vehículo.'
                            )
                        with transaction.atomic():
                            self.guardar_vehiculo(datos_vehiculo, cliente)
                        self.resultado['vehiculos'] += 1
                    self.marcar(numero, estado)
                except Exception as error:
                    self.registrar_error(numero, identificacion, registro, self.motivo_de(error))

            # El cliente quedó guardado aunque alguna de sus filas de vehículo
            # falle (esas filas van a `errores`): cuenta como importado.
            self.resultado['exitosos'] += 1
            self.resultado['nuevos' if creado else 'actualizados'] += 1
