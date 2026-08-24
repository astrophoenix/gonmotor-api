from rest_framework import serializers
from .models import Vehiculo


class VehiculoNestedSerializer(serializers.ModelSerializer):
    imagen = serializers.ImageField(required=False, allow_null=True)

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
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def create(self, validated_data):
        empresas = validated_data.pop('empresas', [])
        imagen = validated_data.pop('imagen', None)
        instance = super().create(validated_data)
        if imagen:
            instance.imagen = imagen
            instance.save()
        if empresas:
            instance.empresas.set(empresas)
        return instance

    def update(self, instance, validated_data):
        empresas = validated_data.pop('empresas', None)
        imagen = validated_data.pop('imagen', None)
        instance = super().update(instance, validated_data)
        if imagen is not None:
            instance.imagen = imagen
            instance.save()
        if empresas is not None:
            instance.empresas.set(empresas)
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

    class Meta:
        model = Vehiculo
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'cliente_nombre', 'cliente_id']

    def create(self, validated_data):
        empresas = validated_data.pop('empresas', [])
        request = self.context.get('request')
        if request and hasattr(request.user, 'profile'):
            user_empresa = request.user.profile.empresa
            if user_empresa and user_empresa not in empresas:
                empresas.append(user_empresa)

        instance = super().create(validated_data)
        if empresas:
            instance.empresas.set(empresas)
        return instance
