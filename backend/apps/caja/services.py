"""Reglas de la caja del taller.

- Un taller tiene hoy una sola caja («Caja principal», se crea al abrirla).
- Una caja tiene a lo sumo una sesión abierta; para abrir otra hay que
  cerrar la anterior.
- Todo pago o devolución de una orden se registra en la sesión abierta de la
  caja de su taller: con la caja cerrada no se cobra.
- El efectivo esperado = monto inicial + pagos en efectivo − devoluciones en
  efectivo + ingresos − egresos (todo lo vigente, sin anulados).
- Una sesión cerrada no se modifica; solo se reabre con la acción especial
  `reabrir_caja`.

Quien llame a estas funciones debe estar dentro de una transacción.
"""
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers

from apps.authentication.utils import get_taller_desde_request, talleres_filtro_de_request
from apps.empresas.models import Taller

from .models import Caja, MovimientoCaja, SesionCaja

CERO = Decimal('0.00')
EFECTIVO = 'EFECTIVO'


def _dinero(valor):
    return f'$ {valor:,.2f}'


# ── Taller y caja ─────────────────────────────────────────────────────────
def taller_de_operacion(request, valor=None):
    """Taller en el que opera la petición (`valor` explícito, cabecera o perfil).

    Con «Todos los talleres» no hay un taller de caja: hay que elegir uno.
    """
    filtro = talleres_filtro_de_request(request, explicito=valor or None)
    if filtro is None:
        raise serializers.ValidationError({'taller': 'Elige el taller de la caja.'})
    if not filtro:
        raise serializers.ValidationError({'taller': 'El taller no existe o no tienes acceso a él.'})
    pk = filtro[0] if len(filtro) == 1 else get_taller_desde_request(request)
    return Taller.objects.get(pk=pk)


def caja_del_taller(taller):
    caja, _ = Caja.objects.get_or_create(
        taller=taller, nombre='Caja principal', defaults={'empresa_id': taller.empresa_id},
    )
    return caja


def sesion_abierta(taller, bloquear=False):
    """Sesión abierta de la caja del taller, o None."""
    queryset = SesionCaja.objects.filter(caja__taller=taller, estado=SesionCaja.Estado.ABIERTA)
    if bloquear:
        queryset = queryset.select_for_update()
    return queryset.order_by('id').first()


def sesion_para_cobrar(orden):
    """Sesión abierta (bloqueada) donde se registra un pago o devolución de la orden."""
    sesion = sesion_abierta(orden.sucursal, bloquear=True) if orden.sucursal_id else None
    if sesion is None:
        nombre = orden.sucursal.nombre if orden.sucursal_id else 'de la orden'
        raise serializers.ValidationError(
            f'La caja del taller {nombre} está cerrada. Ábrela para registrar pagos o devoluciones.'
        )
    return sesion


def validar_abierta(sesion, accion='modificarla'):
    if sesion.estado != SesionCaja.Estado.ABIERTA:
        raise serializers.ValidationError(
            f'La caja ya está cerrada; un administrador debe reabrirla para {accion}.'
        )


# ── Totales ───────────────────────────────────────────────────────────────
def resumen(sesion):
    """Totales de la sesión por forma de pago y efectivo esperado."""
    from apps.pagos.models import PagoOrden

    filas = (
        PagoOrden.objects.filter(sesion_caja=sesion, anulado=False)
        .values('forma_pago')
        .annotate(
            pagos=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.PAGO)),
            devoluciones=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.DEVOLUCION)),
        )
    )
    etiquetas = dict(PagoOrden.FormaPago.choices)
    por_forma = []
    efectivo_pagos = efectivo_devoluciones = total_pagos = total_devoluciones = CERO
    for fila in sorted(filas, key=lambda f: list(etiquetas).index(f['forma_pago'])):
        pagos = fila['pagos'] or CERO
        devoluciones = fila['devoluciones'] or CERO
        total_pagos += pagos
        total_devoluciones += devoluciones
        if fila['forma_pago'] == EFECTIVO:
            efectivo_pagos, efectivo_devoluciones = pagos, devoluciones
        por_forma.append({
            'forma_pago': fila['forma_pago'],
            'forma_pago_display': etiquetas[fila['forma_pago']],
            'pagos': pagos,
            'devoluciones': devoluciones,
            'neto': pagos - devoluciones,
        })

    movimientos = MovimientoCaja.objects.filter(sesion=sesion, anulado=False).aggregate(
        ingresos=Sum('monto', filter=Q(tipo=MovimientoCaja.Tipo.INGRESO)),
        egresos=Sum('monto', filter=Q(tipo=MovimientoCaja.Tipo.EGRESO)),
    )
    ingresos = movimientos['ingresos'] or CERO
    egresos = movimientos['egresos'] or CERO
    return {
        'monto_inicial': sesion.monto_inicial,
        'por_forma': por_forma,
        'total_pagos': total_pagos,
        'total_devoluciones': total_devoluciones,
        'efectivo_pagos': efectivo_pagos,
        'efectivo_devoluciones': efectivo_devoluciones,
        'ingresos': ingresos,
        'egresos': egresos,
        'efectivo_esperado': sesion.monto_inicial + efectivo_pagos - efectivo_devoluciones + ingresos - egresos,
    }


