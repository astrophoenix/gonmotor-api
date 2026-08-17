from rest_framework import serializers

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
            'servicios',
            'repuestos',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'subtotal', 'total_iva', 'total', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request.user, 'profile'):
            validated_data['empresa'] = request.user.profile.empresa
        return super().create(validated_data)
