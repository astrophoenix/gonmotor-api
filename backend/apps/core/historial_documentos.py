"""Historial de cambios de los documentos del flujo (recepción, inspección,
cotización y orden).

Complementa la auditoría central (`auditoria.py`, una fila por petición sin
valores): aquí se guarda el valor anterior y el nuevo, pero solo de los campos
declarados en `CAMPOS` y solo cuando cambian. Un guardado sin cambios no deja
filas, aunque el frontend reenvíe todas las líneas.

- Los valores se comparan ya formateados (`10` y `10.00` son iguales) y las FKs
  por id, sin consultar el relacionado salvo que cambie.
- Los eventos de una misma operación comparten `operacion`: la cabecera
  `X-Operacion-ID` (UUID que el frontend genera por cada clic en Guardar) o,
  sin ella, uno por petición.
- Se escriben en la transacción de la operación: si el historial falla, la
  operación también. El registro nunca se desactiva; las acciones especiales
  `historial_<recurso>` solo deciden quién lo consulta.
- Las líneas y las fotos registran sus eventos en el documento padre. De las
  fotos solo se registran reemplazos y bajas (son la evidencia del estado del
  vehículo); las altas no, para no llenar el historial al crear el documento.
- Los vínculos editados por `relaciones/` se registran en los dos documentos.
"""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import action

from apps.authentication.permissions import TieneAccion, TienePermiso
from .models import EventoDocumento
from .serializers import ModelSerializerPublico

Evento = EventoDocumento.Evento

TIPOS = {
    'ordenes.RecepcionVehiculo': EventoDocumento.TipoDocumento.RECEPCION,
    'ordenes.InspeccionVehiculo': EventoDocumento.TipoDocumento.INSPECCION,
    'cotizaciones.Cotizacion': EventoDocumento.TipoDocumento.COTIZACION,
    'ordenes.OrdenTrabajo': EventoDocumento.TipoDocumento.ORDEN,
}

