from rest_framework import serializers

from .models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
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


class RecepcionVehiculoSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecepcionVehiculo
        fields = '__all__'
        read_only_fields = ['id']


class OrdenTrabajoSerializer(serializers.ModelSerializer):
    servicios = DetalleServicioOrdenTrabajoSerializer(many=True, read_only=True)
    repuestos = DetalleRepuestoOrdenTrabajoSerializer(many=True, read_only=True)
    recepcion = RecepcionVehiculoSerializer(read_only=True)

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
            'kilometraje_ingreso',
            'nivel_combustible',
            'falla_reportada',
            'diagnostico_tecnico',
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
            'recepcion',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'subtotal_servicios', 'subtotal_repuestos', 'subtotal_neto', 'monto_iva', 'total', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request.user, 'profile'):
            validated_data['empresa'] = request.user.profile.empresa
        return super().create(validated_data)
