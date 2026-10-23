"""Despacho de repuestos del almacén a una orden de trabajo.

- Despachar resta stock (`SALIDA_ORDEN`) y devolver lo suma
  (`DEVOLUCION_ORDEN`); una línea registrada sin despachar no mueve stock.
- Solo las líneas vinculadas al catálogo (`repuesto`) se despachan; las de
  texto libre (repuesto comprado fuera, del cliente…) no tocan el inventario.
- No se despacha más de la cantidad de la línea, ni más de lo que hay en
  stock salvo `permitir_negativo` (acción especial `stock_negativo`).
- Una línea con repuestos despachados no se borra ni baja su cantidad por
  debajo de lo despachado, y una orden así no se anula: primero se devuelve
  al almacén (lo más seguro: el stock nunca cambia sin un movimiento explícito).

Quien llama debe tener la orden y la línea bloqueadas en la transacción.
"""
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.inventario import services as inventario
from apps.inventario.models import MovimientoInventario

CERO = Decimal('0.00')


def _cantidad(valor, por_defecto):
    cantidad = por_defecto if valor in (None, '') else Decimal(str(valor))
    if cantidad <= CERO:
        raise serializers.ValidationError({'cantidad': 'La cantidad debe ser mayor a cero.'})
    return cantidad


def _referencia(linea):
    return f'OT {linea.orden_trabajo.numero_orden} · {linea.descripcion}'[:255]


def despachar(linea, *, usuario, cantidad=None, permitir_negativo=False):
    if linea.provisto_cliente:
        raise serializers.ValidationError('El repuesto es provisto por el cliente; no se despacha del almacén.')
    if not linea.aprobado:
        raise serializers.ValidationError('El repuesto adicional está pendiente de aprobación del cliente.')
    orden = linea.orden_trabajo
    if orden.estado == 'CANCELADO':
        raise serializers.ValidationError('La orden está anulada; no se despachan repuestos.')
    linea.resolver_repuesto()
    if not linea.repuesto_id:
        raise serializers.ValidationError(
            'Esta línea no está vinculada a un repuesto del catálogo; no mueve inventario.'
        )
    pendiente = linea.cantidad_pendiente_despacho
    if pendiente <= CERO:
        raise serializers.ValidationError('La línea ya está despachada por completo.')
    cantidad = _cantidad(cantidad, pendiente)
    if cantidad > pendiente:
        raise serializers.ValidationError(
            {'cantidad': f'Solo quedan {pendiente} por despachar en esta línea.'}
        )
    repuesto = inventario.bloquear_repuesto(linea.repuesto_id)
    movimiento = inventario.mover_stock(
        repuesto,
        tipo=MovimientoInventario.Tipo.SALIDA_ORDEN,
        cantidad=-cantidad,
        usuario=usuario,
        permitir_negativo=permitir_negativo,
        orden_trabajo=orden,
        linea_orden=linea,
        referencia=_referencia(linea),
    )
    linea.cantidad_despachada += cantidad
    linea.fecha_despacho = timezone.now()
    linea.despachado_por = usuario
    linea.save(update_fields=['repuesto', 'cantidad_despachada', 'fecha_despacho', 'despachado_por'])
    return movimiento


def devolver(linea, *, usuario, cantidad=None, motivo=''):
    if linea.cantidad_despachada <= CERO:
        raise serializers.ValidationError('La línea no tiene repuestos despachados para devolver.')
    cantidad = _cantidad(cantidad, linea.cantidad_despachada)
    if cantidad > linea.cantidad_despachada:
        raise serializers.ValidationError(
            {'cantidad': f'Solo se despacharon {linea.cantidad_despachada} en esta línea.'}
        )
    repuesto = inventario.bloquear_repuesto(linea.repuesto_id)
    movimiento = inventario.mover_stock(
        repuesto,
        tipo=MovimientoInventario.Tipo.DEVOLUCION_ORDEN,
        cantidad=cantidad,
        usuario=usuario,
        orden_trabajo=linea.orden_trabajo,
        linea_orden=linea,
        referencia=_referencia(linea),
        motivo=motivo,
    )
    linea.cantidad_despachada -= cantidad
    linea.save(update_fields=['cantidad_despachada'])
    return movimiento


def validar_sin_despachos(orden, accion):
    """Anular o eliminar la orden exige devolver antes lo despachado."""
    if orden.repuestos.filter(cantidad_despachada__gt=0).exists():
        raise serializers.ValidationError(
            {'estado': f'La orden tiene repuestos despachados. Devuélvelos al almacén antes de {accion}la.'}
        )
