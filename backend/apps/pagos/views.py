from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework import filters, generics, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import TienePermiso, TieneRecurso
from apps.core.filters import BusquedaIndexadaFilter
from apps.core.historial_documentos import Evento, registrar_evento
from apps.core.ids_publicos import filtrar_por_id_publico, por_id_publico
from apps.clientes.models import Cliente
from apps.core.mixins import FiltroDocumentoPadreMixin, LookupPublicoMixin
from apps.ordenes.models import OrdenTrabajo

from . import services
from .models import PagoOrden
from .serializers import AnularPagoSerializer, CuentaPorCobrarSerializer, PagoOrdenSerializer


class PagosPagination(PageNumberPagination):
    # La ficha de la orden pide todos sus movimientos de una vez.
    page_size_query_param = 'page_size'
    max_page_size = 100


def _detalle_evento(pago):
    texto = f'{pago.get_forma_pago_display()} $ {pago.monto:,.2f}'
    return f'{texto} ({pago.referencia})' if pago.referencia else texto


class PagoOrdenViewSet(
    LookupPublicoMixin,
    FiltroDocumentoPadreMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Pagos, abonos y devoluciones de las órdenes de trabajo.

    No se editan ni se borran: un movimiento equivocado se anula con
    `POST {id}/anular/` y se registra de nuevo.
    """

    serializer_class = PagoOrdenSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('facturacion')]
    pagination_class = PagosPagination
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['fecha', 'monto']
    ordering = ['-fecha', '-id']

    documento_padre = 'orden_trabajo'
    documento_query_param = 'orden'
    relaciones_extra = ('registrado_por', 'anulado_por')

    def get_queryset(self):
        queryset = super().get_queryset()
        # Historial de pagos de un cliente (ficha del cliente).
        cliente = self.request.query_params.get('cliente')
        if cliente:
            queryset = queryset.filter(orden_trabajo__cliente__in=por_id_publico(Cliente.objects.all(), cliente))
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        datos = dict(serializer.validated_data)
        orden = OrdenTrabajo.objects.select_for_update().get(pk=datos.pop('orden_trabajo').pk)
        pago = services.registrar_movimiento(
            orden,
            tipo=datos.pop('tipo', PagoOrden.Tipo.PAGO),
            monto=datos.pop('monto'),
            usuario=self.request.user,
            **datos,
        )
        serializer.instance = pago
        evento = Evento.PAGO if pago.tipo == PagoOrden.Tipo.PAGO else Evento.DEVOLUCION
        registrar_evento(self.request, orden, evento, etiqueta=pago.get_tipo_display(), nuevo=_detalle_evento(pago))

    @action(detail=True, methods=['post'], url_path='anular')
    @transaction.atomic
    def anular(self, request, pk=None):
        datos = AnularPagoSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        pago = self.get_object()
        orden = OrdenTrabajo.objects.select_for_update().get(pk=pago.orden_trabajo_id)
        pago = PagoOrden.objects.select_for_update().get(pk=pago.pk)
        pago.orden_trabajo = orden
        services.anular_movimiento(pago, usuario=request.user, motivo=datos.validated_data['motivo'].strip())
        registrar_evento(
            request, orden, Evento.PAGO_ANULADO,
            etiqueta=f'{pago.get_tipo_display()} anulado', anterior=_detalle_evento(pago), nuevo=pago.motivo_anulacion,
        )
        pago = self.get_queryset().get(pk=pago.pk)
        return Response(self.get_serializer(pago).data)


class CuentasPorCobrarView(generics.ListAPIView):
    """Órdenes entregadas a crédito con saldo pendiente.

    Filtros: `search` (orden, cliente, identificación o placa), `cliente`
    (UUID) y `sucursal` (taller explícito, validado como en las órdenes).
    La respuesta paginada trae `resumen` con el total por cobrar del filtro y,
    en «Todos los talleres», el desglose por taller.
    """

    serializer_class = CuentaPorCobrarSerializer
    permission_classes = [permissions.IsAuthenticated, TienePermiso('facturacion', 'ver')]
    filter_backends = [BusquedaIndexadaFilter, filters.OrderingFilter]
    search_fields = ['numero_orden', 'cliente__nombre', 'cliente__identificacion', 'vehiculo__placa']
    # Por defecto, primero las que más tiempo llevan sin pagarse.
    ordering_fields = ['fecha_entrega', 'saldo_db']
    ordering = ['fecha_entrega', 'id']

    def get_queryset(self):
        queryset = OrdenTrabajo.objects.para_request(
            self.request, explicito=self.request.query_params.get('sucursal'),
        )
        cliente = self.request.query_params.get('cliente')
        if cliente:
            queryset = filtrar_por_id_publico(queryset, 'cliente', cliente)
        return services.cuentas_por_cobrar(queryset).select_related(
            'cliente', 'vehiculo', 'sucursal', 'entrega_credito_autorizada_por',
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        totales = queryset.order_by().aggregate(saldo=Sum('saldo_db'), ordenes=Count('pk'))
        por_taller = (
            queryset.order_by('sucursal__nombre')
            .values('sucursal__uuid', 'sucursal__nombre')
            .annotate(saldo=Sum('saldo_db'), ordenes=Count('pk'))
        )
        respuesta = self.get_paginated_response(
            self.get_serializer(self.paginate_queryset(queryset), many=True).data
        )
        respuesta.data['resumen'] = {
            'total_por_cobrar': str(totales['saldo'] or services.CERO),
            'ordenes': totales['ordenes'],
            'clientes': queryset.order_by().values('cliente_id').distinct().count(),
            'por_taller': [
                {
                    'taller': str(fila['sucursal__uuid']) if fila['sucursal__uuid'] else None,
                    'nombre': fila['sucursal__nombre'],
                    'saldo': str(fila['saldo']),
                    'ordenes': fila['ordenes'],
                }
                for fila in por_taller
            ],
        }
        return respuesta


class RecordarSaldoView(APIView):
    """`POST cuentas-por-cobrar/{orden}/recordar/`: aviso de saldo al cliente.

    Cuerpo opcional: `canal` (whatsapp | correo); sin dato de contacto para
    ese canal se usa el otro, igual que el aviso de retiro.
    """

    permission_classes = [permissions.IsAuthenticated, TienePermiso('facturacion', 'modificar')]

    def post(self, request, orden_id):
        from apps.notificaciones.serializers import NotificacionProgramadaSerializer, NotificarRetiroSerializer
        from apps.notificaciones.services import avisos

        datos = NotificarRetiroSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        with transaction.atomic():
            orden = get_object_or_404(
                por_id_publico(OrdenTrabajo.objects.para_request(request), orden_id).select_for_update()
            )
            try:
                notificacion, resultado = avisos.notificar_saldo(
                    orden, services.saldo_para_entrega(orden),
                    user=request.user, canal=datos.validated_data['canal'],
                )
            except avisos.AvisoNoPermitido as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        notificacion.refresh_from_db()
        return Response(
            {
                'resultado': resultado,
                'notificacion': NotificacionProgramadaSerializer(notificacion, context={'request': request}).data,
            },
            status=status.HTTP_201_CREATED if resultado.get('ok') else status.HTTP_400_BAD_REQUEST,
        )
