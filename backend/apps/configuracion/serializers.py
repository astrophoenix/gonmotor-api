from rest_framework import serializers
from apps.empresas.models import Empresa, Taller


class TallerConfigSerializer(serializers.ModelSerializer):
    """Serializa las sucursales / talleres de la empresa en sesión."""

    class Meta:
        model = Taller
        fields = [
            'id',
            'empresa',
            'nombre',
            'codigo_sucursal',
            'ciudad',
            'direccion',
            'telefono',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def validate_nombre(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('El nombre de la sucursal es obligatorio.')
        return value

    def validate_direccion(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('La dirección es obligatoria.')
        return value

    def validate_codigo_sucursal(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('El código de sucursal es obligatorio.')
        return value[:10]

    def validate_ciudad(self, value):
        return (value or '').strip()[:100]

    def validate_telefono(self, value):
        return (value or '').replace(' ', '').strip()[:20]

    def validate(self, attrs):
        empresa_id = attrs.get('empresa_id')
        codigo = attrs.get('codigo_sucursal')
        activo = attrs.get('is_active', self.instance.is_active if self.instance else True)

        if empresa_id and codigo and activo:
            qs = Taller.objects.filter(
                empresa_id=empresa_id,
                codigo_sucursal=codigo,
                is_active=True,
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    'codigo_sucursal': 'El código de sucursal ya está en uso por una sucursal activa.'
                })
        return attrs


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