import logging

from django.db import transaction
from django.utils import timezone
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework import filters, generics, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import TienePermiso, TieneRecurso
from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.filters import BusquedaIndexadaFilter
from apps.core.historial_documentos import Evento, registrar_evento
from apps.core.ids_publicos import filtrar_por_id_publico, por_id_publico
from apps.clientes.models import Cliente
from apps.core.mixins import FiltroDocumentoPadreMixin, LookupPublicoMixin
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.export_limits import ExporteDemasiadoGrande
from apps.core.utils.pdf_export import contexto_reporte, fecha_hora_local
from apps.ordenes.models import OrdenTrabajo
from apps.ordenes.views import parsear_fecha

from . import services
from .models import PagoOrden
from .serializers import AnularPagoSerializer, CuentaPorCobrarSerializer, PagoOrdenSerializer

logger = logging.getLogger(__name__)


class PagosPagination(PageNumberPagination):
    # La ficha de la orden pide todos sus movimientos de una vez.
    page_size_query_param = 'page_size'
    max_page_size = 100


def _excel(request, *, titulo, archivo, encabezados, filas, queryset, alineaciones=None):
    """Respuesta .xlsx con la cabecera de la empresa; el tope de filas responde 400."""
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)
    empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)
    config = ExcelExportConfig(
        title=titulo, filename=archivo, headers=encabezados, empresa=empresa, taller=taller,
        usuario=usuario_nombre, row_builder=filas, alignments=alineaciones,
    )
    try:
        return ExcelExportService(config, queryset).generate_response()
    except ExporteDemasiadoGrande:
        raise  # DRF responde 400 con el detalle
    except Exception:
        logger.exception('Error al generar el Excel %s', archivo)
        return Response({'detail': 'No se pudo generar el Excel en este momento.'}, status=500)


def _si_no(valor):
    return 'Sí' if valor else 'No'


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
        params = self.request.query_params
        # Historial de pagos de un cliente (ficha del cliente).
        cliente = params.get('cliente')
        if cliente:
            queryset = queryset.filter(orden_trabajo__cliente__in=por_id_publico(Cliente.objects.all(), cliente))
        # Rango por día en la zona del proyecto (`__date` usa la zona activa).
        fecha_desde = parsear_fecha(params.get('fecha_desde'))
        if fecha_desde:
            queryset = queryset.filter(fecha__date__gte=fecha_desde)
        fecha_hasta = parsear_fecha(params.get('fecha_hasta'))
        if fecha_hasta:
            queryset = queryset.filter(fecha__date__lte=fecha_hasta)
        if params.get('tipo') in PagoOrden.Tipo.values:
            queryset = queryset.filter(tipo=params['tipo'])
        if params.get('forma_pago') in PagoOrden.FormaPago.values:
            queryset = queryset.filter(forma_pago=params['forma_pago'])
        anulado = (params.get('anulado') or '').lower()
        if anulado in ('true', 'false'):
            queryset = queryset.filter(anulado=anulado == 'true')
        return queryset

    EXCEL_HEADERS = [
        ('Fecha', 1.2), ('Orden', 1.0), ('Taller', 1.2), ('Cliente', 2.0), ('Identificación', 1.2),
        ('Tipo', 0.9), ('Forma de pago', 1.2), ('Referencia', 1.2), ('Monto', 0.9), ('Anticipo', 0.7),
        ('Registrado por', 1.4), ('Anulado', 0.7), ('Motivo de anulación', 2.0),
    ]

    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        """Pagos y devoluciones con los filtros del listado; las devoluciones en negativo."""
        queryset = self.filter_queryset(self.get_queryset()).select_related(
            'orden_trabajo__cliente', 'orden_trabajo__sucursal',
        )

        def filas(pago):
            orden = pago.orden_trabajo
            usuario = pago.registrado_por
            return [
                fecha_hora_local(pago.fecha),
                orden.numero_orden,
                orden.sucursal.nombre if orden.sucursal_id else '-',
                orden.cliente.nombre,
                orden.cliente.identificacion,
                pago.get_tipo_display(),
                pago.get_forma_pago_display(),
                pago.referencia,
                -pago.monto if pago.tipo == PagoOrden.Tipo.DEVOLUCION else pago.monto,
                _si_no(pago.es_anticipo),
                (usuario.get_full_name() or usuario.username) if usuario else '-',
                _si_no(pago.anulado),
                pago.motivo_anulacion,
            ]

        return _excel(
            request, titulo='Pagos de órdenes', archivo='pagos.xlsx', encabezados=self.EXCEL_HEADERS,
            filas=filas, queryset=queryset, alineaciones=['left'] * 8 + ['right'] + ['left'] * 4,
        )

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


class CuentasPorCobrarExcelView(CuentasPorCobrarView):
    """`GET cuentas-por-cobrar/export-excel/`: mismos filtros que el listado."""

    def get(self, request, *args, **kwargs):
        hoy = timezone.localdate()

        def filas(orden):
            usuario = orden.entrega_credito_autorizada_por
            entrega = timezone.localdate(orden.fecha_entrega) if orden.fecha_entrega else None
            return [
                orden.numero_orden,
                orden.sucursal.nombre if orden.sucursal_id else '-',
                orden.cliente.nombre,
                orden.cliente.identificacion,
                orden.cliente.telefono or '',
                orden.vehiculo.placa,
                entrega.strftime('%d/%m/%Y') if entrega else '-',
                max((hoy - entrega).days, 0) if entrega else 0,
                orden.total,
                orden.pagado_neto_db,
                orden.saldo_db,
                (usuario.get_full_name() or usuario.username) if usuario else '-',
                fecha_hora_local(orden.ultimo_recordatorio),
            ]

        return _excel(
            request,
            titulo='Cuentas por cobrar',
            archivo='cuentas_por_cobrar.xlsx',
            encabezados=[
                ('Orden', 1.0), ('Taller', 1.2), ('Cliente', 2.0), ('Identificación', 1.2), ('Teléfono', 1.1),
                ('Placa', 0.9), ('Entregada', 1.0), ('Días de atraso', 0.8), ('Total', 0.9), ('Pagado', 0.9),
                ('Saldo', 0.9), ('Autorizó la entrega', 1.4), ('Último recordatorio', 1.3),
            ],
            filas=filas,
            queryset=self.filter_queryset(self.get_queryset()),
            alineaciones=['left'] * 7 + ['right'] * 4 + ['left'] * 2,
        )
