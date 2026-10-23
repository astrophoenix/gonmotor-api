"""Reglas de las compras a proveedores.

- Registrar una compra suma stock a cada repuesto (`ENTRADA_COMPRA`) y
  recalcula su `costo_referencial` con el **costo promedio ponderado**.
- A contado se registra en el acto el pago por el total; a crédito queda el
  saldo en cuentas por pagar.
- Lo pagado nunca supera el total de la compra.
- Anular una compra anula también sus pagos vigentes y revierte el stock
  (`ANULACION_COMPRA`); se rechaza si algún repuesto ya no tiene stock
  suficiente para revertir. El costo promedio no se recalcula al anular.
"""
from decimal import Decimal

from django.db.models import Case, DecimalField, F, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers

from apps.inventario import services as inventario
from apps.inventario.models import MovimientoInventario

from .models import Compra, DetalleCompra, PagoCompra

CERO = Decimal('0.00')


def _dinero(valor):
    return f'$ {valor:,.2f}'


def pagado(compra):
    return PagoCompra.objects.filter(compra=compra, anulado=False).aggregate(
        total=Sum('monto'),
    )['total'] or CERO


def saldo(compra):
    if compra.estado == Compra.Estado.ANULADA:
        return CERO
    return max(compra.total - pagado(compra), CERO)


