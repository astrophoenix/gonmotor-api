from rest_framework import serializers

from apps.authentication.utils import (
    get_contexto_desde_request,
    get_empresa_id_desde_request,
    get_taller_desde_request,
    talleres_permitidos,
)
from apps.empresas.services import resolver_taller

from .models import Gasto
from apps.core.serializers import ModelSerializerPublico


class GastoSerializer(ModelSerializerPublico):
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
        # `empresa` sale de la sesión (nunca del cliente) e `is_active` lo maneja
        # el borrado lógico.
        read_only_fields = ['id', 'empresa', 'creado_por', 'is_active', 'created_at', 'updated_at']

    def validate_taller(self, taller):
        """Un taller explícito debe ser de la empresa activa y estar permitido al usuario."""
        request = self.context.get('request')
        if taller is None or request is None:
            return taller
        contexto = get_contexto_desde_request(request)
        if not talleres_permitidos(request.user, contexto).filter(pk=taller.pk).exists():
            raise serializers.ValidationError('El taller no pertenece a tu empresa o no tienes acceso a él.')
        return taller

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id and 'empresa' not in validated_data:
                validated_data['empresa_id'] = empresa_id
            if request.user.is_authenticated:
                validated_data['creado_por'] = request.user
            # Sin taller explícito (selector oculto) se usa el taller activo
            # (`X-Taller-ID`); el primero de la empresa queda como último recurso.
            taller = validated_data.get('taller') or get_taller_desde_request(request)
            if empresa_id:
                taller_resuelto = resolver_taller(empresa_id, taller)
                if taller_resuelto is not None:
                    validated_data['taller'] = taller_resuelto
        return super().create(validated_data)
