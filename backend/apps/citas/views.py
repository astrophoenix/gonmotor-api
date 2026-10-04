from django.core import signing
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_time
from rest_framework import filters, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.mixins import SoftDeleteDestroyMixin
from apps.empresas.models import Taller

from .ics import SALT_ENLACE_CITA_ICS, respuesta_ics
from .models import Cita
from .serializers import CitaSerializer
from .services import disponibilidad, resolver_taller_de_cita

# Restricción de unicidad del número de recepción (UNIQUE (empresa, numero_recepcion)).
CONSTRAINT_RECEPCION = 'recepcion_empresa_sucursal_numero_unico'


class CompartirCitaIcs(APIView):
    """Descarga pública del `.ics` de una cita mediante un enlace firmado.

    El token se genera en `CitaSerializer.enlace_ics` con `django.core.signing`,
    por lo que solo quien posee el enlace (el taller se lo puede enviar al
    cliente) puede descargar el archivo. No exige sesión ni cabecera de empresa.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        try:
            cita_id = signing.loads(token, salt=SALT_ENLACE_CITA_ICS)
        except signing.BadSignature:
            raise Http404('Enlace de cita no válido.')

        cita = (
            Cita.objects.select_related('cliente', 'vehiculo', 'taller')
            .filter(pk=cita_id, is_active=True)
            .first()
        )
        if cita is None:
            raise Http404('La cita ya no existe.')

        return respuesta_ics(cita)


class CitaPagination(PageNumberPagination):
    """Paginación de citas.

    El listado sigue paginando a 10 por defecto, pero permite subir el tamaño
    (tope 500) para que el calendario pueda cargar un rango completo de fechas
    en una sola llamada.
    """

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 500


class CitaViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = CitaSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CitaPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'cliente__nombre',
        'cliente__identificacion',
        'vehiculo__placa',
        'vehiculo__marca',
        'vehiculo__modelo',
        'motivo_descripcion',
    ]
    ordering_fields = ['fecha_cita', 'hora_cita', 'created_at', 'id']
    ordering = ['fecha_cita', 'hora_cita']

    delete_identifier_fields = ['fecha_cita']
    delete_relation_fields = ['recepcion_generada']

    def has_related_records(self, instance):
        for field_name in self.delete_relation_fields:
            value = getattr(instance, field_name, None)
            if value is not None and bool(getattr(value, 'id', None)):
                return True
        return False

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Cita.objects.none()
        queryset = (
            Cita.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'taller', 'asesor', 'recepcion_generada')
        )

        numero = self.request.query_params.get('numero', '').strip()
        if numero.isdigit():
            queryset = queryset.filter(pk=int(numero))

        fecha = self.request.query_params.get('fecha')
        if fecha:
            queryset = queryset.filter(fecha_cita=fecha)

        estado = self.request.query_params.get('estado')
        if estado:
            queryset = queryset.filter(estado=estado)

        recepcion_generada = self.request.query_params.get('recepcion_generada')
        if recepcion_generada:
            queryset = queryset.filter(recepcion_generada_id=recepcion_generada)
        elif self.request.query_params.get('sin_recepcion') in ('1', 'true', 'True'):
            queryset = queryset.filter(recepcion_generada__isnull=True)

        desde = self.request.query_params.get('desde')
        if desde:
            try:
                queryset = queryset.filter(fecha_cita__gte=timezone.datetime.strptime(desde, '%Y-%m-%d').date())
            except ValueError:
                pass

        hasta = self.request.query_params.get('hasta')
        if hasta:
            try:
                queryset = queryset.filter(fecha_cita__lte=timezone.datetime.strptime(hasta, '%Y-%m-%d').date())
            except ValueError:
                pass

        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    @action(detail=False, methods=['get'])
    def disponibilidad(self, request):
        """Estado de la agenda del taller para una fecha.

        Query params opcionales: `fecha` (YYYY-MM-DD, por defecto hoy),
        `taller` (id; si no viene se usa el taller por defecto de la empresa),
        `cita` (id de la cita que se está editando, para excluirla del recuento)
        y `hora` + `duracion` para evaluar una concreción puntual.
        """
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa en sesión.'}, status=400)

        fecha_param = request.query_params.get('fecha')
        if fecha_param:
            try:
                fecha = timezone.datetime.strptime(fecha_param, '%Y-%m-%d').date()
            except ValueError:
                raise serializers.ValidationError(
                    {'fecha': ['La fecha debe tener el formato YYYY-MM-DD.']}
                )
        else:
            fecha = timezone.localdate()

        taller_param = request.query_params.get('taller')
        if taller_param:
            taller = Taller.objects.filter(pk=taller_param, empresa_id=empresa_id).first()
            if taller is None:
                raise serializers.ValidationError(
                    {'taller': ['El taller no pertenece a tu empresa.']}
                )
        else:
            taller = resolver_taller_de_cita(empresa_id, None)

        cita_excluida = None
        cita_param = request.query_params.get('cita')
        if cita_param:
            if not Cita.objects.filter(pk=cita_param, empresa_id=empresa_id).exists():
                raise serializers.ValidationError({'cita': ['La cita no pertenece a tu empresa.']})
            cita_excluida = cita_param

        hora = None
        duracion = None
        hora_param = request.query_params.get('hora')
        if hora_param:
            try:
                hora = parse_time(hora_param)
            except ValueError:
                hora = None
            if hora is None:
                raise serializers.ValidationError(
                    {'hora': ['La hora debe tener el formato HH:MM.']}
                )
            duracion_param = request.query_params.get('duracion')
            if duracion_param:
                try:
                    duracion = int(duracion_param)
                except ValueError:
                    raise serializers.ValidationError(
                        {'duracion': ['La duración debe expresarse en minutos.']}
                    )

        return Response(
            disponibilidad(
                taller,
                fecha,
                cita_excluida=cita_excluida,
                hora=hora,
                duracion_minutos=duracion,
            )
        )

    @action(detail=True, methods=['get'], url_path='ics')
    def ics(self, request, pk=None):
        """Archivo `.ics` de la cita para agregarla a un calendario."""
        return respuesta_ics(self.get_object())

    @action(detail=True, methods=['post'])
    def convertir_a_recepcion(self, request, pk=None):
        cita = self.get_object()
        try:
            recepcion = cita.convertir_a_recepcion(usuario=request.user)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        except IntegrityError as exc:
            # El número de recepción es único por taller: si dos conversiones
            # se cruzan, se avisa en vez de devolver un 500.
            if CONSTRAINT_RECEPCION not in str(exc):
                raise
            raise serializers.ValidationError(
                'No se pudo numerar la recepción porque ese número ya fue asignado. '
                'Intenta convertir la cita de nuevo.'
            )
        return Response({
            'id': cita.id,
            'estado': cita.estado,
            'recepcion_id': recepcion.id,
            'numero_recepcion': recepcion.numero_recepcion,
        })