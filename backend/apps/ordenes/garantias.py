"""Condiciones guardadas por trabajo y cobertura informativa al recibir el vehículo."""
from datetime import date

from django.utils import timezone
from rest_framework import serializers

from apps.core.ids_publicos import id_publico


def condiciones(linea, *, fecha=None, kilometraje=None):
    orden = linea.orden_trabajo
    dias, km = linea.garantia_dias or 0, linea.garantia_km or 0
    if (not (dias or km) or not linea.aprobado or not linea.completado
            or orden.tipo_trabajo == 'GARANTIA' or orden.estado != 'ENTREGADO' or not orden.fecha_entrega):
        return None
    inicio = timezone.localdate(orden.fecha_entrega)
    hoy = fecha or timezone.localdate()
    vence = date.fromordinal(min(date.max.toordinal(), inicio.toordinal() + dias)) if dias else None
    limite_km = orden.kilometraje + km if km and orden.kilometraje is not None else None
    if hoy < inicio or (vence and hoy > vence) or (limite_km is not None and kilometraje is not None and kilometraje > limite_km):
        return None
    pendiente = bool(km and (kilometraje is None or limite_km is None or kilometraje < orden.kilometraje))
    return {
        'servicio_id': id_publico(linea), 'descripcion': linea.descripcion,
        'orden_id': id_publico(orden), 'numero_orden': orden.numero_orden,
        'taller': orden.sucursal.nombre if orden.sucursal_id else '',
        'garantia_dias': dias, 'garantia_km': km,
        'fecha_inicio': inicio.isoformat(), 'fecha_hasta': vence.isoformat() if vence else None,
        'kilometraje_inicio': orden.kilometraje, 'kilometraje_hasta': limite_km,
        'estado': 'POR_VERIFICAR' if pendiente else 'VIGENTE',
    }


def validar_orden(instance, attrs, empresa_id):
    tipo = attrs.get('tipo_trabajo', getattr(instance, 'tipo_trabajo', 'MANTENIMIENTO'))
    origen = attrs.get('orden_garantia_origen', getattr(instance, 'orden_garantia_origen', None))
    if tipo != 'GARANTIA':
        if origen:
            raise serializers.ValidationError({'orden_garantia_origen': 'El vínculo corresponde a una orden de garantía.'})
        return
    if not origen:
        raise serializers.ValidationError({'orden_garantia_origen': 'Selecciona la orden original entregada.'})
    vehiculo = attrs.get('vehiculo', getattr(instance, 'vehiculo', None))
    if (origen.empresa_id != empresa_id or not origen.is_active or origen.deleted_at is not None
            or origen.estado != 'ENTREGADO' or origen.tipo_trabajo == 'GARANTIA'
            or origen.vehiculo_id != getattr(vehiculo, 'pk', None) or (instance and origen.pk == instance.pk)):
        raise serializers.ValidationError({'orden_garantia_origen': 'Selecciona una OT original entregada del mismo vehículo, que no sea otra garantía.'})
    if instance and instance.tipo_trabajo != 'GARANTIA' and (instance.servicios.exists() or instance.repuestos.exists()):
        raise serializers.ValidationError({'tipo_trabajo': 'Crea una OT nueva para la garantía; esta orden ya tiene líneas.'})
    if instance and instance.orden_garantia_origen_id and origen.pk != instance.orden_garantia_origen_id:
        raise serializers.ValidationError({'orden_garantia_origen': 'La orden original de una garantía ya registrada no se cambia.'})


def preparar_linea_garantia(linea, attrs):
    orden = attrs.get('orden_trabajo') or getattr(linea, 'orden_trabajo', None)
    if orden and orden.tipo_trabajo == 'GARANTIA':
        for campo in ('costo_unitario', 'horas_aplicadas', 'cantidad'):
            if attrs.get(campo, getattr(linea, campo, 0)) < 0:
                raise serializers.ValidationError({campo: 'El costo y las unidades no pueden ser negativos.'})
        attrs.update(precio_unitario=0, descuento=0)
        if 'garantia_dias' in attrs or hasattr(linea, 'garantia_dias'):
            attrs.update(garantia_dias=0, garantia_km=0)
