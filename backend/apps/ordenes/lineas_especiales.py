"""RF-12: origen de repuestos y costos de trabajos externos, sin movimientos de dinero."""
from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.authentication.permissions import TienePermiso


def preparar_especial(linea, attrs, request, *, es_repuesto):
    if es_repuesto:
        cliente = attrs.get('provisto_cliente', getattr(linea, 'provisto_cliente', False))
        if cliente:
            if linea and linea.cantidad_despachada:
                raise serializers.ValidationError({'provisto_cliente': 'Devuelve lo despachado al almacén antes de marcarlo como provisto por el cliente.'})
            attrs.update(precio_unitario=Decimal('0.00'), costo_unitario=Decimal('0.00'), descuento=Decimal('0.00'))
        elif linea is None and 'precio_unitario' not in attrs:
            raise serializers.ValidationError({'precio_unitario': 'Este campo es requerido.'})
        return

    externo = attrs.get('es_subcontratado', getattr(linea, 'es_subcontratado', False))
    proveedor = attrs.get('proveedor', getattr(linea, 'proveedor', None))
    if not externo:
        if proveedor is not None:
            if linea and linea.es_subcontratado and attrs.get('es_subcontratado') is False:
                attrs['proveedor'] = None
            else:
                raise serializers.ValidationError({'proveedor': 'El proveedor corresponde a un trabajo subcontratado.'})
        return
    if proveedor is None:
        raise serializers.ValidationError({'proveedor': 'Selecciona el proveedor del trabajo subcontratado.'})
    cambia_proveedor = linea is None or not linea.es_subcontratado or proveedor.pk != linea.proveedor_id
    if cambia_proveedor:
        if request is None or not TienePermiso('proveedores', 'ver')().has_permission(request, None):
            raise PermissionDenied('Se requiere acceso a Proveedores para seleccionar el proveedor del subcontrato.')
        if not proveedor.is_active or proveedor.deleted_at is not None:
            raise serializers.ValidationError({'proveedor': 'Selecciona un proveedor activo.'})
    cantidad = attrs.get('cantidad', getattr(linea, 'cantidad', Decimal('1')))
    if cantidad <= 0:
        raise serializers.ValidationError({'cantidad': 'La cantidad debe ser mayor a cero.'})
    if (linea is None or not linea.es_subcontratado) and 'costo_unitario' not in attrs:
        raise serializers.ValidationError({'costo_unitario': 'Registra el costo unitario sin IVA del subcontrato.'})
    for campo in ('costo_unitario', 'precio_unitario'):
        if attrs.get(campo, getattr(linea, campo, 0)) < 0:
            raise serializers.ValidationError({campo: 'El importe no puede ser negativo.'})
