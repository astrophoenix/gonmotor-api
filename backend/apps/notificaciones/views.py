import hmac

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import EsAdminDeEmpresa, TienePermiso, TieneRecurso
from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.ids_publicos import filtrar_por_id_publico, id_publico
from apps.core.mixins import LookupPublicoMixin
from apps.empresas.models import Empresa
from apps.vehiculos.models import Vehiculo

from .models import Campana, NotificacionProgramada, PreferenciaMantenimiento, RegistroMensajeWhatsApp
from .serializers import (
    CampanaSerializer,
    CrearMantenimientoSerializer,
    NotificacionProgramadaSerializer,
    EnviarPruebaSerializer,
    EnviarRecordatorioSerializer,
    PreferenciaMantenimientoSerializer,
    RegistroMensajeWhatsAppSerializer,
)
from .services.telefonos import normalizar_celular, celular_para_wa_link
from .services.whatsapp import resumen_configuracion
from .services import recordatorios as recordatorios_svc
from .services import campanas as campanas_svc
from .services.cola import procesar_notificacion, procesar_pendientes
from .services.mantenimiento import programar_mantenimiento_manual
from apps.vehiculos.models import propietario_actual


def _empresa_o_400(empresa_id):
    try:
        return Empresa.objects.get(pk=empresa_id)
    except Empresa.DoesNotExist as exc:
        raise serializers.ValidationError('No se pudo determinar la empresa activa.') from exc


class EstadoNotificacionesView(APIView):
    """GET /api/notificaciones/estado/ — Resumen del proveedor activo.

    No expone secretos; sirve para que el frontend muestre si está en modo de
    prueba (mock), Twilio Sandbox o Meta y si quedó bien configurado.
    """

    permission_classes = [permissions.IsAuthenticated, TienePermiso('notificaciones', 'ver')]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        info = resumen_configuracion()
        preferencia = recordatorios_svc.get_preferencia(
            _empresa_o_400(empresa_id)
        )
        info['preferencia'] = PreferenciaMantenimientoSerializer(preferencia).data
        return Response(info)


