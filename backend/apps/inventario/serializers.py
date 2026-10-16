from rest_framework import serializers

from apps.core.ids_publicos import id_publico
from apps.authentication.utils import get_empresa_id_desde_request

from .models import Repuesto, RepuestoImagen, Servicio
from apps.core.serializers import ImagenProcesadaField, ModelSerializerPublico


def url_imagen_absoluta(request, url):
    """Convierte una URL relativa de archivo en absoluta (basada en el Host de la API)."""
    if not url:
        return None
    if request is not None:
        return request.build_absolute_uri(url)
    return url


class RepuestoImagenSerializer(ModelSerializerPublico):
    imagen = ImagenProcesadaField(perfil='repuesto', campo_miniatura='miniatura')

    class Meta:
        model = RepuestoImagen
        fields = ['id', 'repuesto', 'imagen', 'es_principal', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        repuesto = attrs.get('repuesto') or getattr(self.instance, 'repuesto', None)
        if repuesto is None:
            return attrs

        request = self.context.get('request')
        empresa_id = get_empresa_id_desde_request(request)
        if empresa_id and repuesto.empresa_id != empresa_id:
            raise serializers.ValidationError(
                {'repuesto': 'El repuesto no pertenece a tu empresa.'}
            )

        if self.instance is None or self.instance.repuesto_id != repuesto.id:
            if repuesto.imagenes.count() >= RepuestoImagen.MAX_IMAGENES:
                raise serializers.ValidationError(
                    {'repuesto': f'Solo se permiten hasta {RepuestoImagen.MAX_IMAGENES} imágenes por repuesto.'}
                )
        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        request = self.context.get('request')
        rep['imagen'] = url_imagen_absoluta(request, rep.get('imagen'))
        return rep


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
                    'id': id_publico(inactivo),
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


class RepuestoSerializer(_CatalogoUnicoMixin, ModelSerializerPublico):
    stock_bajo = serializers.BooleanField(read_only=True)
    imagenes = RepuestoImagenSerializer(many=True, read_only=True)
    imagen_principal = serializers.SerializerMethodField()

    class Meta:
        model = Repuesto
        fields = [
            'id',
            'empresa',
            'codigo',
            'nombre',
            'descripcion',
            'categoria',
            'marca',
            'numero_parte',
            'unidad_medida',
            'costo_referencial',
            'precio_venta',
            'iva_porcentaje_defecto',
            'stock_actual',
            'stock_minimo',
            'stock_bajo',
            'ubicacion',
            'proveedor',
            'contifico_producto_id',
            'contifico_cuenta_contable',
            'imagenes',
            'imagen_principal',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'stock_bajo', 'imagenes', 'imagen_principal', 'created_at', 'updated_at']

    def get_imagen_principal(self, obj):
        # El ordering del modelo deja primero la principal (si existe), así que
        # el primer elemento del prefetch ya es la imagen representativa.
        principal = next(iter(obj.imagenes.all()), None)
        if not principal or not principal.imagen:
            return None
        request = self.context.get('request')
        return url_imagen_absoluta(request, principal.imagen.url)

    def validate(self, attrs):
        self._validar_codigo_unico(Repuesto)
        return attrs


class ServicioSerializer(_CatalogoUnicoMixin, ModelSerializerPublico):
    class Meta:
        model = Servicio
        fields = [
            'id',
            'empresa',
            'codigo',
            'nombre',
            'descripcion',
            'categoria',
            'tiempo_estimado_minutos',
            'tareas_estandar',
            'precio_referencial',
            'iva_porcentaje_defecto',
            'contifico_producto_id',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def validate(self, attrs):
        self._validar_codigo_unico(Servicio)
        return attrs