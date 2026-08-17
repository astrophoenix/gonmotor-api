from rest_framework import serializers
from .models import Cliente
from apps.vehiculos.serializers import VehiculoSerializer

class ClienteSerializer(serializers.ModelSerializer):
    # 🚗 Trae la lista de vehículos del cliente usando el `related_name='vehiculos'` de la relación
    vehiculos = VehiculoSerializer(many=True, read_only=True)

    class Meta:
        model = Cliente
        fields = [
            'id',
            'tipo_identificacion',
            'identificacion',
            'nombre',
            'email',
            'telefono',
            'direccion',
            'contifico_id',
            'is_active',
            'vehiculos',
            'created_at',
            'updated_at'
        ]
        # 💡 'empresa' debe ser de solo lectura para que el cliente HTTP no pueda falsearlo
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def create(self, validated_data):
        """
        Asigna automáticamente la empresa del usuario en sesión al crear el cliente.
        """
        request = self.context.get('request')
        if request and hasattr(request.user, 'profile'):
            validated_data['empresa'] = request.user.profile.empresa
        return super().create(validated_data)