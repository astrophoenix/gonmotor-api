from rest_framework import serializers
from .models import Vehiculo


class VehiculoSerializer(serializers.ModelSerializer):
    # Campo de solo lectura para obtener rápidamente el nombre del cliente
    cliente_nombre = serializers.ReadOnlyField(source='cliente.nombre')

    class Meta:
        model = Vehiculo
        fields = '__all__'
        # 💡 Bloqueamos 'empresa' y marcas de tiempo para que no se puedan falsear desde el frontend
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def create(self, validated_data):
        """
        Asigna automáticamente la empresa del usuario en sesión al registrar el vehículo.
        """
        request = self.context.get('request')
        if request and hasattr(request.user, 'profile'):
            validated_data['empresa'] = request.user.profile.empresa
        return super().create(validated_data)