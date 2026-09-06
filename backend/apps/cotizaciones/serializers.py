from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request
from apps.empresas.services import generar_codigo_secuencial, resolver_taller

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
            'sucursal',
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
        read_only_fields = ['id', 'empresa', 'numero_cotizacion', 'subtotal', 'total_iva', 'total', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            sucursal = validated_data.get('sucursal')
            recepcion = validated_data.get('recepcion_origen')
            if sucursal is None and recepcion is not None and recepcion.sucursal_id:
                sucursal = recepcion.sucursal
            if sucursal is None:
                inspeccion = validated_data.get('inspeccion_origen')
                if inspeccion is not None:
                    if inspeccion.sucursal_id:
                        sucursal = inspeccion.sucursal
                    elif inspeccion.recepcion_id and inspeccion.recepcion.sucursal_id:
                        sucursal = inspeccion.recepcion.sucursal
            if sucursal is None:
                orden = validated_data.get('orden_trabajo_origen')
                if orden is not None and orden.sucursal_id:
                    sucursal = orden.sucursal
            taller = resolver_taller(empresa_id, sucursal)
            if taller is not None:
                validated_data.setdefault('sucursal', taller)
                validated_data['numero_cotizacion'] = generar_codigo_secuencial(taller, 'cotizacion')
        return super().create(validated_data)
