from rest_framework import serializers

from apps.authentication.models import UsuarioEmpresa
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

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)

    def validate(self, attrs):
        recepcion = attrs.get('recepcion')
        if recepcion is not None:
            inspecciones = recepcion.inspecciones.all()
            if self.instance:
                inspecciones = inspecciones.exclude(pk=self.instance.pk)
            if inspecciones.exists():
                raise serializers.ValidationError(
                    {'recepcion': 'Esta recepción ya tiene una inspección registrada.'}
                )
        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['tipo_inspeccion_display'] = instance.get_tipo_inspeccion_display()
        rep['estado_display'] = instance.get_estado_display()
        if instance.recepcion_id:
            rec = instance.recepcion
            rv = rec.vehiculo if rec.vehiculo_id else None
            rc = rec.cliente if rec.cliente_id else None
            rep['recepcion'] = {
                'id': rec.id,
                'vehiculo': {
                    'id': rv.id,
                    'placa': rv.placa,
                    'marca': rv.marca,
                    'modelo': rv.modelo,
                    'color': rv.color,
                } if rv else None,
                'cliente': {
                    'id': rc.id,
                    'nombre': rc.nombre,
                    'identificacion': rc.identificacion,
                    'telefono': rc.telefono,
                    'email': rc.email,
                } if rc else None,
                'placa': rv.placa if rv else None,
                'marca': rv.marca if rv else None,
                'modelo': rv.modelo if rv else None,
                'cliente_nombre': rc.nombre if rc else None,
                'motivo_ingreso': rec.motivo_ingreso,
                'created_at': rec.created_at.isoformat() if rec.created_at else None,
            }
        return rep


class RecepcionVehiculoSerializer(serializers.ModelSerializer):
    inspecciones = InspeccionVehiculoSerializer(many=True, read_only=True)

    class Meta:
        model = RecepcionVehiculo
        fields = '__all__'
        read_only_fields = ['id', 'fecha_firma_receptor', 'fecha_firma_cliente', 'aceptacion_condiciones']

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
                'kilometraje_actual': instance.vehiculo.kilometraje_actual,
            }
        if instance.cliente_id:
            rep['cliente'] = {
                'id': instance.cliente_id,
                'nombre': instance.cliente.nombre,
                'identificacion': instance.cliente.identificacion,
                'telefono': instance.cliente.telefono,
                'email': instance.cliente.email,
            }
        if instance.recibido_por_id:
            rep['recibido_por_nombre'] = instance.recibido_por.get_full_name() or instance.recibido_por.username
            usuario_empresa = UsuarioEmpresa.objects.filter(user=instance.recibido_por, empresa=instance.empresa).first()
            if usuario_empresa:
                rep['recibido_por_rol'] = usuario_empresa.rol
                rep['recibido_por_rol_display'] = usuario_empresa.get_rol_display()
            else:
                rep['recibido_por_rol'] = None
                rep['recibido_por_rol_display'] = None
        else:
            rep['recibido_por_nombre'] = None
            rep['recibido_por_rol'] = None
            rep['recibido_por_rol_display'] = None
        return rep

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            if not validated_data.get('recibido_por') and request.user.is_authenticated:
                validated_data['recibido_por'] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('fecha_firma_receptor', None)
        validated_data.pop('fecha_firma_cliente', None)
        validated_data.pop('aceptacion_condiciones', None)
        return super().update(instance, validated_data)


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
