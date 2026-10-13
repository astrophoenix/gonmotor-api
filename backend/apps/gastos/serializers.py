from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request
from apps.empresas.services import resolver_taller

from .models import Gasto


class GastoSerializer(serializers.ModelSerializer):
    categoria_display = serializers.CharField(source='get_categoria_display', read_only=True)
    taller_nombre = serializers.CharField(source='taller.nombre', read_only=True)

    class Meta:
        model = Gasto
        fields = [
            'id',
            'empresa',
            'taller',
            'taller_nombre',
            'fecha',
            'categoria',
            'categoria_display',
            'monto',
            'descripcion',
            'creado_por',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'creado_por', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id and 'empresa' not in validated_data:
                validated_data['empresa_id'] = empresa_id
            if request.user.is_authenticated:
                validated_data['creado_por'] = request.user
            taller = validated_data.get('taller')
            if empresa_id:
                taller_resuelto = resolver_taller(empresa_id, taller)
                if taller_resuelto is not None:
                    validated_data['taller'] = taller_resuelto
        return super().create(validated_data)
