"""Reglas de cobro de una orden de trabajo.

- Lo pagado neto (pagos menos devoluciones vigentes) nunca supera el total de
  la orden: no existen saldos a favor del cliente.
- Si un cambio en la orden dejaría el total por debajo de lo pagado, primero
  hay que registrar una devolución por la diferencia.
- Una orden anulada no recibe pagos; para anularla no debe quedar dinero del
  cliente en ella.

Quien llame a estas funciones debe tener la orden bloqueada
(`select_for_update`) dentro de la transacción.
"""
from decimal import Decimal

from django.db.models import DecimalField, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers

from .models import PagoOrden

CERO = Decimal('0.00')

ESTADOS_ANTICIPO = ('PENDIENTE', 'EN_ESPERA', 'EN_PROCESO')


class EstadoPago:
    PENDIENTE = 'PENDIENTE'
    PARCIAL = 'PARCIAL'
    PAGADO = 'PAGADO'
    # Orden anulada: no se le cobra nada (su saldo es 0).
    SIN_COBRO = 'SIN_COBRO'

    ETIQUETAS = {PENDIENTE: 'Pendiente de pago', PARCIAL: 'Pago parcial', PAGADO: 'Pagado', SIN_COBRO: 'Sin cobro'}


def _dinero(valor):
    return f'$ {valor:,.2f}'


def pagado_neto(orden):
    """Pagos menos devoluciones vigentes, leído de la BD (para validar)."""
    totales = PagoOrden.objects.filter(orden_trabajo=orden, anulado=False).aggregate(
        pagos=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.PAGO)),
        devoluciones=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.DEVOLUCION)),
    )
    return (totales['pagos'] or CERO) - (totales['devoluciones'] or CERO)


def resumen_pagos(orden):
    """Totales de cobro de la orden calculados en memoria sobre `orden.pagos.all()`.

    Sirve para serializar listados con los pagos precargados sin una consulta
    por fila.
    """
    pagado = devuelto = CERO
    for pago in orden.pagos.all():
        if pago.anulado:
            continue
        if pago.tipo == PagoOrden.Tipo.PAGO:
            pagado += pago.monto
        else:
            devuelto += pago.monto
    neto = pagado - devuelto
    saldo, estado = saldo_y_estado(orden.estado, orden.total, neto)
    return {
        'total_pagado': pagado,
        'total_devuelto': devuelto,
        'pagado_neto': neto,
        'saldo': saldo,
        'estado_pago': estado,
        'estado_pago_display': EstadoPago.ETIQUETAS[estado],
    }


def saldo_y_estado(estado_orden, total, neto):
    """`(saldo, estado_pago)` de una orden a partir de su total y lo pagado neto."""
    if estado_orden == 'CANCELADO':
        return CERO, EstadoPago.SIN_COBRO
    saldo = max((total or CERO) - neto, CERO)
    if saldo == CERO:
        return saldo, EstadoPago.PAGADO
    return saldo, EstadoPago.PARCIAL if neto > CERO else EstadoPago.PENDIENTE


def anotar_saldo(queryset):
    """Anota `pagado_neto_db` y `saldo_db` en un queryset de órdenes (para
    filtrar o exportar). Si ya están anotados, lo deja igual."""
    if 'saldo_db' in queryset.query.annotations:
        return queryset
    vigentes = PagoOrden.objects.filter(orden_trabajo=OuterRef('pk'), anulado=False)

    def suma(tipo):
        subconsulta = (
            vigentes.filter(tipo=tipo).values('orden_trabajo').annotate(suma=Sum('monto')).values('suma')
        )
        return Coalesce(Subquery(subconsulta, output_field=DecimalField()), Value(CERO))

    return queryset.annotate(
        pagado_neto_db=suma(PagoOrden.Tipo.PAGO) - suma(PagoOrden.Tipo.DEVOLUCION),
    ).annotate(saldo_db=F('total') - F('pagado_neto_db'))


