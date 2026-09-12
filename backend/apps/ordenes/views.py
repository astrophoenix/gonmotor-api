from django.utils import timezone
from rest_framework import filters, permissions, serializers, viewsets
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService

from .models import (
    DetalleRepuestoInspeccion,
    DetalleServicioInspeccion,
    FotoInspeccion,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)
from .serializers import (
    DetalleRepuestoInspeccionSerializer,
    DetalleServicioInspeccionSerializer,
    FotoInspeccionSerializer,
    InspeccionVehiculoSerializer,
    OrdenTrabajoSerializer,
    RecepcionVehiculoSerializer,
)


def _check_inspeccion_editable(inspeccion):
    if inspeccion is not None and inspeccion.estado == 'FINALIZADA':
        raise serializers.ValidationError(
            'La inspección está finalizada; reábrela para poder modificar sus detalles.'
        )


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = OrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['numero_orden', 'cliente__nombre', 'vehiculo__placa']
    ordering_fields = ['numero_orden', 'created_at', 'total']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return OrdenTrabajo.objects.none()
        return (
            OrdenTrabajo.objects.filter(empresa_id=empresa_id)
            .select_related(
                'cliente',
                'vehiculo',
                'sucursal',
                'asesor',
                'mecanico_principal',
                'cotizacion_origen',
                'inspeccion',
            )
            .prefetch_related('servicios', 'repuestos', 'recepciones')
        )


class RecepcionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = RecepcionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['vehiculo__placa', 'vehiculo__marca', 'cliente__nombre', 'cliente__identificacion', 'orden_trabajo__numero_orden']
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return RecepcionVehiculo.objects.none()
        return (
            RecepcionVehiculo.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'orden_trabajo', 'sucursal')
            .prefetch_related('inspecciones', 'cotizaciones_generadas')
        )

    def _sincronizar_kilometraje_vehiculo(self, instance):
        if instance.estado == 'NO_ACEPTADA':
            return
        if not instance.vehiculo_id or not instance.kilometraje_ingreso:
            return
        if instance.vehiculo.kilometraje_actual != instance.kilometraje_ingreso:
            instance.vehiculo.kilometraje_actual = instance.kilometraje_ingreso
            instance.vehiculo.save(update_fields=['kilometraje_actual', 'updated_at'])

    def perform_create(self, serializer):
        instance = serializer.save()
        self._sincronizar_kilometraje_vehiculo(instance)
        if instance.firma_receptor and not instance.fecha_firma_receptor:
            instance.fecha_firma_receptor = timezone.now()
            instance.save(update_fields=['fecha_firma_receptor'])
        if instance.firma_cliente and not instance.aceptacion_condiciones:
            instance.fecha_firma_cliente = timezone.now()
            instance.aceptacion_condiciones = True
            instance.estado = 'ACEPTADA'
            instance.save(update_fields=['fecha_firma_cliente', 'aceptacion_condiciones', 'estado'])

    def perform_update(self, serializer):
        instance = serializer.save()
        self._sincronizar_kilometraje_vehiculo(instance)
        if instance.firma_receptor and not instance.fecha_firma_receptor:
            instance.fecha_firma_receptor = timezone.now()
            instance.save(update_fields=['fecha_firma_receptor'])
        elif not instance.firma_receptor and instance.fecha_firma_receptor:
            instance.fecha_firma_receptor = None
            instance.save(update_fields=['fecha_firma_receptor'])
        if instance.firma_cliente and not instance.aceptacion_condiciones:
            instance.fecha_firma_cliente = timezone.now()
            instance.aceptacion_condiciones = True
            instance.estado = 'ACEPTADA'
            instance.save(update_fields=['fecha_firma_cliente', 'aceptacion_condiciones', 'estado'])
        elif not instance.firma_cliente and instance.aceptacion_condiciones:
            instance.fecha_firma_cliente = None
            instance.aceptacion_condiciones = False
            instance.estado = 'PENDIENTE'
            instance.save(update_fields=['fecha_firma_cliente', 'aceptacion_condiciones', 'estado'])


class InspeccionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = InspeccionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['recepcion__vehiculo__placa', 'recepcion__cliente__nombre']
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return InspeccionVehiculo.objects.none()
        return InspeccionVehiculo.objects.filter(empresa_id=empresa_id).prefetch_related(
            'servicios_detectados', 'repuestos_sugeridos', 'fotos'
        )

    @staticmethod
    def _check_editable(inspeccion):
        if inspeccion.orden_trabajo_id:
            raise serializers.ValidationError(
                'La inspección ya se convirtió en orden de trabajo y no puede eliminarse.'
            )

    def destroy(self, request, *args, **kwargs):
        self._check_editable(self.get_object())
        return super().destroy(request, *args, **kwargs)


class DetalleServicioInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleServicioInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleServicioInspeccion.objects.none()
        queryset = DetalleServicioInspeccion.objects.filter(
            inspeccion__empresa_id=empresa_id
        ).select_related('inspeccion', 'servicio')
        inspeccion_id = self.request.query_params.get('inspeccion')
        if inspeccion_id:
            queryset = queryset.filter(inspeccion_id=inspeccion_id)
        return queryset

    def perform_create(self, serializer):
        _check_inspeccion_editable(serializer.validated_data.get('inspeccion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_inspeccion_editable(self.get_object().inspeccion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_inspeccion_editable(instance.inspeccion)
        super().perform_destroy(instance)


class DetalleRepuestoInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleRepuestoInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleRepuestoInspeccion.objects.none()
        queryset = DetalleRepuestoInspeccion.objects.filter(
            inspeccion__empresa_id=empresa_id
        ).select_related('inspeccion', 'repuesto')
        inspeccion_id = self.request.query_params.get('inspeccion')
        if inspeccion_id:
            queryset = queryset.filter(inspeccion_id=inspeccion_id)
        return queryset

    def perform_create(self, serializer):
        _check_inspeccion_editable(serializer.validated_data.get('inspeccion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_inspeccion_editable(self.get_object().inspeccion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_inspeccion_editable(instance.inspeccion)
        super().perform_destroy(instance)


class FotoInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = FotoInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering = ['created_at', 'id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return FotoInspeccion.objects.none()
        queryset = FotoInspeccion.objects.filter(
            inspeccion__empresa_id=empresa_id
        ).select_related('inspeccion')
        inspeccion_id = self.request.query_params.get('inspeccion')
        if inspeccion_id:
            queryset = queryset.filter(inspeccion_id=inspeccion_id)
        return queryset

    def perform_create(self, serializer):
        _check_inspeccion_editable(serializer.validated_data.get('inspeccion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_inspeccion_editable(self.get_object().inspeccion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_inspeccion_editable(instance.inspeccion)
        super().perform_destroy(instance)


class RecepcionPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = RecepcionVehiculo.objects.filter(
                orden_trabajo__empresa=empresa_id,
                is_active=True
            ).select_related('orden_trabajo', 'orden_trabajo__vehiculo', 'orden_trabajo__cliente').order_by('-created_at')

            def subtitle_builder(qs):
                if not qs.exists():
                    return None
                return f'Generado: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}'

            def row_builder(recepcion, cell_style):
                orden = recepcion.orden_trabajo
                vehiculo = orden.vehiculo if orden else None
                cliente = orden.cliente if orden else None
                return [
                    Paragraph(f'#{recepcion.id}', cell_style),
                    Paragraph(vehiculo.placa if vehiculo else '-', cell_style),
                    Paragraph(cliente.nombre if cliente else '-', cell_style),
                    Paragraph('Sí' if recepcion.ingreso_en_grua else 'No', cell_style),
                    Paragraph(timezone.localtime(recepcion.created_at).strftime('%d/%m/%Y %H:%M'), cell_style),
                ]

            empresa = None
            taller = None
            if queryset.exists():
                primera = queryset.first()
                if primera.orden_trabajo:
                    empresa = primera.orden_trabajo.empresa
                    taller = primera.orden_trabajo.empresa.talleres.first() if hasattr(primera.orden_trabajo.empresa, 'talleres') else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            config = PdfExportConfig(
                title='Listado de Recepciones',
                filename='listado_recepciones.pdf',
                headers=[
                    ('ID', 0.8),
                    ('Placa', 1.4),
                    ('Cliente', 2.0),
                    ('Grúa', 0.8),
                    ('Fecha ingreso', 1.6),
                ],
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                subtitle_builder=subtitle_builder,
                row_builder=row_builder,
            )

            service = PdfExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500
            )


class RecepcionExcelExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = RecepcionVehiculo.objects.filter(
                orden_trabajo__empresa=empresa_id,
                is_active=True
            ).select_related('orden_trabajo', 'orden_trabajo__vehiculo', 'orden_trabajo__cliente').order_by('-created_at')

            empresa = None
            taller = None
            if queryset.exists():
                primera = queryset.first()
                if primera.orden_trabajo:
                    empresa = primera.orden_trabajo.empresa
                    taller = primera.orden_trabajo.empresa.talleres.first() if hasattr(primera.orden_trabajo.empresa, 'talleres') else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def row_builder(recepcion):
                orden = recepcion.orden_trabajo
                vehiculo = orden.vehiculo if orden else None
                cliente = orden.cliente if orden else None
                return [
                    str(recepcion.id),
                    vehiculo.placa if vehiculo else '-',
                    cliente.nombre if cliente else '-',
                    'Sí' if recepcion.ingreso_en_grua else 'No',
                    timezone.localtime(recepcion.created_at).strftime('%d/%m/%Y %H:%M'),
                ]

            config = ExcelExportConfig(
                title='Listado de Recepciones',
                filename='listado_recepciones.xlsx',
                headers=[
                    ('ID', 0.8),
                    ('Placa', 1.4),
                    ('Cliente', 2.0),
                    ('Grúa', 0.8),
                    ('Fecha ingreso', 1.6),
                ],
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                row_builder=row_builder,
            )

            service = ExcelExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el Excel en este momento. ({str(e)})'},
                status=500
            )