def anotar_saldo(queryset):
    """Anota `pagado_db` y `saldo_db` (0 en las anuladas) para listar y filtrar."""
    pagos = (
        PagoCompra.objects.filter(compra=OuterRef('pk'), anulado=False)
        .values('compra').annotate(suma=Sum('monto')).values('suma')
    )
    return queryset.annotate(
        pagado_db=Coalesce(Subquery(pagos, output_field=DecimalField()), Value(CERO)),
    ).annotate(
        saldo_db=Case(
            When(estado=Compra.Estado.ANULADA, then=Value(CERO)),
            default=F('total') - F('pagado_db'),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
    )


def registrar_compra(*, empresa_id, taller, proveedor, lineas, usuario, forma_pago_contado='EFECTIVO', **datos):
    """Crea la compra con sus líneas y mueve el stock. Llamar dentro de una transacción."""
    compra = Compra.objects.create(
        empresa_id=empresa_id, taller=taller, proveedor=proveedor, registrado_por=usuario, **datos,
    )
    referencia = f'Compra {compra.numero_factura} · {proveedor.nombre}'
    subtotal = iva = CERO
    # Bloqueo en orden de pk para que dos compras simultáneas no se crucen.
    for linea in sorted(lineas, key=lambda l: l['repuesto'].pk):
        repuesto = inventario.bloquear_repuesto(linea['repuesto'].pk)
        detalle = DetalleCompra.objects.create(compra=compra, **{**linea, 'repuesto': repuesto})
        repuesto.costo_referencial = inventario.costo_promedio(repuesto, detalle.cantidad, detalle.costo_unitario)
        inventario.mover_stock(
            repuesto,
            tipo=MovimientoInventario.Tipo.ENTRADA_COMPRA,
            cantidad=detalle.cantidad,
            usuario=usuario,
            costo_unitario=detalle.costo_unitario,
            compra=compra,
            referencia=referencia,
        )
        repuesto.save(update_fields=['costo_referencial', 'updated_at'])
        subtotal += detalle.subtotal
        iva += detalle.monto_iva
    compra.subtotal, compra.monto_iva, compra.total = subtotal, iva, subtotal + iva
    compra.save(update_fields=['subtotal', 'monto_iva', 'total', 'updated_at'])
    if compra.condicion == Compra.Condicion.CONTADO and compra.total > 0:
        movimiento = _egreso_de_contado(compra, monto=compra.total, forma_pago=forma_pago_contado, usuario=usuario)
        PagoCompra.objects.create(
            movimiento_caja=movimiento,
            compra=compra, monto=compra.total, forma_pago=forma_pago_contado,
            registrado_por=usuario, observacion='Pago de contado',
        )
    return compra


def _egreso_de_contado(compra, *, monto, forma_pago, usuario):
    if compra.condicion != Compra.Condicion.CONTADO or forma_pago != 'EFECTIVO':
        return None
    from apps.caja import services as caja
    from apps.caja.models import MovimientoCaja
    sesion = caja.sesion_abierta(compra.taller, bloquear=True) if compra.taller_id else None
    if sesion is None:
        raise serializers.ValidationError('Abre la caja del taller para registrar una compra de contado en efectivo.')
    monto_caja = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01')).run_validation(monto)
    return caja.registrar_movimiento(
        sesion, tipo=MovimientoCaja.Tipo.EGRESO, monto=monto_caja,
        motivo=f'Compra {compra.numero_factura} · {compra.proveedor.nombre}'[:255], usuario=usuario,
    )


def registrar_pago(compra, *, monto, usuario, **datos):
    if compra.estado == Compra.Estado.ANULADA:
        raise serializers.ValidationError('La compra está anulada; no admite pagos.')
    pendiente = saldo(compra)
    if pendiente <= 0:
        raise serializers.ValidationError('La compra no tiene saldo pendiente.')
    if monto > pendiente:
        raise serializers.ValidationError(
            {'monto': f'El pago supera el saldo pendiente con el proveedor ({_dinero(pendiente)}).'}
        )
    movimiento = _egreso_de_contado(compra, monto=monto, forma_pago=datos.get('forma_pago'), usuario=usuario)
    return PagoCompra.objects.create(compra=compra, monto=monto, registrado_por=usuario, movimiento_caja=movimiento, **datos)


def _anular_pago(pago, usuario, motivo):
    if pago.movimiento_caja_id:
        from apps.caja import services as caja
        from apps.caja.models import MovimientoCaja, SesionCaja
        sesion_id = MovimientoCaja.objects.values_list('sesion_id', flat=True).get(pk=pago.movimiento_caja_id)
        sesion = SesionCaja.objects.select_for_update().get(pk=sesion_id)
        movimiento = MovimientoCaja.objects.select_for_update().get(pk=pago.movimiento_caja_id)
        movimiento.sesion = sesion
        caja.anular_movimiento(movimiento, usuario=usuario, motivo=motivo, desde_compra=True)
    pago.anulado = True
    pago.fecha_anulacion = timezone.now()
    pago.anulado_por = usuario
    pago.motivo_anulacion = motivo
    pago.save(update_fields=['anulado', 'fecha_anulacion', 'anulado_por', 'motivo_anulacion', 'updated_at'])


def anular_pago(pago, *, usuario, motivo):
    if pago.anulado:
        raise serializers.ValidationError('El pago ya está anulado.')
    _anular_pago(pago, usuario, motivo)
    return pago


def anular_compra(compra, *, usuario, motivo):
    """Revierte el stock de cada línea y anula los pagos vigentes."""
    if compra.estado == Compra.Estado.ANULADA:
        raise serializers.ValidationError('La compra ya está anulada.')
    referencia = f'Anulación compra {compra.numero_factura}'
    detalles = sorted(compra.detalles.all(), key=lambda d: d.repuesto_id)
    for detalle in detalles:
        repuesto = inventario.bloquear_repuesto(detalle.repuesto_id)
        if repuesto.stock_actual < detalle.cantidad:
            raise serializers.ValidationError(
                f'No se puede anular: el repuesto {repuesto.codigo} tiene {repuesto.stock_actual} en stock '
                f'y la compra ingresó {detalle.cantidad}. Ajusta el inventario primero.'
            )
        inventario.mover_stock(
            repuesto,
            tipo=MovimientoInventario.Tipo.ANULACION_COMPRA,
            cantidad=-detalle.cantidad,
            usuario=usuario,
            costo_unitario=detalle.costo_unitario,
            compra=compra,
            referencia=referencia,
            motivo=motivo,
        )
    for pago in compra.pagos.filter(anulado=False):
        _anular_pago(pago, usuario, f'Compra anulada: {motivo}'[:255])
    compra.estado = Compra.Estado.ANULADA
    compra.fecha_anulacion = timezone.now()
    compra.anulada_por = usuario
    compra.motivo_anulacion = motivo
    compra.save(update_fields=['estado', 'fecha_anulacion', 'anulada_por', 'motivo_anulacion', 'updated_at'])
    return compra

