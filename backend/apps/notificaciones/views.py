from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
from apps.empresas.models import Empresa
from apps.vehiculos.models import Vehiculo

from .models import PreferenciaMantenimiento, RegistroMensajeWhatsApp
from .serializers import (
    EjecutarRecordatoriosSerializer,
    EnviarPruebaSerializer,
    EnviarRecordatorioSerializer,
    PreferenciaMantenimientoSerializer,
    RegistroMensajeWhatsAppSerializer,
)
from .services.telefonos import normalizar_celular, celular_para_wa_link
from .services.whatsapp import resumen_configuracion
from .services import recordatorios as recordatorios_svc


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

    permission_classes = [permissions.IsAuthenticated]

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

    permission_classes = [permissions.IsAuthenticated]

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

        vehiculo = Vehiculo.objects.filter(
            pk=serializer.validated_data['vehiculo_id'],
            empresas=empresa,
        ).first()
        if not vehiculo:
            return Response(
                {'detail': 'Vehículo no encontrado en la empresa actual.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        propietario = recordatorios_svc._propietario_actual(vehiculo)
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

    permission_classes = [permissions.IsAuthenticated]

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


class EjecutarRecordatoriosView(APIView):
    """POST /api/notificaciones/recordatorios/ejecutar/

    Dispara ahora el proceso de detección/envío de recordatorios para la
    empresa actual. Con `preview: true` solo lista los candidatos sin enviar.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = EjecutarRecordatoriosSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        empresa = _empresa_o_400(empresa_id)
        preferencia = recordatorios_svc.get_preferencia(empresa)

        resultado = recordatorios_svc.ejecutar_para_empresa(
            empresa=empresa,
            preferencia=preferencia,
            preview=serializer.validated_data.get('preview', False),
            vehiculos_ids=serializer.validated_data.get('vehiculos_ids'),
            user=request.user,
        )
        return Response(resultado)


class RecordatoriosListView(APIView):
    """GET /api/notificaciones/recordatorios/

    Bitácora de mensajes de WhatsApp de la empresa actual. Acepta `?estado=`,
    `?origen=` y `?limit=` (máx. 50 por defecto).
    """

    permission_classes = [permissions.IsAuthenticated]

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

    permission_classes = [permissions.IsAuthenticated]

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