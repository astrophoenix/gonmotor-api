from django.db import transaction
from django.db.models import Count, Prefetch, Sum
from rest_framework import filters, mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.permissions import TieneRecurso
from apps.core.filters import BusquedaIndexadaFilter
from apps.core.ids_publicos import filtrar_por_id_publico
from apps.core.mixins import FiltroDocumentoPadreMixin, FiltroTallerMixin, LookupPublicoMixin

from . import services
from .models import Compra, DetalleCompra, PagoCompra
from .serializers import AnularSerializer, CompraSerializer, PagoCompraSerializer


class CompraViewSet(
    LookupPublicoMixin,
    FiltroTallerMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Compras a proveedores. No se editan ni se borran: se anulan.

    Filtros: `search` (factura, proveedor), `proveedor`, `estado`, `con_saldo`,
    `fecha_desde`, `fecha_hasta`, `taller_id`. La respuesta del listado trae
    `resumen` con el total por pagar del filtro y su desglose por proveedor.
    """

    serializer_class = CompraSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('compras')]
    taller_filter_field = 'taller'
    taller_query_param = 'taller_id'
    filter_backends = [BusquedaIndexadaFilter, filters.OrderingFilter]
    search_fields = ['numero_factura', 'proveedor__nombre', 'proveedor__identificacion']
    ordering_fields = ['fecha', 'total', 'saldo_db']
    ordering = ['-fecha', '-created_at']

    def get_queryset(self):
        queryset = Compra.objects.para_request(
            self.request, explicito=self.request.query_params.get(self.taller_query_param),
        ).select_related('proveedor', 'taller', 'registrado_por', 'anulada_por').prefetch_related(
            Prefetch('detalles', queryset=DetalleCompra.objects.select_related('repuesto')),
            Prefetch('pagos', queryset=PagoCompra.objects.select_related('registrado_por', 'anulado_por', 'compra', 'movimiento_caja')),
        )
        queryset = services.anotar_saldo(queryset)
        params = self.request.query_params
        if params.get('proveedor'):
            queryset = filtrar_por_id_publico(queryset, 'proveedor', params['proveedor'])
        if params.get('estado'):
            queryset = queryset.filter(estado=params['estado'])
        if params.get('con_saldo', '').lower() == 'true':
            queryset = queryset.filter(saldo_db__gt=0)
        if params.get('fecha_desde'):
            queryset = queryset.filter(fecha__gte=params['fecha_desde'])
        if params.get('fecha_hasta'):
            queryset = queryset.filter(fecha__lte=params['fecha_hasta'])
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        respuesta = self.get_paginated_response(
            self.get_serializer(self.paginate_queryset(queryset), many=True).data
        )
        con_saldo = queryset.filter(saldo_db__gt=0).order_by()
        total = con_saldo.aggregate(saldo=Sum('saldo_db'))['saldo'] or services.CERO
        por_proveedor = (
            con_saldo.values('proveedor__uuid', 'proveedor__nombre')
            .annotate(saldo=Sum('saldo_db'), compras=Count('pk'))
            .order_by('-saldo')
        )
        respuesta.data['resumen'] = {
            'total_por_pagar': str(total),
            'compras_con_saldo': con_saldo.count(),
            'por_proveedor': [
                {
                    'proveedor': str(fila['proveedor__uuid']),
                    'nombre': fila['proveedor__nombre'],
                    'saldo': str(fila['saldo']),
                    'compras': fila['compras'],
                }
                for fila in por_proveedor
            ],
        }
        return respuesta

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save()

    def create(self, request, *args, **kwargs):
        respuesta = super().create(request, *args, **kwargs)
        compra = self.get_queryset().get(uuid=respuesta.data['id'])
        respuesta.data = self.get_serializer(compra).data
        return respuesta

    @action(detail=True, methods=['post'], url_path='anular')
    @transaction.atomic
    def anular(self, request, pk=None):
        datos = AnularSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        compra = Compra.objects.select_for_update().get(pk=self.get_object().pk)
        services.anular_compra(compra, usuario=request.user, motivo=datos.validated_data['motivo'].strip())
        return Response(self.get_serializer(self.get_queryset().get(pk=compra.pk)).data)


class PagoCompraViewSet(
    LookupPublicoMixin,
    FiltroDocumentoPadreMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Pagos a proveedores contra una compra (`?compra=`); se anulan, no se editan."""

    serializer_class = PagoCompraSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('compras')]
    filter_backends = [filters.OrderingFilter]
    ordering = ['-fecha', '-id']
    documento_padre = 'compra'
    documento_query_param = 'compra'
    relaciones_extra = ('registrado_por', 'anulado_por', 'movimiento_caja')

    @transaction.atomic
    def perform_create(self, serializer):
        datos = dict(serializer.validated_data)
        compra = Compra.objects.select_for_update().get(pk=datos.pop('compra').pk)
        serializer.instance = services.registrar_pago(
            compra, monto=datos.pop('monto'), usuario=self.request.user, **datos,
        )

    @action(detail=True, methods=['post'], url_path='anular')
    @transaction.atomic
    def anular(self, request, pk=None):
        datos = AnularSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        visible = self.get_object()
        Compra.objects.select_for_update().get(pk=visible.compra_id)
        pago = PagoCompra.objects.select_for_update().get(pk=visible.pk)
        services.anular_pago(pago, usuario=request.user, motivo=datos.validated_data['motivo'].strip())
        return Response(self.get_serializer(self.get_queryset().get(pk=pago.pk)).data)
