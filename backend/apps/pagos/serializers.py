from django.utils import timezone
from rest_framework import serializers

from apps.core.ids_publicos import id_publico
from apps.core.mixins import ValidarDocumentoPadreMixin
from apps.core.serializers import ModelSerializerPublico
from apps.ordenes.models import OrdenTrabajo

from .models import PagoOrden


def _nombre(usuario):
    return (usuario.get_full_name() or usuario.username) if usuario else None


class PagoOrdenSerializer(ValidarDocumentoPadreMixin, ModelSerializerPublico):
    campo_documento = 'orden_trabajo'

    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    forma_pago_display = serializers.CharField(source='get_forma_pago_display', read_only=True)
    numero_orden = serializers.CharField(source='orden_trabajo.numero_orden', read_only=True)
    registrado_por_nombre = serializers.SerializerMethodField()
    anulado_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = PagoOrden
        fields = [
            'id',
            'orden_trabajo',
            'numero_orden',
            'tipo',
            'tipo_display',
            'forma_pago',
            'forma_pago_display',
            'monto',
            'fecha',
            'referencia',
            'observacion',
            'es_anticipo',
            'registrado_por_nombre',
            'anulado',
            'fecha_anulacion',
            'anulado_por_nombre',
            'motivo_anulacion',
            'created_at',
        ]
        read_only_fields = [
            'id', 'fecha', 'es_anticipo', 'anulado', 'fecha_anulacion', 'motivo_anulacion', 'created_at',
        ]
        extra_kwargs = {
            'monto': {
                'error_messages': {'min_value': 'El monto debe ser mayor a cero.'},
            },
        }

    def get_registrado_por_nombre(self, obj):
        return _nombre(obj.registrado_por)

    def get_anulado_por_nombre(self, obj):
        return _nombre(obj.anulado_por)

    def validate_referencia(self, valor):
        return (valor or '').strip()

    def validate_observacion(self, valor):
        return (valor or '').strip()

    def validate(self, attrs):
        forma = attrs.get('forma_pago')
        if forma in (PagoOrden.FormaPago.TRANSFERENCIA, PagoOrden.FormaPago.CHEQUE) and not attrs.get('referencia'):
            etiqueta = 'la transferencia' if forma == PagoOrden.FormaPago.TRANSFERENCIA else 'el cheque'
            raise serializers.ValidationError({'referencia': f'Indica el número de {etiqueta}.'})
        return attrs


class AnularPagoSerializer(serializers.Serializer):
    motivo = serializers.CharField(
        max_length=255,
        error_messages={
            'required': 'Indica el motivo de la anulación.',
            'blank': 'Indica el motivo de la anulación.',
        },
    )


class CuentaPorCobrarSerializer(ModelSerializerPublico):
    """Orden entregada con saldo (anotada por `services.cuentas_por_cobrar`)."""

    saldo = serializers.DecimalField(source='saldo_db', max_digits=10, decimal_places=2, read_only=True)
    pagado_neto = serializers.DecimalField(source='pagado_neto_db', max_digits=10, decimal_places=2, read_only=True)
    ultimo_recordatorio = serializers.DateTimeField(read_only=True)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True, default=None)
    dias_atraso = serializers.SerializerMethodField()
    cliente = serializers.SerializerMethodField()
    vehiculo = serializers.SerializerMethodField()
    entrega_credito_autorizada_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = OrdenTrabajo
        fields = [
            'id',
            'numero_orden',
            'sucursal_nombre',
            'cliente',
            'vehiculo',
            'fecha_entrega',
            'dias_atraso',
            'total',
            'pagado_neto',
            'saldo',
            'saldo_al_entregar',
            'entrega_credito_autorizada_por_nombre',
            'ultimo_recordatorio',
        ]
        read_only_fields = fields

    def get_dias_atraso(self, obj):
        if not obj.fecha_entrega:
            return 0
        return max((timezone.localdate() - timezone.localdate(obj.fecha_entrega)).days, 0)

    def get_cliente(self, obj):
        cliente = obj.cliente
        return {
            'id': id_publico(cliente),
            'nombre': cliente.nombre,
            'identificacion': cliente.identificacion,
            'telefono': cliente.telefono,
            'email': cliente.email,
        }

    def get_vehiculo(self, obj):
        vehiculo = obj.vehiculo
        return {'id': id_publico(vehiculo), 'placa': vehiculo.placa, 'marca': vehiculo.marca, 'modelo': vehiculo.modelo}

    def get_entrega_credito_autorizada_por_nombre(self, obj):
        return _nombre(obj.entrega_credito_autorizada_por)