def cuentas_por_cobrar(queryset):
    """Órdenes entregadas a crédito que aún tienen saldo, con `saldo_db`,
    `pagado_neto_db` y `ultimo_recordatorio` (último aviso de saldo enviado)."""
    from apps.notificaciones.models import NotificacionProgramada

    recordatorios = NotificacionProgramada.objects.filter(
        orden=OuterRef('pk'),
        tipo=NotificacionProgramada.Tipo.SALDO,
        estado=NotificacionProgramada.Estado.ENVIADA,
    ).order_by('-fecha_envio').values('fecha_envio')[:1]
    return (
        anotar_saldo(queryset.filter(estado='ENTREGADO'))
        .filter(saldo_db__gt=CERO)
        .annotate(ultimo_recordatorio=Subquery(recordatorios))
    )


def filtrar_por_estado_pago(queryset, estado):
    """Mismo criterio que `resumen_pagos`; un estado desconocido no filtra."""
    if estado == EstadoPago.SIN_COBRO:
        return queryset.filter(estado='CANCELADO')
    filtros = {
        EstadoPago.PAGADO: Q(saldo_db__lte=CERO),
        EstadoPago.PARCIAL: Q(saldo_db__gt=CERO, pagado_neto_db__gt=CERO),
        EstadoPago.PENDIENTE: Q(saldo_db__gt=CERO, pagado_neto_db__lte=CERO),
    }
    if estado not in filtros:
        return queryset
    return anotar_saldo(queryset.exclude(estado='CANCELADO')).filter(filtros[estado])


def filtrar_sin_factura(queryset):
    """Órdenes cobradas (algún pago vigente) sin comprobante externo registrado."""
    pagos_vigentes = PagoOrden.objects.filter(
        orden_trabajo=OuterRef('pk'), tipo=PagoOrden.Tipo.PAGO, anulado=False,
    )
    return queryset.exclude(estado='CANCELADO').filter(numero_factura='').filter(Exists(pagos_vigentes))


def estado_tras_movimiento(pago):
    """Pagado neto y saldo de la orden justo después de `pago` (para su recibo).

    Cuenta los movimientos vigentes registrados hasta él, incluido él mismo si
    no está anulado. El saldo usa el total actual de la orden.
    """
    orden = pago.orden_trabajo
    previos = PagoOrden.objects.filter(orden_trabajo=orden, anulado=False).filter(
        Q(fecha__lt=pago.fecha) | Q(fecha=pago.fecha, id__lte=pago.id)
    )
    totales = previos.aggregate(
        pagos=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.PAGO)),
        devoluciones=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.DEVOLUCION)),
    )
    neto = (totales['pagos'] or CERO) - (totales['devoluciones'] or CERO)
    saldo = CERO if orden.estado == 'CANCELADO' else max(orden.total - neto, CERO)
    return {'pagado_neto': neto, 'saldo': saldo}


def registrar_comprobante(orden, *, tipo_comprobante, numero_factura, fecha_factura):
    """Guarda (o borra, con número vacío) el comprobante externo de la orden."""
    if orden.estado == 'CANCELADO' and numero_factura:
        raise serializers.ValidationError('La orden está anulada; no admite comprobante.')
    if numero_factura:
        from apps.ordenes.models import OrdenTrabajo

        repetida = OrdenTrabajo.objects.filter(
            empresa_id=orden.empresa_id, tipo_comprobante=tipo_comprobante, numero_factura=numero_factura,
        ).exclude(pk=orden.pk).first()
        if repetida:
            raise serializers.ValidationError(
                {'numero_factura': f'Ese comprobante ya está registrado en la orden {repetida.numero_orden}.'}
            )
    orden.tipo_comprobante = tipo_comprobante if numero_factura else ''
    orden.numero_factura = numero_factura
    orden.fecha_factura = fecha_factura if numero_factura else None
    orden.save(update_fields=['tipo_comprobante', 'numero_factura', 'fecha_factura', 'updated_at'])
    return orden


def saldo_para_entrega(orden):
    """Saldo pendiente de la orden leído de la BD (0 si está pagada)."""
    return max(orden.total - pagado_neto(orden), CERO)


def validar_entrega(orden, confirma_credito):
    """Entregar con saldo pendiente exige confirmar la entrega a crédito."""
    saldo = saldo_para_entrega(orden)
    if saldo > CERO and not confirma_credito:
        raise serializers.ValidationError({
            'entrega_a_credito': (
                f'La orden tiene un saldo pendiente de {_dinero(saldo)}. '
                'Confirma la entrega a crédito o registra el pago antes de entregar.'
            ),
            'saldo': str(saldo),
        })


