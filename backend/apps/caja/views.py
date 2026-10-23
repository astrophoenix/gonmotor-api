from django.db import transaction
from django.http import FileResponse
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import TieneAccion, TienePermiso, TieneRecurso
from apps.authentication.utils import (
    get_contexto_desde_request,
    get_empresa_id_desde_request,
    talleres_filtro_de_request,
)
from apps.core.mixins import LookupPublicoMixin
from apps.authentication.permisos_repo import permisos_en_request

from . import services
from .models import MovimientoCaja, SesionCaja
from .pdf import exportar_cierre_pdf
from .serializers import (
    AbrirCajaSerializer,
    CerrarCajaSerializer,
    MotivoSerializer,
    MovimientoCajaSerializer,
    NuevoMovimientoSerializer,
    SesionCajaSerializer,
)


def _puede_gastos(request):
    contexto = get_contexto_desde_request(request)
    return 'modificar' in permisos_en_request(request, contexto.rol, contexto.empresa_id).get('gastos', ())


class EstadoCajaView(APIView):
    """`GET caja/estado/?taller=`: la caja del taller, su sesión abierta (con
    totales y movimientos) y el último cierre (para proponer el monto inicial)."""

    permission_classes = [permissions.IsAuthenticated, TienePermiso('facturacion', 'ver')]

    def get(self, request):
        taller = services.taller_de_operacion(request, request.query_params.get('taller'))
        sesion = services.sesion_abierta(taller)
        ultimo = services.ultimo_cierre(taller)
        contexto = {'request': request, 'detalle': True}
        return Response({
            'taller': {'id': str(taller.uuid), 'nombre': taller.nombre},
            'sesion': SesionCajaSerializer(sesion, context=contexto).data if sesion else None,
            'ultimo_cierre': SesionCajaSerializer(ultimo, context={'request': request}).data if ultimo else None,
            'puede_registrar_gasto': _puede_gastos(request),
        })


class AbrirCajaView(APIView):
    permission_classes = [permissions.IsAuthenticated, TienePermiso('facturacion', 'modificar')]

    @transaction.atomic
    def post(self, request):
        datos = AbrirCajaSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        taller = services.taller_de_operacion(request, datos.validated_data.get('taller'))
        sesion = services.abrir(taller, monto_inicial=datos.validated_data['monto_inicial'], usuario=request.user)
        return Response(
            SesionCajaSerializer(sesion, context={'request': request, 'detalle': True}).data,
            status=status.HTTP_201_CREATED,
        )


class SesionCajaViewSet(LookupPublicoMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Jornadas de caja: historial de cierres, detalle, cierre, reapertura,
    ingresos/egresos y PDF del cierre. Filtros: `taller`, `estado`,
    `fecha_desde`, `fecha_hasta` (fecha de apertura)."""

    serializer_class = SesionCajaSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('facturacion')]

    def get_permissions(self):
        if self.action == 'reabrir':
            return [
                permissions.IsAuthenticated(),
                TienePermiso('facturacion', 'modificar')(),
                TieneAccion('reabrir_caja')(),
            ]
        if self.action == 'pdf':
            return [permissions.IsAuthenticated(), TienePermiso('facturacion', 'ver')()]
        return super().get_permissions()

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return SesionCaja.objects.none()
        queryset = SesionCaja.objects.filter(caja__empresa_id=empresa_id).select_related(
            'caja__taller', 'abierta_por', 'cerrada_por', 'reabierta_por',
        )
        talleres = talleres_filtro_de_request(self.request, explicito=self.request.query_params.get('taller'))
        if talleres is not None:
            queryset = queryset.filter(caja__taller_id__in=talleres)
        params = self.request.query_params
        if params.get('estado'):
            queryset = queryset.filter(estado=params['estado'])
        if params.get('fecha_desde'):
            queryset = queryset.filter(fecha_apertura__date__gte=params['fecha_desde'])
        if params.get('fecha_hasta'):
            queryset = queryset.filter(fecha_apertura__date__lte=params['fecha_hasta'])
        return queryset.order_by('-fecha_apertura', '-id')

    def get_serializer_context(self):
        contexto = super().get_serializer_context()
        contexto['detalle'] = self.action != 'list'
        return contexto

    def _sesion_bloqueada(self):
        return SesionCaja.objects.select_for_update().select_related('caja__taller').get(pk=self.get_object().pk)

    def _respuesta(self, sesion, codigo=status.HTTP_200_OK):
        sesion = self.get_queryset().get(pk=sesion.pk)
        return Response(self.get_serializer(sesion).data, status=codigo)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def cerrar(self, request, pk=None):
        datos = CerrarCajaSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        sesion = services.cerrar(
            self._sesion_bloqueada(),
            efectivo_contado=datos.validated_data['efectivo_contado'],
            observacion=datos.validated_data['observacion'],
            usuario=request.user,
        )
        return self._respuesta(sesion)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def reabrir(self, request, pk=None):
        datos = MotivoSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        sesion = services.reabrir(
            self._sesion_bloqueada(), motivo=datos.validated_data['motivo'].strip(), usuario=request.user,
        )
        return self._respuesta(sesion)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def movimientos(self, request, pk=None):
        datos = NuevoMovimientoSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        categoria = datos.validated_data['categoria_gasto']
        if categoria and not _puede_gastos(request):
            raise serializers.ValidationError({'categoria_gasto': 'No tienes permiso para registrar gastos.'})
        movimiento = services.registrar_movimiento(
            self._sesion_bloqueada(),
            tipo=datos.validated_data['tipo'],
            monto=datos.validated_data['monto'],
            motivo=datos.validated_data['motivo'].strip(),
            usuario=request.user,
            categoria_gasto=categoria or None,
        )
        return Response(MovimientoCajaSerializer(movimiento).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        sesion = self.get_object()
        return FileResponse(
            exportar_cierre_pdf(sesion, request),
            content_type='application/pdf',
            filename=f'caja_{sesion.caja.taller.nombre}_{sesion.fecha_apertura:%Y%m%d}.pdf'.replace(' ', '_'),
            as_attachment=True,
        )


class MovimientoCajaViewSet(LookupPublicoMixin, viewsets.GenericViewSet):
    """`POST caja/movimientos/{id}/anular/` (con la sesión abierta)."""

    serializer_class = MovimientoCajaSerializer
    permission_classes = [permissions.IsAuthenticated, TienePermiso('facturacion', 'modificar')]

    def get_queryset(self):
        sesiones = SesionCajaViewSet(request=self.request, action='list', format_kwarg=None).get_queryset()
        return MovimientoCaja.objects.filter(sesion__in=sesiones.values('pk')).select_related('sesion', 'gasto', 'pago_compra')

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def anular(self, request, pk=None):
        datos = MotivoSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        movimiento = self.get_object()
        SesionCaja.objects.select_for_update().get(pk=movimiento.sesion_id)
        movimiento = services.anular_movimiento(
            movimiento, usuario=request.user, motivo=datos.validated_data['motivo'].strip(),
        )
        return Response(MovimientoCajaSerializer(movimiento).data)
