from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request

from .models import Cotizacion, DetalleRepuestoCotizacion, DetalleServicioCotizacion


class DetalleServicioCotizacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleServicioCotizacion
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class DetalleRepuestoCotizacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleRepuestoCotizacion
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class CotizacionSerializer(serializers.ModelSerializer):
    servicios = DetalleServicioCotizacionSerializer(many=True, read_only=True)
    repuestos = DetalleRepuestoCotizacionSerializer(many=True, read_only=True)

    class Meta:
        model = Cotizacion
        fields = [
            'id',
            'empresa',
            'cliente',
            'vehiculo',
            'numero_cotizacion',
            'estado',
            'validez_dias',
            'subtotal',
            'total_iva',
            'total',
            'observaciones',
            'recepcion_origen',
            'inspeccion_origen',
            'orden_trabajo_origen',
            'fecha_aceptacion',
            'aceptada_por',
            'metodo_aceptacion',
            'servicios',
            'repuestos',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'subtotal', 'total_iva', 'total', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)