# Campos sensibles por modelo (`_meta.label`): {campo: etiqueta visible}.
# Los totales no se listan: se derivan de las líneas, que sí se registran.
CAMPOS = {
    'ordenes.RecepcionVehiculo': {
        'estado': 'Estado', 'firma_cliente': 'Firma del cliente', 'fecha_firma_cliente': 'Fecha de firma',
        'motivo_no_recepcion': 'Motivo de no recepción', 'cliente': 'Cliente', 'vehiculo': 'Vehículo',
        'recibido_por': 'Recibido por', 'tipo_recepcion': 'Tipo de recepción', 'orden_trabajo': 'Orden de trabajo',
        'fecha_ingreso': 'Fecha de ingreso', 'fecha_salida': 'Fecha de salida',
        'kilometraje_ingreso': 'Kilometraje de ingreso', 'nivel_combustible': 'Nivel de combustible',
        'cantidad_llaves': 'Cantidad de llaves', 'detalles_carroceria': 'Daños de carrocería',
        # Objetos de valor declarados al ingresar: base de cualquier reclamo del cliente.
        'pertenencia_celular': 'Pertenencia: celular', 'pertenencia_billetera': 'Pertenencia: billetera',
        'pertenencia_dinero': 'Pertenencia: dinero en efectivo', 'pertenencia_documentos': 'Pertenencia: documentos',
        'pertenencia_gafas': 'Pertenencia: gafas', 'pertenencia_equipaje': 'Pertenencia: equipaje',
        'pertenencia_otros': 'Pertenencia: otros objetos', 'pertenencias_observaciones': 'Observaciones de pertenencias',
    },
    'ordenes.InspeccionVehiculo': {
        'estado': 'Estado', 'responsable': 'Responsable', 'cliente': 'Cliente', 'vehiculo': 'Vehículo',
        'tipo_inspeccion': 'Tipo de inspección', 'orden_trabajo': 'Orden de trabajo',
        'kilometraje_diagnostico': 'Kilometraje del diagnóstico', 'codigos_dtc': 'Códigos de falla (DTC)',
        'diagnostico_tecnico': 'Diagnóstico técnico', 'recomendaciones': 'Recomendaciones',
    },
    'ordenes.OrdenTrabajo': {
        'estado': 'Estado', 'motivo_espera': 'Motivo de espera', 'prioridad': 'Prioridad',
        'tipo_trabajo': 'Tipo de trabajo', 'cliente': 'Cliente', 'vehiculo': 'Vehículo',
        'asesor': 'Asesor', 'mecanico_principal': 'Técnico responsable', 'kilometraje': 'Kilometraje',
        'fecha_ingreso': 'Fecha de ingreso', 'fecha_entrega': 'Fecha de entrega',
        'observaciones': 'Observaciones', 'saldo_al_entregar': 'Entregada a crédito con saldo',
        'tipo_comprobante': 'Tipo de comprobante', 'numero_factura': 'Número de comprobante',
        'fecha_factura': 'Fecha del comprobante',
    },
    'cotizaciones.Cotizacion': {
        'estado': 'Estado', 'cliente': 'Cliente', 'vehiculo': 'Vehículo', 'asesor': 'Asesor',
        'validez_dias': 'Días de validez', 'metodo_aceptacion': 'Método de aceptación',
        'observaciones': 'Observaciones',
    },
    'ordenes.DetalleServicioOrdenTrabajo': {
        'es_adicional': 'Trabajo adicional', 'aprobado': 'Aprobado por el cliente',
        'medio_aprobacion': 'Medio de aprobación', 'fecha_aprobacion': 'Fecha de aprobación',
        'aprobado_por': 'Aprobación registrada por',
        'descripcion': 'Descripción', 'horas_aplicadas': 'Horas', 'precio_unitario': 'Precio unitario',
        'descuento': 'Descuento', 'iva_porcentaje': 'IVA', 'completado': 'Completado',
        'mecanico_asignado': 'Técnico',
    },
    'ordenes.DetalleRepuestoOrdenTrabajo': {
        'es_adicional': 'Trabajo adicional', 'aprobado': 'Aprobado por el cliente',
        'medio_aprobacion': 'Medio de aprobación', 'fecha_aprobacion': 'Fecha de aprobación',
        'aprobado_por': 'Aprobación registrada por',
        'codigo_repuesto': 'Código', 'descripcion': 'Descripción', 'cantidad': 'Cantidad',
        'precio_unitario': 'Precio unitario', 'descuento': 'Descuento', 'iva_porcentaje': 'IVA',
    },
    'ordenes.DetalleServicioInspeccion': {
        'descripcion': 'Descripción', 'horas_estimadas': 'Horas', 'precio_referencial': 'Precio referencial',
        'es_sugerido': 'Sugerido', 'prioridad': 'Prioridad',
    },
    'ordenes.DetalleRepuestoInspeccion': {
        'descripcion': 'Descripción', 'cantidad': 'Cantidad', 'precio_referencial': 'Precio referencial',
        'es_sugerido': 'Sugerido', 'prioridad': 'Prioridad',
    },
    'cotizaciones.DetalleServicioCotizacion': {
        'descripcion': 'Descripción', 'horas_estimadas': 'Horas', 'precio_unitario': 'Precio unitario',
        'descuento': 'Descuento', 'iva_porcentaje': 'IVA', 'es_opcional': 'Opcional',
    },
    'cotizaciones.DetalleRepuestoCotizacion': {
        'codigo_repuesto': 'Código', 'descripcion': 'Descripción', 'cantidad': 'Cantidad',
        'precio_unitario_referencial': 'Precio unitario', 'descuento': 'Descuento',
        'iva_porcentaje': 'IVA', 'es_opcional': 'Opcional',
    },
}

# Líneas: (nombre visible, documento padre, campo cantidad, campo precio).
LINEAS = {
    'ordenes.DetalleServicioInspeccion': ('Servicio', 'inspeccion', 'horas_estimadas', 'precio_referencial'),
    'ordenes.DetalleRepuestoInspeccion': ('Repuesto', 'inspeccion', 'cantidad', 'precio_referencial'),
    'ordenes.DetalleServicioOrdenTrabajo': ('Servicio', 'orden_trabajo', 'horas_aplicadas', 'precio_unitario'),
    'ordenes.DetalleRepuestoOrdenTrabajo': ('Repuesto', 'orden_trabajo', 'cantidad', 'precio_unitario'),
    'cotizaciones.DetalleServicioCotizacion': ('Servicio', 'cotizacion', 'horas_estimadas', 'precio_unitario'),
    'cotizaciones.DetalleRepuestoCotizacion': ('Repuesto', 'cotizacion', 'cantidad', 'precio_unitario_referencial'),
}

# Fotos: documento padre de cada modelo.
FOTOS = {
    'ordenes.FotoRecepcion': 'recepcion',
    'ordenes.FotoInspeccion': 'inspeccion',
    'ordenes.FotoOrdenTrabajo': 'orden_trabajo',
}

