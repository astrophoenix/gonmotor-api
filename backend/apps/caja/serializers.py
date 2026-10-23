from decimal import Decimal

from rest_framework import serializers

from apps.core.ids_publicos import id_publico
from apps.core.serializers import ModelSerializerPublico

from . import services
from .models import MovimientoCaja, SesionCaja


def _nombre(usuario):
    return (usuario.get_full_name() or usuario.username) if usuario else None


def _texto(valor):
    return str(valor) if isinstance(valor, Decimal) else valor


class MovimientoCajaSerializer(ModelSerializerPublico):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    registrado_por_nombre = serializers.SerializerMethodField()
    anulado_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoCaja
        fields = [
            'id', 'tipo', 'tipo_display', 'monto', 'motivo', 'gasto', 'fecha', 'registrado_por_nombre',
            'anulado', 'fecha_anulacion', 'anulado_por_nombre', 'motivo_anulacion',
        ]
        read_only_fields = fields

    def get_registrado_por_nombre(self, obj):
        return _nombre(obj.registrado_por)

    def get_anulado_por_nombre(self, obj):
        return _nombre(obj.anulado_por)


class SesionCajaSerializer(ModelSerializerPublico):
    """Sesión con sus totales. Con `detalle=True` en el contexto incluye la
    lista de pagos de órdenes y de movimientos de la jornada."""

    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    taller = serializers.SerializerMethodField()
    caja_nombre = serializers.CharField(source='caja.nombre', read_only=True)
    abierta_por_nombre = serializers.SerializerMethodField()
    cerrada_por_nombre = serializers.SerializerMethodField()
    reabierta_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = SesionCaja
        fields = [
            'id', 'estado', 'estado_display', 'taller', 'caja_nombre',
            'monto_inicial', 'fecha_apertura', 'abierta_por_nombre',
            'fecha_cierre', 'cerrada_por_nombre', 'efectivo_esperado', 'efectivo_contado', 'diferencia',
            'observacion_cierre', 'reaperturas', 'fecha_reapertura', 'reabierta_por_nombre', 'motivo_reapertura',
        ]
        read_only_fields = fields

    def get_taller(self, obj):
        taller = obj.caja.taller
        return {'id': id_publico(taller), 'nombre': taller.nombre}

    def get_abierta_por_nombre(self, obj):
        return _nombre(obj.abierta_por)

    def get_cerrada_por_nombre(self, obj):
        return _nombre(obj.cerrada_por)

    def get_reabierta_por_nombre(self, obj):
        return _nombre(obj.reabierta_por)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if not self.context.get('detalle'):
            return rep
        resumen = services.resumen(instance)
        resumen['por_forma'] = [
            {clave: _texto(valor) for clave, valor in fila.items()} for fila in resumen['por_forma']
        ]
        rep['resumen'] = {clave: _texto(valor) for clave, valor in resumen.items()}
        rep['pagos'] = [
            {
                'id': id_publico(pago),
                'tipo': pago.tipo,
                'tipo_display': pago.get_tipo_display(),
                'forma_pago': pago.forma_pago,
                'forma_pago_display': pago.get_forma_pago_display(),
                'monto': str(pago.monto),
                'referencia': pago.referencia,
                'fecha': pago.fecha,
                'orden_id': id_publico(pago.orden_trabajo),
                'numero_orden': pago.orden_trabajo.numero_orden,
                'cliente_nombre': pago.orden_trabajo.cliente.nombre,
                'registrado_por_nombre': _nombre(pago.registrado_por),
                'anulado': pago.anulado,
                'motivo_anulacion': pago.motivo_anulacion,
            }
            for pago in instance.pagos.select_related(
                'orden_trabajo__cliente', 'registrado_por',
            ).order_by('-fecha', '-id')
        ]
        rep['movimientos'] = MovimientoCajaSerializer(
            instance.movimientos.select_related('registrado_por', 'anulado_por').order_by('-fecha', '-id'),
            many=True, context=self.context,
        ).data
        return rep


class AbrirCajaSerializer(serializers.Serializer):
    taller = serializers.CharField(required=False, allow_blank=True)
    monto_inicial = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.00'),
        error_messages={'min_value': 'El monto inicial no puede ser negativo.'},
    )


class CerrarCajaSerializer(serializers.Serializer):
    efectivo_contado = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.00'),
        error_messages={'min_value': 'El efectivo contado no puede ser negativo.'},
    )
    observacion = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class MotivoSerializer(serializers.Serializer):
    motivo = serializers.CharField(
        max_length=255,
        error_messages={'required': 'Indica el motivo.', 'blank': 'Indica el motivo.'},
    )


class NuevoMovimientoSerializer(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=MovimientoCaja.Tipo.choices)
    monto = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'),
        error_messages={'min_value': 'El monto debe ser mayor a cero.'},
    )
    motivo = serializers.CharField(
        max_length=255,
        error_messages={'required': 'Indica el motivo.', 'blank': 'Indica el motivo.'},
    )
    categoria_gasto = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_categoria_gasto(self, valor):
        from apps.gastos.models import Gasto

        if valor and valor not in Gasto.Categoria.values:
            raise serializers.ValidationError('Categoría de gasto no válida.')
        return valor
