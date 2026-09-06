from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request

from .models import Repuesto, Servicio


class _CatalogoUnicoMixin:
    """Valida que el código sea único por empresa distinguiendo activos/inactivos.

    - Si el código ya existe ACTIVO  -> error de validación en `codigo`.
    - Si el código existe INACTIVO  -> devuelve `inactive_duplicate` con el id
      para que el frontend ofrezca reactivarlo (mismo estándar que vehículos).
    """

    def _get_empresa_id(self):
        request = self.context.get('request')
        if not request:
            return getattr(self.instance, 'empresa_id', None)
        return get_empresa_id_desde_request(request)

    def _validar_codigo_unico(self, modelo):
        codigo = (self.initial_data.get('codigo') or '').strip().upper()
        if not codigo:
            return

        empresa_id = self._get_empresa_id()
        if not empresa_id:
            return

        activo = modelo.objects.filter(
            empresa_id=empresa_id,
            codigo=codigo,
            is_active=True,
        ).exclude(pk=self.instance.pk if self.instance else None).first()
        if activo:
            raise serializers.ValidationError({
                'codigo': [f'Ya existe un registro activo con el código "{codigo}".']
            })

        inactivo = modelo.objects.filter(
            empresa_id=empresa_id,
            codigo=codigo,
            is_active=False,
        ).exclude(pk=self.instance.pk if self.instance else None).first()
        if inactivo:
            raise serializers.ValidationError({
                'inactive_duplicate': {
                    'id': inactivo.pk,
                    'codigo': codigo,
                    'nombre': getattr(inactivo, 'nombre', ''),
                    'message': (
                        f'Ya existe un registro desactivado con el código {codigo} '
                        f'({getattr(inactivo, "nombre", "")}). Puedes reactivarlo.'
                    ),
                }
            })

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)


class RepuestoSerializer(_CatalogoUnicoMixin, serializers.ModelSerializer):
    stock_bajo = serializers.BooleanField(read_only=True)

    class Meta:
        model = Repuesto
        fields = [
            'id',
            'empresa',
            'sucursal',
            'codigo',
            'nombre',
            'descripcion',
            'categoria',
            'marca',
            'numero_parte',
            'unidad_medida',
            'costo_referencial',
            'precio_venta',
            'aplica_iva',
            'stock_actual',
            'stock_minimo',
            'stock_bajo',
            'ubicacion',
            'proveedor',
            'contifico_producto_id',
            'contifico_cuenta_contable',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'stock_bajo', 'created_at', 'updated_at']

    def validate(self, attrs):
        self._validar_codigo_unico(Repuesto)
        return attrs


class ServicioSerializer(_CatalogoUnicoMixin, serializers.ModelSerializer):
    class Meta:
        model = Servicio
        fields = [
            'id',
            'empresa',
            'sucursal',
            'codigo',
            'nombre',
            'descripcion',
            'categoria',
            'tiempo_estimado_minutos',
            'tareas_estandar',
            'precio_referencial',
            'contifico_producto_id',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def validate(self, attrs):
        self._validar_codigo_unico(Servicio)
        return attrs