LARGO_VALOR = EventoDocumento._meta.get_field('valor_nuevo').max_length


def _recortar(texto, largo=LARGO_VALOR):
    texto = ' '.join(str(texto).split())
    return texto if len(texto) <= largo else texto[:largo - 1] + '…'


def _nombre_de(obj):
    if obj is None:
        return ''
    if isinstance(obj, get_user_model()):
        return obj.get_full_name() or obj.get_username()
    for atributo in ('numero_orden', 'nombre', 'placa', 'nombre_comercial'):
        if getattr(obj, atributo, None):
            return getattr(obj, atributo)
    return str(obj)


def _formatear(campo, valor):
    if valor is None or valor == '':
        return ''
    if campo.choices:
        return str(dict(campo.flatchoices).get(valor, valor))
    if isinstance(campo, models.BooleanField):
        return 'Sí' if valor else 'No'
    if isinstance(campo, models.FileField):
        # La firma se reemplaza con otro nombre de archivo: importa si existe, no cuál es.
        return 'Registrada'
    if isinstance(valor, Decimal):
        if campo.name.startswith('iva'):
            return f'{(valor * 100).normalize():f} %'
        return f'{valor:.2f}'
    if isinstance(campo, models.DateTimeField):
        return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')
    return _recortar(valor)


def instantanea(obj):
    """Valores comparables de los campos sensibles de `obj` (FKs por id)."""
    resultado = {}
    for nombre in CAMPOS[obj._meta.label]:
        campo = obj._meta.get_field(nombre)
        if campo.is_relation:
            resultado[nombre] = getattr(obj, campo.attname)
        else:
            resultado[nombre] = _formatear(campo, getattr(obj, nombre))
    return resultado


def _visible(modelo, nombre, valor):
    campo = modelo._meta.get_field(nombre)
    if not campo.is_relation:
        return valor
    if valor is None:
        return ''
    return _recortar(_nombre_de(campo.related_model._base_manager.filter(pk=valor).first()))


def _cambios(obj, antes):
    """[(campo, anterior visible, nuevo visible)] de lo que cambió respecto de `antes`."""
    despues = instantanea(obj)
    modelo = type(obj)
    return [
        (nombre, _visible(modelo, nombre, antes[nombre]), _visible(modelo, nombre, valor))
        for nombre, valor in despues.items()
        if antes.get(nombre) != valor
    ]


def operacion_de_request(request):
    """UUID de la operación: cabecera `X-Operacion-ID` válida o uno por petición."""
    if request is None:
        return uuid.uuid4()
    cacheada = getattr(request, '_operacion_historial', None)
    if cacheada is None:
        try:
            cacheada = uuid.UUID(str(request.headers.get('X-Operacion-ID', '')))
        except ValueError:
            cacheada = uuid.uuid4()
        request._operacion_historial = cacheada
    return cacheada


def _crear(request, documento, filas):
    if not filas:
        return
    usuario = getattr(request, 'user', None)
    if usuario is not None and not usuario.is_authenticated:
        usuario = None
    # La inspección admite empresa nula (datos antiguos): se toma la de su recepción.
    empresa_id = documento.empresa_id or getattr(getattr(documento, 'recepcion', None), 'empresa_id', None)
    if empresa_id is None:
        return
    comunes = dict(
        empresa_id=empresa_id, taller_id=documento.sucursal_id,
        tipo_documento=TIPOS[documento._meta.label], documento=documento.uuid,
        usuario=usuario, usuario_nombre=_recortar(_nombre_de(usuario) or 'Sistema', 200),
        operacion=operacion_de_request(request), fecha=timezone.now(),
    )
    EventoDocumento.objects.bulk_create([EventoDocumento(**comunes, **fila) for fila in filas])


def registrar_evento(request, documento, evento, etiqueta='', anterior='', nuevo=''):
    _crear(request, documento, [dict(
        evento=evento, etiqueta=_recortar(etiqueta, 200),
        valor_anterior=_recortar(anterior), valor_nuevo=_recortar(nuevo),
    )])


def registrar_cambios(request, documento, antes):
    """Un evento por cada campo sensible del documento que cambió desde `antes`."""
    etiquetas = CAMPOS[documento._meta.label]
    _crear(request, documento, [
        dict(
            evento=Evento.ESTADO if nombre == 'estado' else Evento.CAMPO,
            etiqueta=etiquetas[nombre], valor_anterior=anterior, valor_nuevo=nuevo,
        )
        for nombre, anterior, nuevo in _cambios(documento, antes)
    ])


