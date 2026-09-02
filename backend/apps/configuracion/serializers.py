from rest_framework import serializers
from apps.empresas.models import Empresa


class EmpresaConfigSerializer(serializers.ModelSerializer):
    """Serializa los datos fiscales y corporativos de la empresa del taller."""

    class Meta:
        model = Empresa
        fields = [
            'id',
            'nombre_comercial',
            'razon_social',
            'ruc',
            'email_contacto',
            'telefono',
            'logo',
            'is_active',
        ]
        read_only_fields = ['id', 'is_active']

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
        return value

    def validate_email_contacto(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('El correo de contacto es obligatorio.')
        return value