from decimal import Decimal

from rest_framework import serializers

from apps.authentication.utils import (
    get_contexto_desde_request,
    get_empresa_id_desde_request,
    get_taller_desde_request,
    talleres_permitidos,
)
from apps.core.serializers import ModelSerializerPublico
from apps.empresas.services import resolver_taller
from apps.inventario.models import Repuesto
from apps.proveedores.models import Proveedor

from .models import Compra, DetalleCompra, PagoCompra

FORMAS_PAGO = [valor for valor, _ in PagoCompra._meta.get_field('forma_pago').choices]
TARIFAS_IVA = (Decimal('0'), Decimal('0.15'))


def _nombre(usuario):
    return (usuario.get_full_name() or usuario.username) if usuario else None


class DetalleCompraSerializer(ModelSerializerPublico):
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)

    class Meta:
        model = DetalleCompra
        fields = [
            'id', 'repuesto', 'repuesto_codigo', 'repuesto_nombre', 'cantidad', 'costo_unitario',
            'iva_porcentaje', 'subtotal', 'monto_iva',
        ]
        read_only_fields = ['id', 'subtotal', 'monto_iva']

    def validate_iva_porcentaje(self, valor):
        if valor not in TARIFAS_IVA:
            raise serializers.ValidationError('El IVA debe ser 0 % o 15 %.')
        return valor


class PagoCompraSerializer(ModelSerializerPublico):
    forma_pago_display = serializers.CharField(source='get_forma_pago_display', read_only=True)
    numero_factura = serializers.CharField(source='compra.numero_factura', read_only=True)
    registrado_por_nombre = serializers.SerializerMethodField()
    anulado_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = PagoCompra
        fields = [
            'id', 'compra', 'numero_factura', 'forma_pago', 'forma_pago_display', 'monto', 'fecha', 'referencia',
            'observacion', 'registrado_por_nombre', 'anulado', 'fecha_anulacion', 'anulado_por_nombre',
            'motivo_anulacion',
        ]
        read_only_fields = ['id', 'fecha', 'anulado', 'fecha_anulacion', 'motivo_anulacion']
        extra_kwargs = {'monto': {'error_messages': {'min_value': 'El monto debe ser mayor a cero.'}}}

    def get_registrado_por_nombre(self, obj):
        return _nombre(obj.registrado_por)

    def get_anulado_por_nombre(self, obj):
        return _nombre(obj.anulado_por)

    def validate_compra(self, compra):
        request = self.context.get('request')
        if request is None or not Compra.objects.para_request(request).filter(pk=compra.pk).exists():
            raise serializers.ValidationError('La compra no existe o no está disponible para tu empresa o taller.')
        return compra

    def validate(self, attrs):
        attrs['referencia'] = (attrs.get('referencia') or '').strip()
        attrs['observacion'] = (attrs.get('observacion') or '').strip()
        if attrs.get('forma_pago') in ('TRANSFERENCIA', 'CHEQUE') and not attrs['referencia']:
            raise serializers.ValidationError({'referencia': 'Indica el número de la transferencia o del cheque.'})
        return attrs


