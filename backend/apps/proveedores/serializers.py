from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request

from .models import Proveedor


class ProveedorListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = [
            'id',
            'tipo_identificacion',
            'identificacion',
            'nombre',
            'email',
            'telefono',
            'contacto',
            'is_active',
            'created_at',
        ]


class ProveedorSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(required=False, default=True)

    class Meta:
        model = Proveedor
        fields = [
            'id',
            'tipo_identificacion',
            'identificacion',
            'nombre',
            'email',
            'telefono',
            'direccion',
            'contacto',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            raise serializers.ValidationError({
                "empresa": "No se pudo determinar la empresa activa del usuario en sesión."
            })

        validated_data['empresa_id'] = empresa_id

        # Normaliza la identificación para búsquedas y almacenamiento.
        identificacion = (validated_data.get('identificacion') or '').strip().upper()
        validated_data['identificacion'] = identificacion

        # Un proveedor ACTIVO con la misma identificación en esta empresa
        # impide crear un duplicado (misma regla que Clientes).
        existe_activo = Proveedor.objects.filter(
            empresa_id=empresa_id,
            identificacion=identificacion,
            is_active=True,
        ).first()
        if existe_activo:
            raise serializers.ValidationError({
                "identificacion": [
                    "Ya existe un proveedor activo con esta identificación."
                ]
            })

        # Si existe un proveedor DESACTIVADO con la misma identificación en
        # ESTA empresa, se informa al usuario con el id para que decida
        # activarlo (el frontend hace PATCH con is_active=True).
        existe_inactivo = Proveedor.objects.filter(
            empresa_id=empresa_id,
            identificacion=identificacion,
            is_active=False,
        ).first()
        if existe_inactivo:
            raise serializers.ValidationError({
                'inactive_duplicate': {
                    'id': existe_inactivo.id,
                    'identificacion': identificacion,
                    'nombre': existe_inactivo.nombre,
                    'message': (
                        f"Ya existe un proveedor desactivado con la identificación "
                        f"{identificacion}. Puedes activarlo."
                    ),
                }
            })

        return super().create(validated_data)