def _resumen_linea(linea):
    _, _, cantidad, precio = LINEAS[linea._meta.label]
    texto = f'{getattr(linea, cantidad)} × {getattr(linea, precio) or 0:.2f}'
    if getattr(linea, 'es_adicional', False):
        texto += ' · Adicional ' + ('aprobado' if linea.aprobado else 'pendiente de aprobación')
    if getattr(linea, 'descuento', None):
        texto += f' (desc. {linea.descuento:.2f})'
    return texto


def documento_de_linea(linea):
    return getattr(linea, LINEAS[linea._meta.label][1])


def registrar_linea(request, evento, linea, antes=None):
    """Alta, baja o edición (con `antes`) de una línea, registrada en su documento."""
    nombre_linea = LINEAS[linea._meta.label][0]
    documento = documento_de_linea(linea)
    titulo = f'{nombre_linea}: {linea.descripcion}'
    if evento == Evento.LINEA_AGREGADA:
        filas = [dict(valor_nuevo=_resumen_linea(linea))]
    elif evento == Evento.LINEA_ELIMINADA:
        filas = [dict(valor_anterior=_resumen_linea(linea))]
    else:
        etiquetas = CAMPOS[linea._meta.label]
        filas = [
            dict(etiqueta=_recortar(f'{titulo} · {etiquetas[nombre]}', 200), valor_anterior=anterior, valor_nuevo=nuevo)
            for nombre, anterior, nuevo in _cambios(linea, antes)
        ]
    _crear(request, documento, [
        {'evento': evento, 'etiqueta': _recortar(titulo, 200), 'valor_anterior': '', 'valor_nuevo': '', **fila}
        for fila in filas
    ])


def registrar_vinculo(request, documento, nombre_relacionado, referencia, vinculado):
    """Alta o baja de un vínculo del flujo (`relaciones/`) vista desde `documento`."""
    registrar_evento(
        request, documento, Evento.VINCULO, f'Vínculo: {nombre_relacionado}',
        anterior='' if vinculado else referencia, nuevo=referencia if vinculado else '',
    )


def registrar_foto(request, evento, foto):
    """Reemplazo o baja de una foto de evidencia, registrada en su documento."""
    if hasattr(foto, 'get_tipo_vista_display'):
        nombre = foto.get_tipo_vista_display()
    else:
        nombre = foto.descripcion or 'sin descripción'
    subida = f'Subida el {timezone.localtime(foto.created_at).strftime("%d/%m/%Y %H:%M")}' if foto.created_at else 'Foto anterior'
    registrar_evento(
        request, getattr(foto, FOTOS[foto._meta.label]), evento, f'Foto: {nombre}',
        anterior=subida, nuevo='Nueva imagen' if evento == Evento.FOTO_REEMPLAZADA else '',
    )


class EventoDocumentoSerializer(ModelSerializerPublico):
    class Meta:
        model = EventoDocumento
        fields = ['id', 'fecha', 'usuario_nombre', 'operacion', 'evento', 'etiqueta', 'valor_anterior', 'valor_nuevo']
        read_only_fields = fields


class HistorialDocumentoMixin:
    """Añade `GET <documento>/{id}/historial/` a un viewset del flujo.

    Exige `ver` sobre `recurso_historial` y la acción especial
    `historial_<recurso_historial>`. El documento se resuelve con
    `get_object()`, así que respeta empresa y taller del viewset.
    """

    recurso_historial = None

    def get_permissions(self):
        if self.action == 'historial':
            return [
                permissions.IsAuthenticated(),
                TienePermiso(self.recurso_historial, 'ver')(),
                TieneAccion(f'historial_{self.recurso_historial}')(),
            ]
        return super().get_permissions()

    @action(detail=True, methods=['get'], url_path='historial')
    def historial(self, request, pk=None):
        documento = self.get_object()
        queryset = EventoDocumento.objects.filter(
            empresa_id=documento.empresa_id or getattr(getattr(documento, 'recepcion', None), 'empresa_id', None),
            tipo_documento=TIPOS[documento._meta.label],
            documento=documento.uuid,
        )
        pagina = self.paginate_queryset(queryset)
        return self.get_paginated_response(EventoDocumentoSerializer(pagina, many=True).data)