class CompraSerializer(ModelSerializerPublico):
    detalles = DetalleCompraSerializer(many=True)
    pagos = PagoCompraSerializer(many=True, read_only=True)
    # Solo para las compras de contado: cómo se pagó al proveedor.
    forma_pago = serializers.ChoiceField(choices=FORMAS_PAGO, write_only=True, required=False, default='EFECTIVO')
    proveedor_nombre = serializers.CharField(source='proveedor.nombre', read_only=True)
    proveedor_identificacion = serializers.CharField(source='proveedor.identificacion', read_only=True)
    taller_nombre = serializers.CharField(source='taller.nombre', read_only=True, default=None)
    condicion_display = serializers.CharField(source='get_condicion_display', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    registrado_por_nombre = serializers.SerializerMethodField()
    anulada_por_nombre = serializers.SerializerMethodField()
    pagado = serializers.SerializerMethodField()
    saldo = serializers.SerializerMethodField()

    class Meta:
        model = Compra
        fields = [
            'id', 'empresa', 'taller', 'taller_nombre', 'proveedor', 'proveedor_nombre', 'proveedor_identificacion',
            'numero_factura', 'fecha', 'condicion', 'condicion_display', 'fecha_vencimiento', 'observaciones',
            'subtotal', 'monto_iva', 'total', 'pagado', 'saldo', 'estado', 'estado_display',
            'registrado_por_nombre', 'fecha_anulacion', 'anulada_por_nombre', 'motivo_anulacion',
            'detalles', 'pagos', 'forma_pago', 'created_at',
        ]
        read_only_fields = [
            'id', 'empresa', 'subtotal', 'monto_iva', 'total', 'estado', 'fecha_anulacion', 'motivo_anulacion',
            'created_at',
        ]

    def get_registrado_por_nombre(self, obj):
        return _nombre(obj.registrado_por)

    def get_anulada_por_nombre(self, obj):
        return _nombre(obj.anulada_por)

    def get_pagado(self, obj):
        if hasattr(obj, 'pagado_db'):
            return str(obj.pagado_db)
        return str(sum((p.monto for p in obj.pagos.all() if not p.anulado), Decimal('0.00')))

    def get_saldo(self, obj):
        if hasattr(obj, 'saldo_db'):
            return str(obj.saldo_db)
        if obj.estado == Compra.Estado.ANULADA:
            return '0.00'
        return str(max(obj.total - Decimal(self.get_pagado(obj)), Decimal('0.00')))

    def _empresa_id(self):
        return get_empresa_id_desde_request(self.context['request'])

    def validate_proveedor(self, proveedor):
        if proveedor.empresa_id != self._empresa_id() or not proveedor.is_active:
            raise serializers.ValidationError('El proveedor no existe o está inactivo.')
        return proveedor

    def validate_taller(self, taller):
        request = self.context.get('request')
        if taller is None or request is None:
            return taller
        contexto = get_contexto_desde_request(request)
        if not talleres_permitidos(request.user, contexto).filter(pk=taller.pk).exists():
            raise serializers.ValidationError('El taller no pertenece a tu empresa o no tienes acceso a él.')
        return taller

    def validate_numero_factura(self, valor):
        valor = (valor or '').strip()
        if not valor:
            raise serializers.ValidationError('Indica el número de factura del proveedor.')
        return valor

    def validate_detalles(self, detalles):
        if not detalles:
            raise serializers.ValidationError('Agrega al menos un repuesto a la compra.')
        empresa_id = self._empresa_id()
        vistos = set()
        for detalle in detalles:
            repuesto = detalle['repuesto']
            if repuesto.empresa_id != empresa_id:
                raise serializers.ValidationError('Uno de los repuestos no pertenece a tu empresa.')
            if repuesto.pk in vistos:
                raise serializers.ValidationError(f'El repuesto {repuesto.codigo} está repetido en la compra.')
            vistos.add(repuesto.pk)
        return detalles

    def validate(self, attrs):
        if attrs.get('condicion') == Compra.Condicion.CREDITO:
            vence = attrs.get('fecha_vencimiento')
            if vence and attrs.get('fecha') and vence < attrs['fecha']:
                raise serializers.ValidationError({'fecha_vencimiento': 'El vencimiento no puede ser anterior a la factura.'})
        else:
            attrs['fecha_vencimiento'] = None
        duplicada = Compra.objects.filter(
            empresa_id=self._empresa_id(), proveedor=attrs.get('proveedor'),
            numero_factura=attrs.get('numero_factura'), estado=Compra.Estado.REGISTRADA,
        ).exists()
        if duplicada:
            raise serializers.ValidationError({'numero_factura': 'Esta factura del proveedor ya está registrada.'})
        return attrs

    def create(self, validated_data):
        from . import services

        request = self.context['request']
        empresa_id = self._empresa_id()
        lineas = validated_data.pop('detalles')
        forma_pago = validated_data.pop('forma_pago', 'EFECTIVO')
        taller = validated_data.pop('taller', None) or get_taller_desde_request(request)
        return services.registrar_compra(
            empresa_id=empresa_id,
            taller=resolver_taller(empresa_id, taller),
            lineas=lineas,
            usuario=request.user,
            forma_pago_contado=forma_pago,
            **validated_data,
        )


class AnularSerializer(serializers.Serializer):
    motivo = serializers.CharField(
        max_length=255,
        error_messages={
            'required': 'Indica el motivo de la anulación.',
            'blank': 'Indica el motivo de la anulación.',
        },
    )
