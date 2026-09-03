from rest_framework import serializers
from django.utils import timezone
from .models import Vehiculo, VehiculoPropietario
from apps.clientes.models import Cliente
from apps.core.utils.images import validate_image_extension
from apps.authentication.utils import get_empresa_id_desde_request
from apps.empresas.models import Empresa


class ClienteNullablePkField(serializers.PrimaryKeyRelatedField):
    def to_internal_value(self, data):
        if data in (None, ''):
            return None
        return super().to_internal_value(data)


class VehiculoNestedSerializer(serializers.ModelSerializer):
    imagen = serializers.ImageField(required=False, allow_null=True)
    cliente_id = ClienteNullablePkField(
        queryset=Cliente.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Vehiculo
        fields = [
            'id',
            'placa',
            'vin',
            'numero_motor',
            'marca',
            'modelo',
            'anio',
            'color',
            'transmision',
            'combustible',
            'tipo',
            'pais_origen',
            'kilometraje_actual',
            'observaciones',
            'is_active',
            'imagen',
            'empresas',
            'cliente_id',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_imagen(self, value):
        if not value:
            return value

        validate_image_extension(value)

        if value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError(
                'La imagen no debe superar los 2MB.'
            )

        return value

    def _aplicar_propietario(self, instance, cliente):
        if cliente is not None:
            relacion_activa = instance.propietarios.filter(es_actual=True).first()
            if relacion_activa and relacion_activa.cliente_id == cliente.id:
                return
            if relacion_activa:
                relacion_activa.es_actual = False
                relacion_activa.fecha_fin = timezone.localdate()
                relacion_activa.save(update_fields=['es_actual', 'fecha_fin'])
            VehiculoPropietario.objects.update_or_create(
                vehiculo=instance,
                cliente=cliente,
                es_actual=True,
                defaults={'fecha_fin': None},
            )
        else:
            instance.propietarios.filter(es_actual=True).update(
                es_actual=False,
                fecha_fin=timezone.localdate(),
            )

    def create(self, validated_data):
        cliente = validated_data.pop('cliente_id', None)
        empresas = validated_data.pop('empresas', [])
        imagen = validated_data.pop('imagen', None)
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                try:
                    user_empresa = Empresa.objects.get(id=empresa_id)
                    if user_empresa not in empresas:
                        empresas.append(user_empresa)
                except Empresa.DoesNotExist:
                    pass

        instance = super().create(validated_data)
        if imagen:
            instance.imagen = imagen
            instance.save()
        if empresas:
            instance.empresas.set(empresas)
        if cliente is not None:
            self._aplicar_propietario(instance, cliente)
        return instance

    def update(self, instance, validated_data):
        cliente = validated_data.pop('cliente_id', None)
        empresas = validated_data.pop('empresas', None)
        imagen = validated_data.pop('imagen', None)
        has_cliente = 'cliente_id' in self.get_initial()
        instance = super().update(instance, validated_data)
        if imagen is not None:
            instance.imagen = imagen
            instance.save()
        if empresas is not None:
            instance.empresas.set(empresas)
        if has_cliente:
            self._aplicar_propietario(instance, cliente)
        return instance


class VehiculoSerializer(serializers.ModelSerializer):
    cliente_nombre = serializers.SerializerMethodField()
    cliente_id = serializers.SerializerMethodField()

    def _propietario_actual(self, obj):
        return obj.propietarios.filter(es_actual=True).select_related('cliente').first()

    def get_cliente_nombre(self, obj):
        propietario = self._propietario_actual(obj)
        return propietario.cliente.nombre if propietario else None

    def get_cliente_id(self, obj):
        propietario = self._propietario_actual(obj)
        return propietario.cliente_id if propietario else None

    def validate_imagen(self, value):
        if not value:
            return value

        validate_image_extension(value)

        if value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError(
                'La imagen no debe superar los 2MB.'
            )

        return value

    class Meta:
        model = Vehiculo
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'cliente_nombre', 'cliente_id']

    def create(self, validated_data):
        empresas = validated_data.pop('empresas', [])
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                try:
                    user_empresa = Empresa.objects.get(id=empresa_id)
                    if user_empresa not in empresas:
                        empresas.append(user_empresa)
                except Empresa.DoesNotExist:
                    pass

        instance = super().create(validated_data)
        if empresas:
            instance.empresas.set(empresas)
        return instance
