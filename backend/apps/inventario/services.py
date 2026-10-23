"""Movimientos de stock de los repuestos (kardex).

Todo cambio de `Repuesto.stock_actual` pasa por `mover_stock`, con el
repuesto bloqueado (`select_for_update`) dentro de la transacción de quien
llama, para que dos despachos simultáneos no lean el mismo stock.
"""
from decimal import ROUND_HALF_UP, Decimal

from rest_framework import serializers

from .models import MovimientoInventario, Repuesto

CERO = Decimal('0.00')
CENTAVO = Decimal('0.01')

Tipo = MovimientoInventario.Tipo


def bloquear_repuesto(repuesto_id):
    return Repuesto.all_objects.select_for_update().get(pk=repuesto_id)


def error_stock_insuficiente(repuesto, cantidad):
    """400 que la web reconoce (`stock_insuficiente`) para ofrecer la autorización."""
    return serializers.ValidationError({
        'cantidad': (
            f'Stock insuficiente de {repuesto.codigo}: hay {repuesto.stock_actual} y se piden {cantidad}.'
        ),
        'stock_insuficiente': True,
        'stock_actual': str(repuesto.stock_actual),
    })


def mover_stock(repuesto, *, tipo, cantidad, usuario=None, costo_unitario=None, permitir_negativo=False, **origen):
    """Suma `cantidad` (con signo) al stock y deja el movimiento en el kardex.

    `repuesto` debe venir bloqueado. Una salida que deje el stock en negativo
    se rechaza salvo `permitir_negativo`.
    """
    cantidad = Decimal(cantidad)
    nuevo = repuesto.stock_actual + cantidad
    if cantidad < 0 and nuevo < 0 and not permitir_negativo:
        raise error_stock_insuficiente(repuesto, -cantidad)
    repuesto.stock_actual = nuevo
    repuesto.save(update_fields=['stock_actual', 'updated_at'])
    return MovimientoInventario.objects.create(
        empresa_id=repuesto.empresa_id,
        repuesto=repuesto,
        tipo=tipo,
        cantidad=cantidad,
        stock_resultante=nuevo,
        costo_unitario=repuesto.costo_referencial if costo_unitario is None else costo_unitario,
        usuario=usuario,
        **origen,
    )


def costo_promedio(repuesto, cantidad, costo_unitario):
    """Costo promedio ponderado tras una entrada de `cantidad` a `costo_unitario`.

    Un stock previo negativo o nulo no pondera: manda el costo de la compra.
    """
    previo = max(repuesto.stock_actual, CERO)
    total = previo + cantidad
    if previo <= 0 or total <= 0:
        return Decimal(costo_unitario).quantize(CENTAVO, ROUND_HALF_UP)
    valor = previo * repuesto.costo_referencial + cantidad * Decimal(costo_unitario)
    return (valor / total).quantize(CENTAVO, ROUND_HALF_UP)
