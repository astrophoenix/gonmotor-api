from rest_framework import viewsets, status, serializers
from rest_framework.response import Response
from django.db import IntegrityError
from apps.empresas.models import Empresa, Taller
from apps.authentication.utils import get_empresa_id_desde_request
from .serializers import EmpresaConfigSerializer, TallerConfigSerializer
from .permissions import IsEmpresaAdminOrReadOnly

DUPLICATE_CODE_MESSAGE_TALLER = 'El código del taller ya está en uso por un taller activo.'


class TallerConfigViewSet(viewsets.ModelViewSet):
    """
    CRUD de sucursales / talleres de la empresa en sesión.

    GET/POST /api/configuracion/sucursales/        -> Listar / crear
    GET/PUT/PATCH/DELETE /api/configuracion/sucursales/{id}/
    """
    serializer_class = TallerConfigSerializer
    permission_classes = [IsEmpresaAdminOrReadOnly]

    def get_empresa_id(self, request):
        return get_empresa_id_desde_request(request)

    def get_queryset(self):
        empresa_id = self.get_empresa_id(self.request)
        if not empresa_id:
            return Taller.objects.none()
        return Taller.objects.filter(empresa_id=empresa_id)

    def _validar_nombre_duplicado(self, nombre, taller_id=None):
        empresa_id = self.get_empresa_id(self.request)
        nombre = (nombre or '').strip()
        if not empresa_id or not nombre:
            return
        qs = Taller.objects.filter(
            empresa_id=empresa_id,
            nombre__iexact=nombre,
            is_active=True,
        )
        if taller_id:
            qs = qs.exclude(pk=taller_id)
        if qs.exists():
            raise serializers.ValidationError({
                'nombre': 'Ya existe un taller activo con este nombre en la empresa.'
            })

    def perform_create(self, serializer):
        empresa_id = self.get_empresa_id(self.request)
        if not empresa_id:
            raise PermissionError('No se pudo determinar la empresa en sesión.')
        self._validar_nombre_duplicado(serializer.validated_data.get('nombre'))
        try:
            serializer.save(empresa_id=empresa_id)
        except IntegrityError as exc:
            if 'una_sucursal_activa_por_empresa_codigo' in str(exc):
                raise serializers.ValidationError({
                    'codigo_sucursal': DUPLICATE_CODE_MESSAGE_TALLER
                })
            raise exc

    def perform_update(self, serializer):
        self._validar_nombre_duplicado(
            serializer.validated_data.get('nombre'),
            taller_id=self.get_object().pk,
        )
        try:
            serializer.save()
        except IntegrityError as exc:
            if 'una_sucursal_activa_por_empresa_codigo' in str(exc):
                raise serializers.ValidationError({
                    'codigo_sucursal': DUPLICATE_CODE_MESSAGE_TALLER
                })
            raise exc


class EmpresaConfigViewSet(viewsets.ModelViewSet):
    """
    Endpoint de configuración de la empresa del taller (datos fiscales/corporativos).

    GET   /api/configuracion/empresa/   -> Empresa del tenant en sesión
    GET   /api/configuracion/empresa/{id}/ -> Empresa por id (tenant permitido)
    PATCH /api/configuracion/empresa/{id}/ -> Actualiza datos (solo administradores)
    """
    serializer_class = EmpresaConfigSerializer
    permission_classes = [IsEmpresaAdminOrReadOnly]

    def get_empresa_id(self, request):
        return get_empresa_id_desde_request(request)

    def get_queryset(self):
        return Empresa.objects.filter(is_active=True)

    def list(self, request, *args, **kwargs):
        empresa_id = self.get_empresa_id(request)
        if not empresa_id:
            return Response(
                {"detail": "No se pudo determinar la empresa en sesión."},
                status=status.HTTP_403_FORBIDDEN
            )
        try:
            empresa = Empresa.objects.get(id=empresa_id, is_active=True)
        except Empresa.DoesNotExist:
            return Response(
                {"detail": "La empresa en sesión no existe o está inactiva."},
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = self.get_serializer(empresa)
        return Response(serializer.data)