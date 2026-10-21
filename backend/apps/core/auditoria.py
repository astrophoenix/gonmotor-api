"""Auditoría central de operaciones de la API.

Una fila por petición de escritura exitosa, no por cada fila de una cascada.
Se registra la operación, no un diff de campos. No cubre SQL directo, comandos
de administración ni tareas en segundo plano. Nunca se leen payloads, query
strings completos, cookies, tokens o cabeceras de autenticación.
"""
import uuid
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError

from apps.authentication.permissions import TienePermiso
from apps.authentication.utils import get_contexto_desde_request, talleres_permitidos, taller_de_valor
from .models import RegistroAuditoria
from .serializers import ModelSerializerPublico


def registrar_acceso(request, usuario, empresa):
    """Marca un login validado por el servidor, nunca datos del cliente."""
    request._acceso_auditoria = (usuario, empresa)


class AuditoriaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE') or not request.path.startswith('/api/'):
            return self.get_response(request)
        # La operación y su registro se confirman juntos. Si la bitácora falla,
        # no se deja una escritura exitosa sin su trazabilidad.
        with transaction.atomic():
            response = self.get_response(request)
            if response.status_code >= 400:
                transaction.set_rollback(True)
                return response
            contexto = getattr(response, 'renderer_context', None) or {}
            drf_request = contexto.get('request')
            if drf_request is not None:
                self.registrar(drf_request, contexto.get('view'), response)
            return response

    def registrar(self, request, view, response):
        partes = request.path.strip('/').split('/')
        modulo = partes[1] if len(partes) > 1 else ''
        if modulo == 'auditoria' or (modulo == 'token'):
            return
        acceso = getattr(request, '_acceso_auditoria', None)
        if acceso:
            usuario, empresa = acceso
            empresa_id = empresa.pk
            accion = 'iniciar_sesion'
        else:
            usuario = request.user
            if not usuario or not usuario.is_authenticated:
                return
            contexto = get_contexto_desde_request(request)
            if not contexto.hay_contexto:
                return
            empresa_id = contexto.empresa_id
            accion = getattr(view, 'action', None) or (partes[-1] if len(partes) > 2 else request.method.lower())
        # La prevalidación de importaciones no es una importación confirmada.
        if request.query_params.get('validar') == '1':
            return
        taller = taller_de_valor(request.headers.get('X-Taller-ID'), request)
        if taller and not talleres_permitidos(usuario, get_contexto_desde_request(request)).filter(pk=taller.pk).exists():
            taller = None
        registro_id = getattr(view, 'kwargs', {}).get('pk', '')
        if not registro_id and isinstance(getattr(response, 'data', None), dict):
            registro_id = response.data.get('id', '')
        # Solo IDs públicos; nunca mensajes, correos o valores de formulario.
        try:
            registro_id = str(uuid.UUID(str(registro_id)))
        except (ValueError, TypeError, AttributeError):
            registro_id = ''
        RegistroAuditoria.objects.create(
            empresa_id=empresa_id, usuario=usuario,
            usuario_nombre=(usuario.get_full_name() or usuario.get_username())[:200],
            taller=taller, taller_nombre=(taller.nombre if taller else '')[:150],
            modulo=modulo[:60], accion=accion[:100], registro_id=registro_id,
            metodo=request.method,
        )


class RegistroAuditoriaSerializer(ModelSerializerPublico):
    class Meta:
        model = RegistroAuditoria
        fields = ['id', 'fecha', 'usuario_nombre', 'taller_nombre', 'modulo', 'accion', 'registro_id', 'metodo']
        read_only_fields = fields


class AuditoriaListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, TienePermiso('auditoria', 'ver')]
    serializer_class = RegistroAuditoriaSerializer

    def get_queryset(self):
        contexto = get_contexto_desde_request(self.request)
        qs = RegistroAuditoria.objects.filter(empresa_id=contexto.empresa_id)
        params = self.request.query_params
        for campo in ('modulo', 'accion'):
            if params.get(campo):
                qs = qs.filter(**{campo: params[campo]})
        if params.get('search'):
            texto = params['search'][:200]
            qs = qs.filter(Q(usuario_nombre__icontains=texto) | Q(registro_id__icontains=texto))
        fechas = {}
        for campo in ('desde', 'hasta'):
            if params.get(campo):
                try:
                    fechas[campo] = datetime.strptime(params[campo], '%Y-%m-%d').date()
                except ValueError:
                    raise ValidationError({campo: 'Usa una fecha válida en formato AAAA-MM-DD.'})
        if fechas.get('desde') and fechas.get('hasta') and fechas['desde'] > fechas['hasta']:
            raise ValidationError({'hasta': 'La fecha final no puede ser anterior a la inicial.'})
        if 'desde' in fechas:
            qs = qs.filter(fecha__gte=timezone.make_aware(datetime.combine(fechas['desde'], time.min)))
        if 'hasta' in fechas:
            try:
                fin = fechas['hasta'] + timedelta(days=1)
            except OverflowError:
                raise ValidationError({'hasta': 'La fecha final está fuera de rango.'})
            qs = qs.filter(fecha__lt=timezone.make_aware(datetime.combine(fin, time.min)))
        return qs
