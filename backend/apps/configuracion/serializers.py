import copy

from rest_framework import serializers

from apps.empresas.models import Empresa, Taller
from apps.empresas.services import TIPOS_VALIDOS, ultimo_numero_emitido

_ETIQUETAS_SECUENCIA = {
    'recepcion': 'Recepción',
    'inspeccion': 'Inspección',
    'cotizacion': 'Cotización',
    'ot': 'Orden de Trabajo',
}


class TallerConfigSerializer(serializers.ModelSerializer):
    """Serializa las sucursales / talleres de la empresa en sesión.

    Incluye la numeración de documentos: prefijos, contadores y dígitos
    configurables para recepciones, inspecciones, cotizaciones y órdenes de
    trabajo, el último número realmente emitido y un ejemplo del formato.
    """

    ultimo_recepcion = serializers.SerializerMethodField()
    ultimo_inspeccion = serializers.SerializerMethodField()
    ultimo_cotizacion = serializers.SerializerMethodField()
    ultimo_ot = serializers.SerializerMethodField()
    ejemplo_recepcion = serializers.SerializerMethodField()
    ejemplo_inspeccion = serializers.SerializerMethodField()
    ejemplo_cotizacion = serializers.SerializerMethodField()
    ejemplo_ot = serializers.SerializerMethodField()

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
            'prefijo_recepcion',
            'siguiente_recepcion',
            'digitos_recepcion',
            'prefijo_inspeccion',
            'siguiente_inspeccion',
            'digitos_inspeccion',
            'prefijo_cotizacion',
            'siguiente_cotizacion',
            'digitos_cotizacion',
            'prefijo_ot',
            'siguiente_ot',
            'digitos_ot',
            'ultimo_recepcion',
            'ultimo_inspeccion',
            'ultimo_cotizacion',
            'ultimo_ot',
            'ejemplo_recepcion',
            'ejemplo_inspeccion',
            'ejemplo_cotizacion',
            'ejemplo_ot',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'empresa',
            'created_at',
            'updated_at',
        ]

    def validate_nombre(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('El nombre del taller es obligatorio.')
        return value

    def validate_direccion(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('La dirección es obligatoria.')
        return value

    def validate_codigo_sucursal(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('El código del taller es obligatorio.')
        return value[:10]

    def validate_ciudad(self, value):
        return (value or '').strip()[:100]

    def validate_telefono(self, value):
        return (value or '').replace(' ', '').strip()[:20]

    def _validar_prefijo(self, prefijo):
        prefijo = (prefijo or '').strip()
        if not prefijo:
            raise serializers.ValidationError('El prefijo es obligatorio.')
        return prefijo[:10]

    def _validar_entero(self, value, minimo, maximo, etiqueta):
        try:
            numero = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError(f'{etiqueta} debe ser un número entero.')
        if numero < minimo:
            raise serializers.ValidationError(f'{etiqueta} debe ser al menos {minimo}.')
        if maximo is not None and numero > maximo:
            raise serializers.ValidationError(f'{etiqueta} no puede superar {maximo}.')
        return numero

    def validate_prefijo_recepcion(self, value):
        return self._validar_prefijo(value)

    def validate_prefijo_inspeccion(self, value):
        return self._validar_prefijo(value)

    def validate_prefijo_cotizacion(self, value):
        return self._validar_prefijo(value)

    def validate_prefijo_ot(self, value):
        return self._validar_prefijo(value)

    def validate_siguiente_recepcion(self, value):
        return self._validar_entero(value, 1, None, 'El siguiente número de Recepción')

    def validate_siguiente_inspeccion(self, value):
        return self._validar_entero(value, 1, None, 'El siguiente número de Inspección')

    def validate_siguiente_cotizacion(self, value):
        return self._validar_entero(value, 1, None, 'El siguiente número de Cotización')

    def validate_siguiente_ot(self, value):
        return self._validar_entero(value, 1, None, 'El siguiente número de OT')

    def validate_digitos_recepcion(self, value):
        return self._validar_entero(value, 2, 10, 'Los dígitos de Recepción')

    def validate_digitos_inspeccion(self, value):
        return self._validar_entero(value, 2, 10, 'Los dígitos de Inspección')

    def validate_digitos_cotizacion(self, value):
        return self._validar_entero(value, 2, 10, 'Los dígitos de Cotización')

    def validate_digitos_ot(self, value):
        return self._validar_entero(value, 2, 10, 'Los dígitos de OT')

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
                    'codigo_sucursal': 'El código del taller ya está en uso por un taller activo.'
                })

        if self.instance:
            taller_ref = copy.copy(self.instance)
            for tipo in TIPOS_VALIDOS:
                prefijo = attrs.get(f'prefijo_{tipo}')
                if prefijo is not None:
                    setattr(taller_ref, f'prefijo_{tipo}', prefijo)
            for tipo in TIPOS_VALIDOS:
                siguiente = attrs.get(f'siguiente_{tipo}')
                if siguiente is None:
                    siguiente = getattr(self.instance, f'siguiente_{tipo}')
                ultimo = ultimo_numero_emitido(taller_ref, tipo)
                if int(siguiente) <= ultimo:
                    raise serializers.ValidationError({
                        f'siguiente_{tipo}': (
                            f'El siguiente número de {_ETIQUETAS_SECUENCIA[tipo]} debe ser '
                            f'mayor que el último emitido ({ultimo}).'
                        )
                    })
        return attrs

    def get_ultimo_recepcion(self, obj):
        return ultimo_numero_emitido(obj, 'recepcion')

    def get_ultimo_inspeccion(self, obj):
        return ultimo_numero_emitido(obj, 'inspeccion')

    def get_ultimo_cotizacion(self, obj):
        return ultimo_numero_emitido(obj, 'cotizacion')

    def get_ultimo_ot(self, obj):
        return ultimo_numero_emitido(obj, 'ot')

    def _get_ejemplo(self, obj, tipo):
        prefijo = (getattr(obj, f'prefijo_{tipo}') or '').strip()
        siguiente = max(int(getattr(obj, f'siguiente_{tipo}') or 1), 1)
        digitos = max(int(getattr(obj, f'digitos_{tipo}') or 5), 1)
        return f'{prefijo}{siguiente:0{digitos}d}'

    def get_ejemplo_recepcion(self, obj):
        return self._get_ejemplo(obj, 'recepcion')

    def get_ejemplo_inspeccion(self, obj):
        return self._get_ejemplo(obj, 'inspeccion')

    def get_ejemplo_cotizacion(self, obj):
        return self._get_ejemplo(obj, 'cotizacion')

    def get_ejemplo_ot(self, obj):
        return self._get_ejemplo(obj, 'ot')


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