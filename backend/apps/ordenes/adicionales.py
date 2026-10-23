"""Reglas de RF-13, ejecutadas con la orden bloqueada antes de guardar."""
import hashlib
import json
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

ABIERTAS = {'PENDIENTE', 'EN_PROCESO', 'EN_ESPERA'}
COMERCIALES = {
    'descripcion', 'precio_unitario', 'horas_aplicadas', 'cantidad', 'descuento',
    'iva_porcentaje', 'servicio', 'servicio_codigo', 'repuesto', 'codigo_repuesto',
    'provisto_cliente', 'es_subcontratado', 'proveedor',
}


def _valor_comercial(nombre, valor):
    # El editor representa códigos opcionales nulos como una cadena vacía.
    if nombre in {'codigo_repuesto', 'servicio_codigo'}:
        return valor or ''
    return valor


def version_linea(linea):
    campos = {campo.name: campo for campo in linea._meta.fields}
    valores = []
    for nombre in sorted(COMERCIALES & campos.keys()):
        campo = campos[nombre]
        valor = _valor_comercial(nombre, getattr(linea, campo.attname))
        if isinstance(valor, Decimal):
            valor = format(valor.normalize(), 'f')
        valores.append((nombre, valor))
    return hashlib.sha256(json.dumps(valores, ensure_ascii=False).encode()).hexdigest()


def validar_abierta(orden):
    if orden.estado not in ABIERTAS:
        raise serializers.ValidationError('Los adicionales solo se modifican o aprueban en una orden abierta.')


def preparar_linea(linea, attrs):
    adicional = linea.es_adicional if linea else attrs.get('es_adicional', False)
    if linea and 'es_adicional' in attrs and attrs['es_adicional'] != adicional:
        raise serializers.ValidationError({'es_adicional': 'La condición de adicional se fija al crear la línea.'})
    if not adicional:
        return
    orden = attrs.get('orden_trabajo') or (linea.orden_trabajo if linea else None)
    if orden:
        validar_abierta(orden)
    if linea and 'orden_trabajo' in attrs and orden.pk != linea.orden_trabajo_id:
        raise serializers.ValidationError({'orden_trabajo': 'Un adicional no puede trasladarse a otra orden.'})
    cambia = linea is not None and any(
        campo in attrs and _valor_comercial(campo, attrs[campo]) != _valor_comercial(campo, getattr(linea, campo))
        for campo in COMERCIALES if hasattr(linea, campo)
    )
    pendiente = linea is None or not linea.aprobado or cambia
    if pendiente and attrs.get('completado', getattr(linea, 'completado', False)):
        raise serializers.ValidationError({'completado': 'El adicional necesita aprobación antes de completarlo. Desmarca «Hecho» para modificarlo.'})
    if cambia and getattr(linea, 'cantidad_despachada', 0):
        raise serializers.ValidationError('Devuelve los repuestos al almacén antes de modificar un adicional aprobado.')
    if pendiente:
        attrs.update(aprobado=False, medio_aprobacion='', fecha_aprobacion=None, aprobado_por=None)


def aprobar_linea(linea, datos, usuario):
    validar_abierta(linea.orden_trabajo)
    if not linea.es_adicional:
        raise serializers.ValidationError('La línea no es un trabajo adicional.')
    # Reintentos no reescriben quién ni cuándo aprobó el cliente.
    if linea.aprobado:
        return False
    if datos.get('version_aprobacion') != version_linea(linea):
        raise serializers.ValidationError('La propuesta cambió. Recarga la orden y revisa sus valores antes de aprobarla.')
    valores = {}
    campos = {'medio_aprobacion': serializers.ChoiceField(choices=linea.Medio.choices),
              'fecha_aprobacion': serializers.DateTimeField()}
    for nombre, campo in campos.items():
        valor = datos.get(nombre, timezone.now() if nombre == 'fecha_aprobacion' else None)
        try:
            valores[nombre] = campo.run_validation(valor)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({nombre: exc.detail}) from exc
    medio, fecha = valores['medio_aprobacion'], valores['fecha_aprobacion']
    if fecha > timezone.now():
        raise serializers.ValidationError({'fecha_aprobacion': 'La aprobación no puede tener una fecha futura.'})
    linea.aprobado = True
    linea.medio_aprobacion = medio
    linea.fecha_aprobacion = fecha
    linea.aprobado_por = usuario
    linea.save()
    linea.orden_trabajo.calcular_totales()
    return True