def validar_total_cubre_pagos(orden):
    """El total recalculado de la orden no puede quedar por debajo de lo pagado."""
    neto = pagado_neto(orden)
    if orden.total < neto:
        raise serializers.ValidationError(
            f'La orden ya tiene pagos por {_dinero(neto)} y su total quedaría en {_dinero(orden.total)}. '
            f'Registra primero una devolución de {_dinero(neto - orden.total)} al cliente.'
        )


def validar_anulacion_orden(orden):
    """Para anular la orden hay que devolver antes todo lo cobrado."""
    neto = pagado_neto(orden)
    if neto > CERO:
        raise serializers.ValidationError(
            {'estado': f'La orden tiene pagos por {_dinero(neto)}. Registra la devolución al cliente antes de anularla.'}
        )


def registrar_movimiento(orden, *, tipo, monto, usuario, **datos):
    """Crea un pago o una devolución validando el saldo de la orden.

    Queda en la sesión abierta de la caja del taller de la orden; con la caja
    cerrada no se registra.
    """
    from apps.caja import services as caja

    neto = pagado_neto(orden)
    if tipo == PagoOrden.Tipo.PAGO:
        if orden.estado == 'CANCELADO':
            raise serializers.ValidationError('La orden está anulada; no admite pagos.')
        saldo = orden.total - neto
        if saldo <= CERO:
            raise serializers.ValidationError('La orden no tiene saldo pendiente.')
        if monto > saldo:
            raise serializers.ValidationError(
                {'monto': f'El pago supera el saldo pendiente ({_dinero(saldo)}). No se aceptan saldos a favor.'}
            )
    elif monto > neto:
        raise serializers.ValidationError(
            {'monto': f'La devolución supera lo pagado por el cliente ({_dinero(neto)}).'}
        )
    sesion = caja.sesion_para_cobrar(orden)
    if tipo == PagoOrden.Tipo.DEVOLUCION and datos.get('forma_pago') == PagoOrden.FormaPago.EFECTIVO:
        caja.validar_salida_efectivo(sesion, monto)
    return PagoOrden.objects.create(
        orden_trabajo=orden,
        sesion_caja=sesion,
        tipo=tipo,
        monto=monto,
        registrado_por=usuario,
        es_anticipo=tipo == PagoOrden.Tipo.PAGO and orden.estado in ESTADOS_ANTICIPO,
        **datos,
    )


def anular_movimiento(pago, *, usuario, motivo):
    """Anula un pago o devolución. Anular una devolución no puede dejar saldo a favor."""
    from apps.caja import services as caja
    from apps.caja.models import SesionCaja

    if pago.anulado:
        raise serializers.ValidationError('El movimiento ya está anulado.')
    if pago.sesion_caja_id:
        sesion = SesionCaja.objects.select_for_update().get(pk=pago.sesion_caja_id)
        caja.validar_abierta(sesion, 'anular sus pagos')
        # Anular un pago en efectivo saca ese dinero de la caja.
        if pago.tipo == PagoOrden.Tipo.PAGO and pago.forma_pago == PagoOrden.FormaPago.EFECTIVO:
            caja.validar_salida_efectivo(sesion, pago.monto)
    if pago.tipo == PagoOrden.Tipo.DEVOLUCION:
        orden = pago.orden_trabajo
        if orden.estado == 'CANCELADO':
            raise serializers.ValidationError(
                'La orden está anulada; su devolución no se puede anular.'
            )
        if pagado_neto(orden) + pago.monto > orden.total:
            raise serializers.ValidationError(
                'No se puede anular la devolución: lo pagado superaría el total de la orden.'
            )
    pago.anulado = True
    pago.fecha_anulacion = timezone.now()
    pago.anulado_por = usuario
    pago.motivo_anulacion = motivo
    pago.save(update_fields=['anulado', 'fecha_anulacion', 'anulado_por', 'motivo_anulacion', 'updated_at'])
    return pago
