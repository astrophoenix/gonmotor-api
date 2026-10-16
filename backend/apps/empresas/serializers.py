from rest_framework import serializers
from .models import Empresa
from apps.core.serializers import ModelSerializerPublico


class EmpresaSerializer(ModelSerializerPublico):
    """Alta y edición de empresas (tenants) por el administrador del SaaS."""

    class Meta:
        model = Empresa
        fields = [
            'id',
            'nombre_comercial',
            'razon_social',
            'ruc',
            'telefono',
            'email_contacto',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']
        # La unicidad del RUC (solo entre empresas no eliminadas) se valida en
        # `validate_ruc` con un mensaje en español.
        validators = []

    def validate_nombre_comercial(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('El nombre comercial es obligatorio.')
        return value

    def validate_razon_social(self, value):
        return (value or '').strip()

    def validate_ruc(self, value):
        value = (value or '').replace(' ', '').strip()
        if len(value) != 13 or not value.isdigit():
            raise serializers.ValidationError(
                'El RUC debe contener exactamente 13 dígitos numéricos.'
            )
        duplicadas = Empresa.objects.filter(ruc=value)
        if self.instance is not None:
            duplicadas = duplicadas.exclude(pk=self.instance.pk)
        if duplicadas.exists():
            raise serializers.ValidationError('Ya existe una empresa registrada con este RUC.')
        return value

    def validate_telefono(self, value):
        value = (value or '').strip()
        if value and not value.replace('+', '', 1).isdigit():
            raise serializers.ValidationError('El teléfono solo puede contener números.')
        return value

    def validate_email_contacto(self, value):
        value = (value or '').strip().lower()
        if not value:
            raise serializers.ValidationError('El correo electrónico es obligatorio.')
        return value
