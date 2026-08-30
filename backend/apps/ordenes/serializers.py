from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request

from .models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)


class DetalleServicioOrdenTrabajoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleServicioOrdenTrabajo
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class DetalleRepuestoOrdenTrabajoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleRepuestoOrdenTrabajo
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class InspeccionVehiculoSerializer(serializers.ModelSerializer):
    class Meta:
        model = InspeccionVehiculo
        fields = '__all__'
        read_only_fields = ['id']


class RecepcionVehiculoSerializer(serializers.ModelSerializer):
    inspecciones = InspeccionVehiculoSerializer(many=True, read_only=True)

    class Meta:
        model = RecepcionVehiculo
        fields = '__all__'
        read_only_fields = ['id']

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.vehiculo_id:
            rep['vehiculo'] = {
                'id': instance.vehiculo_id,
                'placa': instance.vehiculo.placa,
                'marca': instance.vehiculo.marca,
                'modelo': instance.vehiculo.modelo,
                'color': instance.vehiculo.color,
                'tipo': instance.vehiculo.tipo,
                'grupo_blueprint': instance.vehiculo.grupo_blueprint,
            }
        if instance.cliente_id:
            rep['cliente'] = {
                'id': instance.cliente_id,
                'nombre': instance.cliente.nombre,
                'identificacion': instance.cliente.identificacion,
                'telefono': instance.cliente.telefono,
                'email': instance.cliente.email,
            }
        return rep

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)


class OrdenTrabajoSerializer(serializers.ModelSerializer):
    servicios = DetalleServicioOrdenTrabajoSerializer(many=True, read_only=True)
    repuestos = DetalleRepuestoOrdenTrabajoSerializer(many=True, read_only=True)
    recepciones = RecepcionVehiculoSerializer(many=True, read_only=True)
    inspeccion = InspeccionVehiculoSerializer(read_only=True)

    class Meta:
        model = OrdenTrabajo
        fields = [
            'id',
            'empresa',
            'cliente',
            'vehiculo',
            'asesor',
            'mecanico_principal',
            'cotizacion_origen',
            'numero_orden',
            'estado',
            'prioridad',
            'tipo_trabajo',
            'observaciones_internas',
            'fecha_ingreso',
            'fecha_entrega',
            'subtotal_servicios',
            'subtotal_repuestos',
            'descuento',
            'subtotal_neto',
            'monto_iva',
            'total',
            'servicios',
            'repuestos',
            'recepciones',
            'inspeccion',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'subtotal_servicios', 'subtotal_repuestos', 'subtotal_neto', 'monto_iva', 'total', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)