def validar_salida_efectivo(sesion, monto):
    """No sale de la caja más efectivo del que hay."""
    disponible = resumen(sesion)['efectivo_esperado']
    if monto > disponible:
        raise serializers.ValidationError(
            {'monto': f'En la caja solo hay {_dinero(disponible)} en efectivo.'}
        )


# ── Ciclo de la sesión ────────────────────────────────────────────────────
def abrir(taller, *, monto_inicial, usuario):
    caja = Caja.objects.select_for_update().get(pk=caja_del_taller(taller).pk)
    if sesion_abierta(taller) is not None:
        raise serializers.ValidationError('La caja de este taller ya está abierta.')
    return SesionCaja.objects.create(caja=caja, monto_inicial=monto_inicial, abierta_por=usuario)


def ultimo_cierre(taller):
    return (
        SesionCaja.objects.filter(caja__taller=taller, estado=SesionCaja.Estado.CERRADA)
        .order_by('-fecha_cierre', '-id').first()
    )


def cerrar(sesion, *, efectivo_contado, observacion, usuario):
    validar_abierta(sesion, 'cerrarla de nuevo')
    esperado = resumen(sesion)['efectivo_esperado']
    diferencia = efectivo_contado - esperado
    observacion = (observacion or '').strip()
    if diferencia and not observacion:
        tipo = 'sobrante' if diferencia > 0 else 'faltante'
        raise serializers.ValidationError(
            {'observacion': f'Hay un {tipo} de {_dinero(abs(diferencia))}. Explica la diferencia.'}
        )
    sesion.estado = SesionCaja.Estado.CERRADA
    sesion.fecha_cierre = timezone.now()
    sesion.cerrada_por = usuario
    sesion.efectivo_esperado = esperado
    sesion.efectivo_contado = efectivo_contado
    sesion.diferencia = diferencia
    sesion.observacion_cierre = observacion
    sesion.save()
    return sesion


def reabrir(sesion, *, motivo, usuario):
    if sesion.estado != SesionCaja.Estado.CERRADA:
        raise serializers.ValidationError('La caja ya está abierta.')
    if SesionCaja.objects.filter(caja=sesion.caja, estado=SesionCaja.Estado.ABIERTA).exists():
        raise serializers.ValidationError('Cierra primero la caja abierta de este taller para reabrir esta.')
    sesion.estado = SesionCaja.Estado.ABIERTA
    sesion.reaperturas += 1
    sesion.fecha_reapertura = timezone.now()
    sesion.reabierta_por = usuario
    sesion.motivo_reapertura = motivo
    sesion.save()
    return sesion


# ── Ingresos y egresos ────────────────────────────────────────────────────
def registrar_movimiento(sesion, *, tipo, monto, motivo, usuario, categoria_gasto=None):
    """Ingreso o egreso de efectivo. Con `categoria_gasto`, el egreso queda
    además como gasto del taller."""
    validar_abierta(sesion, 'registrar movimientos')
    if tipo == MovimientoCaja.Tipo.EGRESO:
        validar_salida_efectivo(sesion, monto)
    gasto = None
    if categoria_gasto:
        if tipo != MovimientoCaja.Tipo.EGRESO:
            raise serializers.ValidationError({'categoria_gasto': 'Solo un egreso se registra como gasto.'})
        from apps.gastos.models import Gasto

        gasto = Gasto.objects.create(
            empresa_id=sesion.caja.empresa_id,
            taller_id=sesion.caja.taller_id,
            fecha=timezone.localdate(),
            categoria=categoria_gasto,
            monto=monto,
            descripcion=motivo[:255],
            creado_por=usuario,
        )
    return MovimientoCaja.objects.create(
        sesion=sesion, tipo=tipo, monto=monto, motivo=motivo, gasto=gasto, registrado_por=usuario,
    )


def anular_movimiento(movimiento, *, usuario, motivo, desde_compra=False):
    if not desde_compra:
        from apps.compras.models import PagoCompra
        if PagoCompra.objects.filter(movimiento_caja_id=movimiento.pk).exists():
            raise serializers.ValidationError('Este egreso pertenece a una compra. Anula el pago o la compra desde Compras.')
    validar_abierta(movimiento.sesion, 'anular sus movimientos')
    if movimiento.anulado:
        raise serializers.ValidationError('El movimiento ya está anulado.')
    if movimiento.tipo == MovimientoCaja.Tipo.INGRESO:
        validar_salida_efectivo(movimiento.sesion, movimiento.monto)
    movimiento.anulado = True
    movimiento.fecha_anulacion = timezone.now()
    movimiento.anulado_por = usuario
    movimiento.motivo_anulacion = motivo
    movimiento.save(update_fields=['anulado', 'fecha_anulacion', 'anulado_por', 'motivo_anulacion', 'updated_at'])
    if movimiento.gasto_id:
        # El gasto creado con el egreso se elimina (borrado lógico) con él.
        movimiento.gasto.delete()
    return movimiento
