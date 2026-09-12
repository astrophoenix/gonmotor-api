from rest_framework import serializers

from apps.authentication.models import UsuarioEmpresa
from apps.authentication.utils import get_empresa_id_desde_request
from apps.empresas.services import generar_codigo_secuencial, resolver_taller

from .models import (
    DetalleRepuestoInspeccion,
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioInspeccion,
    DetalleServicioOrdenTrabajo,
    FotoRecepcion,
    FotoInspeccion,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)


class DetalleServicioOrdenTrabajoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleServicioOrdenTrabajo
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class DetalleRepuestoOrdenTrabajoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleRepuestoOrdenTrabajo
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class DetalleServicioInspeccionSerializer(serializers.ModelSerializer):
    servicio_codigo = serializers.SerializerMethodField()
    servicio_nombre = serializers.SerializerMethodField()
    prioridad_display = serializers.CharField(read_only=True, source='get_prioridad_display')

    class Meta:
        model = DetalleServicioInspeccion
        fields = [
            'id',
            'inspeccion',
            'servicio',
            'servicio_codigo',
            'servicio_nombre',
            'descripcion',
            'horas_estimadas',
            'precio_referencial',
            'es_sugerido',
            'prioridad',
            'prioridad_display',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'servicio_codigo', 'servicio_nombre', 'prioridad_display', 'created_at', 'updated_at']

    def get_servicio_codigo(self, obj):
        return obj.servicio.codigo if obj.servicio_id else None

    def get_servicio_nombre(self, obj):
        return obj.servicio.nombre if obj.servicio_id else None

    def validate(self, attrs):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            inspeccion = attrs.get('inspeccion') or (self.instance.inspeccion if self.instance else None)
            servicio = attrs.get('servicio', self.instance.servicio if self.instance else None)
            if inspeccion is not None and inspeccion.empresa_id != empresa_id:
                raise serializers.ValidationError({'inspeccion': 'La inspección no pertenece a tu empresa.'})
            if servicio is not None and servicio.empresa_id != empresa_id:
                raise serializers.ValidationError({'servicio': 'El servicio no pertenece a tu empresa.'})
        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['prioridad_display'] = instance.get_prioridad_display()
        return rep


class DetalleRepuestoInspeccionSerializer(serializers.ModelSerializer):
    repuesto_codigo = serializers.SerializerMethodField()
    repuesto_nombre = serializers.SerializerMethodField()
    prioridad_display = serializers.CharField(read_only=True, source='get_prioridad_display')

    class Meta:
        model = DetalleRepuestoInspeccion
        fields = [
            'id',
            'inspeccion',
            'repuesto',
            'repuesto_codigo',
            'repuesto_nombre',
            'descripcion',
            'cantidad',
            'precio_referencial',
            'es_sugerido',
            'prioridad',
            'prioridad_display',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'repuesto_codigo', 'repuesto_nombre', 'prioridad_display', 'created_at', 'updated_at']

    def get_repuesto_codigo(self, obj):
        return obj.repuesto.codigo if obj.repuesto_id else None

    def get_repuesto_nombre(self, obj):
        return obj.repuesto.nombre if obj.repuesto_id else None

    def validate(self, attrs):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            inspeccion = attrs.get('inspeccion') or (self.instance.inspeccion if self.instance else None)
            repuesto = attrs.get('repuesto', self.instance.repuesto if self.instance else None)
            if inspeccion is not None and inspeccion.empresa_id != empresa_id:
                raise serializers.ValidationError({'inspeccion': 'La inspección no pertenece a tu empresa.'})
            if repuesto is not None and repuesto.empresa_id != empresa_id:
                raise serializers.ValidationError({'repuesto': 'El repuesto no pertenece a tu empresa.'})
        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['prioridad_display'] = instance.get_prioridad_display()
        return rep


class FotoInspeccionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoInspeccion
        fields = ['id', 'inspeccion', 'imagen', 'descripcion', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_imagen(self, imagen):
        if imagen.size > 5 * 1024 * 1024:
            raise serializers.ValidationError('La imagen supera el tamaño máximo de 5 MB.')
        if imagen.content_type not in ['image/jpeg', 'image/png', 'image/webp']:
            raise serializers.ValidationError('Formato no permitido. Solo JPG, PNG o WebP.')
        return imagen

    def validate(self, attrs):
        inspeccion = attrs.get('inspeccion')
        if inspeccion is None:
            return attrs

        request = self.context.get('request')
        empresa_id = get_empresa_id_desde_request(request)
        if empresa_id and inspeccion.empresa_id != empresa_id:
            raise serializers.ValidationError({'inspeccion': 'La inspección no pertenece a tu empresa.'})

        if self.instance is None or self.instance.inspeccion_id != inspeccion.id:
            if inspeccion.fotos.count() >= FotoInspeccion.MAX_FOTOS:
                raise serializers.ValidationError(
                    {'inspeccion': f'Solo se permiten hasta {FotoInspeccion.MAX_FOTOS} fotos por inspección.'}
                )
        return attrs


class InspeccionVehiculoSerializer(serializers.ModelSerializer):
    servicios_detectados = DetalleServicioInspeccionSerializer(many=True, read_only=True)
    repuestos_sugeridos = DetalleRepuestoInspeccionSerializer(many=True, read_only=True)
    fotos = FotoInspeccionSerializer(many=True, read_only=True)

    TRANSICIONES_PERMITIDAS = {
        'PENDIENTE': {'PENDIENTE', 'EN_PROCESO'},
        'EN_PROCESO': {'PENDIENTE', 'EN_PROCESO', 'FINALIZADA'},
        'FINALIZADA': {'FINALIZADA', 'EN_PROCESO'},
    }

    class Meta:
        model = InspeccionVehiculo
        fields = [
            'id',
            'empresa',
            'sucursal',
            'orden_trabajo',
            'recepcion',
            'numero_inspeccion',
            'tipo_inspeccion',
            'estado',
            'motivo_ingreso',
            'codigos_dtc',
            'diagnostico_tecnico',
            'recomendaciones',
            'testigo_check_engine',
            'testigo_abs',
            'testigo_airbag',
            'testigo_bateria',
            'testigo_aceite',
            'testigo_temperatura',
            'testigo_presion_llantas',
            'testigo_desempanado',
            'testigo_limpiaparabrisas',
            'testigo_luces_largas',
            'testigo_combustible_bajo',
            'testigo_antiniebla_traseras',
            'testigo_esp',
            'testigo_bujias_precalentamiento',
            'testigo_pedal_freno',
            'testigo_luces_emergencia',
            'testigo_puerta_maletero',
            'testigo_cinturon',
            'testigo_freno_estacionamiento',
            'testigo_frenos_fallo',
            'otros_testigos_observaciones',
            'servicios_detectados',
            'repuestos_sugeridos',
            'fotos',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'numero_inspeccion', 'is_active', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            sucursal = validated_data.get('sucursal')
            recepcion = validated_data.get('recepcion')
            if sucursal is None and recepcion is not None and recepcion.sucursal_id:
                sucursal = recepcion.sucursal
            taller = resolver_taller(empresa_id, sucursal)
            if taller is not None:
                validated_data.setdefault('sucursal', taller)
                validated_data['numero_inspeccion'] = generar_codigo_secuencial(taller, 'inspeccion')
        return super().create(validated_data)

    def validate(self, attrs):
        recepcion = attrs.get('recepcion')
        if recepcion is not None:
            inspecciones = recepcion.inspecciones.all()
            if self.instance:
                inspecciones = inspecciones.exclude(pk=self.instance.pk)
            if inspecciones.exists():
                raise serializers.ValidationError(
                    {'recepcion': 'Esta recepción ya tiene una inspección registrada.'}
                )

        estado = attrs.get('estado')
        if self.instance and estado and estado != self.instance.estado:
            permitidas = self.TRANSICIONES_PERMITIDAS.get(self.instance.estado, set())
            if estado not in permitidas:
                raise serializers.ValidationError(
                    {'estado': 'No se permite la transición de estado solicitada.'}
                )

        if self.instance and self.instance.estado == 'FINALIZADA':
            es_reapertura = estado == 'EN_PROCESO' and set(attrs.keys()) <= {'estado'}
            if not es_reapertura:
                raise serializers.ValidationError(
                    {'detail': 'La inspección está finalizada; reábrela para poder modificarla.'}
                )
        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['tipo_inspeccion_display'] = instance.get_tipo_inspeccion_display()
        rep['estado_display'] = instance.get_estado_display()
        rep['tiene_orden_trabajo'] = instance.orden_trabajo_id is not None
        rep['orden_trabajo_numero'] = (
            instance.orden_trabajo.numero_orden if instance.orden_trabajo_id else None
        )
        from apps.cotizaciones.models import Cotizacion

        rep['tiene_cotizacion_activa'] = instance.cotizaciones_generadas.filter(
            estado__in=[Cotizacion.EstadoCotizacion.BORRADOR, Cotizacion.EstadoCotizacion.ENVIADA]
        ).exists()
        cotizacion_activa = instance.cotizaciones_generadas.filter(
            estado__in=[Cotizacion.EstadoCotizacion.BORRADOR, Cotizacion.EstadoCotizacion.ENVIADA]
        ).only('id').first()
        rep['cotizacion_activa_id'] = cotizacion_activa.id if cotizacion_activa else None
        if instance.recepcion_id:
            rec = instance.recepcion
            rv = rec.vehiculo if rec.vehiculo_id else None
            rc = rec.cliente if rec.cliente_id else None
            rep['recepcion'] = {
                'id': rec.id,
                'numero_recepcion': rec.numero_recepcion,
                'vehiculo': {
                    'id': rv.id,
                    'placa': rv.placa,
                    'marca': rv.marca,
                    'modelo': rv.modelo,
                    'color': rv.color,
                } if rv else None,
                'cliente': {
                    'id': rc.id,
                    'nombre': rc.nombre,
                    'identificacion': rc.identificacion,
                    'telefono': rc.telefono,
                    'email': rc.email,
                } if rc else None,
                'placa': rv.placa if rv else None,
                'marca': rv.marca if rv else None,
                'modelo': rv.modelo if rv else None,
                'cliente_nombre': rc.nombre if rc else None,
                'motivo_ingreso': rec.motivo_ingreso,
                'created_at': rec.created_at.isoformat() if rec.created_at else None,
            }
        return rep


class FotoRecepcionSerializer(serializers.ModelSerializer):
    tipo_vista_display = serializers.CharField(source='get_tipo_vista_display', read_only=True)

    class Meta:
        model = FotoRecepcion
        fields = ['id', 'tipo_vista', 'tipo_vista_display', 'imagen', 'descripcion', 'created_at']
        read_only_fields = ['id', 'tipo_vista_display', 'created_at']


class RecepcionVehiculoSerializer(serializers.ModelSerializer):
    inspecciones = InspeccionVehiculoSerializer(many=True, read_only=True)
    fotos = FotoRecepcionSerializer(many=True, read_only=True)
    cotizaciones_generadas = serializers.SerializerMethodField()
    orden_trabajo_numero = serializers.SerializerMethodField()

    class Meta:
        model = RecepcionVehiculo
        fields = '__all__'
        read_only_fields = [
            'id',
            'numero_recepcion',
            'fecha_firma_receptor',
            'fecha_firma_cliente',
            'aceptacion_condiciones',
        ]

    def get_cotizaciones_generadas(self, instance):
        return [
            {'id': cotizacion.id, 'numero_cotizacion': cotizacion.numero_cotizacion}
            for cotizacion in instance.cotizaciones_generadas.order_by('created_at')
        ]

    def get_orden_trabajo_numero(self, instance):
        if instance.orden_trabajo_id:
            return instance.orden_trabajo.numero_orden
        return None

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['estado_display'] = instance.get_estado_display()
        if instance.vehiculo_id:
            rep['vehiculo'] = {
                'id': instance.vehiculo_id,
                'placa': instance.vehiculo.placa,
                'marca': instance.vehiculo.marca,
                'modelo': instance.vehiculo.modelo,
                'color': instance.vehiculo.color,
                'tipo': instance.vehiculo.tipo,
                'grupo_blueprint': instance.vehiculo.grupo_blueprint,
                'kilometraje_actual': instance.vehiculo.kilometraje_actual,
            }
        if instance.cliente_id:
            rep['cliente'] = {
                'id': instance.cliente_id,
                'nombre': instance.cliente.nombre,
                'identificacion': instance.cliente.identificacion,
                'telefono': instance.cliente.telefono,
                'email': instance.cliente.email,
            }
        if instance.recibido_por_id:
            rep['recibido_por_nombre'] = instance.recibido_por.get_full_name() or instance.recibido_por.username
            usuario_empresa = UsuarioEmpresa.objects.filter(user=instance.recibido_por, empresa=instance.empresa).first()
            if usuario_empresa:
                rep['recibido_por_rol'] = usuario_empresa.rol
                rep['recibido_por_rol_display'] = usuario_empresa.get_rol_display()
            else:
                rep['recibido_por_rol'] = None
                rep['recibido_por_rol_display'] = None
        else:
            rep['recibido_por_nombre'] = None
            rep['recibido_por_rol'] = None
            rep['recibido_por_rol_display'] = None
        return rep

    def _recolectar_fotos(self, request):
        """Lee el bloque de hasta 5 fotos enviado por FormData.

        Cada archivo viaja en un campo con nombre igual al tipo de vista, p.ej.
        `foto_FRONTAL`, `foto_LATERAL_IZQ`, ... Solo se consideran las vistas
        obligatorias definidas en FotoRecepcion.VISTAS_OBLIGATORIAS.
        """
        archivos = request.FILES or {}
        fotos = {}
        for tipo in FotoRecepcion.VISTAS_OBLIGATORIAS:
            campo = f'foto_{tipo}'
            archivo = archivos.get(campo)
            if archivo:
                fotos[tipo] = archivo
        return fotos

    def _validar_bloque_fotos(self, fotos, vistas_adicionales=None):
        """Valida que estén presentes las vistas obligatorias.

        `fotos` son las recién adjuntadas; `vistas_adicionales` trae las vistas
        que la recepción ya tenía registradas (caso edición: no es necesario
        reenviar un archivo para una vista que ya existe en la BD).
        """
        presentes = set(fotos.keys())
        if vistas_adicionales:
            presentes |= {
                t.value if isinstance(t, FotoRecepcion.TipoVista) else t
                for t in vistas_adicionales
                if t
            }
        faltantes = [
            tipo for tipo in FotoRecepcion.VISTAS_OBLIGATORIAS
            if (tipo.value if isinstance(tipo, FotoRecepcion.TipoVista) else tipo) not in presentes
        ]
        if faltantes:
            nombres = ', '.join(
                dict(FotoRecepcion.TipoVista.choices)[t] for t in faltantes
            )
            raise serializers.ValidationError({
                'fotos': (
                    'Debes subir las 5 fotos obligatorias de la recepción. '
                    f'Faltan: {nombres}.'
                )
            })

    def _guardar_fotos(self, instance, fotos):
        """Crea el bloque de fotos de la recepción (reemplaza cada vista existente)."""
        for tipo, archivo in fotos.items():
            valor = tipo.value if isinstance(tipo, FotoRecepcion.TipoVista) else tipo
            instance.fotos.filter(tipo_vista=valor).delete()
            FotoRecepcion.objects.create(
                recepcion=instance,
                tipo_vista=valor,
                imagen=archivo,
            )

    def validate(self, attrs):
        if getattr(self.instance, 'estado', None) or attrs.get('estado'):
            estado = attrs.get('estado') or (self.instance.estado if self.instance else 'PENDIENTE')
        else:
            estado = 'PENDIENTE'
        if estado == 'NO_ACEPTADA':
            motivo = attrs.get('motivo_no_recepcion')
            if motivo is None and self.instance:
                motivo = self.instance.motivo_no_recepcion
            if not (motivo or '').strip():
                raise serializers.ValidationError({
                    'motivo_no_recepcion': 'El motivo de la no aceptación es obligatorio.'
                })
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        fotos = {}
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            if not validated_data.get('recibido_por') and request.user.is_authenticated:
                validated_data['recibido_por'] = request.user
            sucursal = validated_data.get('sucursal')
            taller = resolver_taller(empresa_id, sucursal)
            if taller is not None:
                validated_data.setdefault('sucursal', taller)
                validated_data['numero_recepcion'] = generar_codigo_secuencial(taller, 'recepcion')
            fotos = self._recolectar_fotos(request)
        if validated_data.get('estado') != 'NO_ACEPTADA':
            self._validar_bloque_fotos(fotos)
        instance = super().create(validated_data)
        self._guardar_fotos(instance, fotos)
        return instance

    def update(self, instance, validated_data):
        if instance.aceptacion_condiciones and instance.fecha_firma_cliente:
            raise serializers.ValidationError({
                'detail': 'No se puede editar una recepción cuyo cliente ya aceptó y firmó las condiciones de recepción.'
            })
        validated_data.pop('fecha_firma_receptor', None)
        validated_data.pop('fecha_firma_cliente', None)
        validated_data.pop('aceptacion_condiciones', None)

        request = self.context.get('request')
        fotos = {}
        if request:
            fotos = self._recolectar_fotos(request)
        if validated_data.get('estado') != 'NO_ACEPTADA':
            self._validar_bloque_fotos(
                fotos,
                vistas_adicionales=instance.fotos.values_list('tipo_vista', flat=True),
            )

        instance = super().update(instance, validated_data)
        self._guardar_fotos(instance, fotos)
        return instance


class OrdenTrabajoSerializer(serializers.ModelSerializer):
    servicios = DetalleServicioOrdenTrabajoSerializer(many=True, read_only=True)
    repuestos = DetalleRepuestoOrdenTrabajoSerializer(many=True, read_only=True)
    recepciones = RecepcionVehiculoSerializer(many=True, read_only=True)
    inspeccion = InspeccionVehiculoSerializer(read_only=True)

    class Meta:
        model = OrdenTrabajo
        fields = [
            'id',
            'empresa',
            'sucursal',
            'cliente',
            'vehiculo',
            'asesor',
            'mecanico_principal',
            'cotizacion_origen',
            'numero_orden',
            'estado',
            'prioridad',
            'tipo_trabajo',
            'observaciones_internas',
            'fecha_ingreso',
            'fecha_entrega',
            'subtotal_servicios',
            'subtotal_repuestos',
            'descuento',
            'subtotal_neto',
            'monto_iva',
            'total',
            'servicios',
            'repuestos',
            'recepciones',
            'inspeccion',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'numero_orden', 'subtotal_servicios', 'subtotal_repuestos', 'subtotal_neto', 'monto_iva', 'total', 'created_at', 'updated_at']

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['estado_display'] = instance.get_estado_display()
        rep['tipo_trabajo_display'] = instance.get_tipo_trabajo_display()
        rep['prioridad_display'] = instance.get_prioridad_display()
        rep['sucursal_nombre'] = instance.sucursal.nombre if instance.sucursal_id else None
        if instance.vehiculo_id:
            rep['vehiculo'] = {
                'id': instance.vehiculo_id,
                'placa': instance.vehiculo.placa,
                'marca': instance.vehiculo.marca,
                'modelo': instance.vehiculo.modelo,
                'color': instance.vehiculo.color,
                'tipo': instance.vehiculo.tipo,
            }
        if instance.cliente_id:
            rep['cliente'] = {
                'id': instance.cliente_id,
                'nombre': instance.cliente.nombre,
                'identificacion': instance.cliente.identificacion,
                'telefono': instance.cliente.telefono,
                'email': instance.cliente.email,
            }
        rep['asesor_nombre'] = (
            instance.asesor.get_full_name() or instance.asesor.username
        ) if instance.asesor_id else None
        rep['mecanico_nombre'] = (
            instance.mecanico_principal.get_full_name() or instance.mecanico_principal.username
        ) if instance.mecanico_principal_id else None
        rep['cotizacion_origen_numero'] = (
            instance.cotizacion_origen.numero_cotizacion if instance.cotizacion_origen_id else None
        )
        rep['recepciones'] = [
            {'id': r.id, 'numero_recepcion': r.numero_recepcion}
            for r in instance.recepciones.all()
        ]
        inspeccion = getattr(instance, 'inspeccion', None)
        rep['inspeccion'] = (
            {
                'id': inspeccion.id,
                'numero_inspeccion': inspeccion.numero_inspeccion,
                'tipo_inspeccion': inspeccion.tipo_inspeccion,
            }
            if inspeccion is not None
            else None
        )
        return rep

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            sucursal = validated_data.get('sucursal')
            taller = resolver_taller(empresa_id, sucursal)
            if taller is not None:
                validated_data.setdefault('sucursal', taller)
                validated_data['numero_orden'] = generar_codigo_secuencial(taller, 'ot')
        return super().create(validated_data)