class EnviarRecordatorioVehiculoView(APIView):
    """POST /api/notificaciones/recordatorios/enviar-vehiculo/

    Envía (o simula) el recordatorio de mantenimiento del vehículo al teléfono
    WhatsApp de su propietario actual.
    """

    permission_classes = [permissions.IsAuthenticated, TienePermiso('notificaciones', 'modificar')]

    def post(self, request):
        serializer = EnviarRecordatorioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        empresa = _empresa_o_400(empresa_id)

        vehiculo = Vehiculo.objects.operativos_en(empresa.pk).filter(
            uuid=serializer.validated_data['vehiculo_id'],
        ).first()
        if not vehiculo:
            return Response(
                {'detail': 'Vehículo no encontrado en la empresa actual.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        propietario = recordatorios_svc._propietario_actual(vehiculo, empresa_id)
        celular = normalizar_celular(propietario.cliente.telefono) if propietario else ''
        if not propietario or not celular:
            return Response(
                {'detail': (
                    'El vehículo no tiene un propietario activo con teléfono de WhatsApp. '
                    'Revisa los datos del cliente.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        preferencia = recordatorios_svc.get_preferencia(empresa)

        # Verificación (opcional): ¿demasiado reciente?
        if recordatorios_svc._ya_notificado_recientemente(
            vehiculo, empresa, preferencia.frecuencia_dias
        ):
            return Response({
                'detail': (
                    f'El vehículo {vehiculo.placa} ya recibió un recordatorio en los últimos '
                    f'{preferencia.frecuencia_dias} días. Envío omitido.'
                ),
                'omitido': True,
            }, status=status.HTTP_200_OK)

        resultado = recordatorios_svc.enviar_recordatorio_vehiculo(
            vehiculo=vehiculo,
            empresa=empresa,
            preferencia=preferencia,
            cliente=propietario.cliente,
            celular=celular,
            origen='MANUAL',
            user=request.user,
        )
        resultado['placa'] = vehiculo.placa
        resultado['cliente'] = propietario.cliente.nombre
        resultado['wa_link'] = resultado.get('wa_link') or f'https://wa.me/{celular_para_wa_link(celular)}'
        return Response(resultado, status=status.HTTP_201_CREATED if resultado.get('ok') else status.HTTP_400_BAD_REQUEST)


class EnviarPruebaWhatsAppView(APIView):
    """POST /api/notificaciones/recordatorios/enviar-prueba/

    Envía un mensaje de prueba gratuito hacia un número específico (ideal para
    validar la configuración del proveedor).
    """

    # Configuración del proveedor: además exige administrar la empresa.
    permission_classes = [
        permissions.IsAuthenticated,
        TienePermiso('notificaciones', 'modificar'),
        TienePermiso('empresa', 'modificar'),
    ]

    def post(self, request):
        serializer = EnviarPruebaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        empresa = _empresa_o_400(empresa_id)

        resultado = recordatorios_svc.enviar_mensaje_prueba(
            celular=serializer.validated_data['celular'],
            mensaje=serializer.validated_data.get('mensaje'),
            empresa=empresa,
            user=request.user,
        )
        status_code = status.HTTP_201_CREATED if resultado.get('ok') else status.HTTP_400_BAD_REQUEST
        return Response(resultado, status=status_code)


class RecordatoriosListView(APIView):
    """GET /api/notificaciones/recordatorios/

    Bitácora de mensajes de WhatsApp de la empresa actual. Acepta `?estado=`,
    `?origen=` y `?limit=` (máx. 50 por defecto).
    """

    permission_classes = [permissions.IsAuthenticated, TienePermiso('notificaciones', 'ver')]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = RegistroMensajeWhatsApp.objects.filter(empresa_id=empresa_id)
        estado = request.query_params.get('estado')
        if estado:
            queryset = queryset.filter(estado=estado)
        origen = request.query_params.get('origen')
        if origen:
            queryset = queryset.filter(origen=origen)

        try:
            limit = min(int(request.query_params.get('limit', 50)), 100)
        except ValueError:
            limit = 50

        resultados = list(queryset.select_related('vehiculo', 'enviado_por')[:limit])
        return Response({
            'resultados': RegistroMensajeWhatsAppSerializer(resultados, many=True).data,
            'total': queryset.count(),
        })


class PreferenciasView(APIView):
    """GET/PATCH /api/notificaciones/preferencias/ — Configuración de alertas."""

    # Leer: permiso de notificaciones. Cambiar: además administrar la empresa.
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('notificaciones'), EsAdminDeEmpresa]

    def _preferencia(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            raise serializers.ValidationError('No se pudo determinar la empresa activa.')
        return recordatorios_svc.get_preferencia(_empresa_o_400(empresa_id))

    def get(self, request):
        preferencia = self._preferencia(request)
        return Response(PreferenciaMantenimientoSerializer(preferencia).data)

    def patch(self, request):
        preferencia = self._preferencia(request)
        serializer = PreferenciaMantenimientoSerializer(
            preferencia, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

class CronProcesarNotificacionesView(APIView):
    """POST /api/notificaciones/cron/procesar/ — Envía la cola vencida.

    Lo llama un cron externo (no un usuario): sin JWT, se autentica con la
    cabecera `X-Cron-Token` contra `NOTIFICACIONES_CRON_TOKEN`. Con el token
    vacío en settings el endpoint queda deshabilitado (503).
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        esperado = getattr(settings, 'NOTIFICACIONES_CRON_TOKEN', '') or ''
        if not esperado:
            return Response(
                {'detail': 'El procesamiento por cron no está configurado.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        recibido = request.headers.get('X-Cron-Token', '')
        if not hmac.compare_digest(recibido.encode(), esperado.encode()):
            return Response(
                {'detail': 'Token de cron inválido.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(procesar_pendientes())


class NotificacionProgramadaViewSet(
    LookupPublicoMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """/api/notificaciones/programadas/ — Cola de avisos de la empresa.

    - GET: listado paginado; filtros `?estado=`, `?tipo=`, `?vehiculo=`, `?cliente=`, `?orden=`, `?cita=`.
    - POST: alta manual de un próximo mantenimiento (reemplaza el pendiente).
    - PATCH: edita una notificación PENDIENTE (fecha, canal, mensaje...).
    - POST `{id}/cancelar/` y `{id}/enviar-ahora/`.
    """

    serializer_class = NotificacionProgramadaSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('notificaciones')]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return NotificacionProgramada.objects.none()
        qs = NotificacionProgramada.objects.filter(empresa_id=empresa_id).select_related(
            'cliente', 'vehiculo', 'orden', 'cita', 'campana',
        )
        params = self.request.query_params
        for campo in ('estado', 'tipo', 'canal', 'origen'):
            if params.get(campo):
                qs = qs.filter(**{campo: params[campo]})
        for campo in ('vehiculo', 'cliente', 'orden', 'cita', 'campana'):
            qs = filtrar_por_id_publico(qs, campo, params.get(campo))
        # Pendientes: la más próxima primero. Resto: la más reciente primero.
        if params.get('estado') == NotificacionProgramada.Estado.PENDIENTE:
            return qs.order_by('fecha_programada', 'pk')
        return qs.order_by('-fecha_programada', '-pk')

    def create(self, request):
        datos = CrearMantenimientoSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        datos = datos.validated_data

        empresa = _empresa_o_400(get_empresa_id_desde_request(request))
        vehiculo = Vehiculo.objects.operativos_en(empresa.pk).filter(uuid=datos['vehiculo_id']).first()
        if not vehiculo:
            return Response({'detail': 'Vehículo no encontrado en la empresa actual.'}, status=status.HTTP_404_NOT_FOUND)
        propietario = propietario_actual(vehiculo, empresa.pk)
        if not propietario:
            return Response(
                {'detail': 'El vehículo no tiene un propietario activo en la empresa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notificacion = programar_mantenimiento_manual(
            empresa=empresa,
            vehiculo=vehiculo,
            cliente=propietario.cliente,
            km_objetivo=datos.get('km_objetivo'),
            fecha_objetivo=datos.get('fecha_objetivo'),
            fecha_programada=datos.get('fecha_programada'),
            canal=datos['canal'],
            mensaje=datos.get('mensaje', ''),
            user=request.user,
        )
        return Response(self.get_serializer(notificacion).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        if serializer.instance.estado != NotificacionProgramada.Estado.PENDIENTE:
            raise serializers.ValidationError('Solo se edita una notificación pendiente.')
        serializer.save()

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        notificacion = self.get_object()
        if notificacion.estado != NotificacionProgramada.Estado.PENDIENTE:
            return Response({'detail': 'Solo se cancela una notificación pendiente.'}, status=status.HTTP_400_BAD_REQUEST)
        notificacion.estado = NotificacionProgramada.Estado.CANCELADA
        notificacion.save(update_fields=['estado', 'updated_at'])
        return Response(self.get_serializer(notificacion).data)

    @action(detail=True, methods=['post'], url_path='enviar-ahora')
    def enviar_ahora(self, request, pk=None):
        """Envía ya una notificación pendiente, o reintenta una con error."""
        reenviables = (NotificacionProgramada.Estado.PENDIENTE, NotificacionProgramada.Estado.ERROR)
        with transaction.atomic():
            notificacion = self.get_object()
            # Bloquea la fila: el cron no puede enviarla a la vez.
            notificacion = NotificacionProgramada.objects.select_for_update().get(pk=notificacion.pk)
            if notificacion.estado not in reenviables:
                return Response(
                    {'detail': 'Solo se envía una notificación pendiente o con error.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if notificacion.estado == NotificacionProgramada.Estado.ERROR:
                notificacion.estado = NotificacionProgramada.Estado.PENDIENTE
                notificacion.intentos = 0
            resultado = procesar_notificacion(notificacion, user=request.user, origen='MANUAL')
        notificacion.refresh_from_db()
        return Response(
            {'resultado': resultado, 'notificacion': self.get_serializer(notificacion).data},
            status=status.HTTP_200_OK if resultado.get('ok') else status.HTTP_400_BAD_REQUEST,
        )


def campanas_con_conteos(qs):
    Estado = NotificacionProgramada.Estado
    return qs.annotate(
        n_pendientes=Count('notificaciones', filter=Q(notificaciones__estado=Estado.PENDIENTE)),
        n_enviadas=Count('notificaciones', filter=Q(notificaciones__estado=Estado.ENVIADA)),
        n_errores=Count('notificaciones', filter=Q(notificaciones__estado=Estado.ERROR)),
    )


class CampanaViewSet(LookupPublicoMixin, viewsets.ModelViewSet):
    """/api/notificaciones/campanas/ — Mensajes masivos o promocionales.

    Se editan y eliminan solo en borrador. `GET {id}/audiencia/` previsualiza
    los destinatarios; `POST {id}/lanzar/` los encola (además exige administrar
    la empresa, como el envío masivo de recordatorios); `POST {id}/cancelar/`
    cancela los mensajes aún pendientes.
    """

    serializer_class = CampanaSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('notificaciones')]

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Campana.objects.none()
        qs = Campana.objects.filter(empresa_id=empresa_id).select_related('creado_por', 'empresa')
        if self.request.query_params.get('estado'):
            qs = qs.filter(estado=self.request.query_params['estado'])
        return campanas_con_conteos(qs).order_by('-created_at')

    def perform_create(self, serializer):
        empresa = _empresa_o_400(get_empresa_id_desde_request(self.request))
        serializer.save(empresa=empresa, creado_por=self.request.user)

    def destroy(self, request, *args, **kwargs):
        campana = self.get_object()
        if campana.estado != Campana.Estado.BORRADOR:
            return Response(
                {'detail': 'Solo se elimina una campaña en borrador; cancélala en su lugar.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campana.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'])
    def audiencia(self, request, pk=None):
        clientes = campanas_svc.audiencia(self.get_object())
        muestra = list(clientes[:50])
        return Response({
            'total': clientes.count(),
            'muestra': [
                {
                    'id': id_publico(c),
                    'nombre': c.nombre,
                    'telefono': c.telefono,
                    'email': c.email,
                }
                for c in muestra
            ],
        })

    @action(
        detail=True,
        methods=['post'],
        permission_classes=[
            permissions.IsAuthenticated,
            TienePermiso('notificaciones', 'modificar'),
            TienePermiso('empresa', 'modificar'),
        ],
    )
    def lanzar(self, request, pk=None):
        campana = self.get_object()
        try:
            campanas_svc.lanzar(campana, user=request.user)
        except campanas_svc.CampanaNoEditable as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(self.get_queryset().get(pk=campana.pk)).data)

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        campana = self.get_object()
        try:
            canceladas = campanas_svc.cancelar(campana)
        except campanas_svc.CampanaNoEditable as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        datos = self.get_serializer(self.get_queryset().get(pk=campana.pk)).data
        datos['canceladas'] = canceladas
        return Response(datos)
