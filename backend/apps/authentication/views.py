from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .serializers import CustomTokenObtainPairSerializer, RegistrationSerializer

from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from django.conf import settings

from .serializers import PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from .serializers import UserAdminSerializer, UserProfileUpdateSerializer, ChangePasswordSerializer
from .serializers import EmpleadoWriteSerializer, EmpleadoReadSerializer
from .utils import get_empresa_id_desde_request
from .models import UserProfile, UsuarioEmpresa
from apps.empresas.models import Empresa, Taller

from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny

User = get_user_model()
class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Endpoint de Login.
    Recibe username/email y password, retorna access y refresh tokens.
    """
    serializer_class = CustomTokenObtainPairSerializer


class SelectCompanyView(APIView):
    """
    Endpoint invocado desde el frontend tras el login si el usuario 
    tiene acceso a múltiples empresas (RUCs distintos).
    """
    permission_classes = [AllowAny]

    def post(self, request):
        user_id = request.data.get('user_id')
        empresa_id = request.data.get('empresa_id')

        if not user_id or not empresa_id:
            return Response(
                {"detail": "Se requieren 'user_id' y 'empresa_id'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Se busca la asignación activa del usuario a la empresa elegida
            relacion = UsuarioEmpresa.objects.select_related('empresa', 'user').get(
                user_id=user_id,
                empresa_id=empresa_id,
                is_active=True,
                empresa__is_active=True
            )
        except UsuarioEmpresa.DoesNotExist:
            return Response(
                {"detail": "No tienes acceso o la empresa seleccionada no está activa."},
                status=status.HTTP_403_FORBIDDEN
            )

        user = relacion.user
        empresa = relacion.empresa

        # Generar tokens inyectando la empresa y rol seleccionados
        refresh = RefreshToken.for_user(user)
        refresh['empresa_id'] = empresa.id
        refresh['rol'] = relacion.rol

        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "rol": relacion.rol,
                "empresa_id": empresa.id,
                "empresa_nombre": getattr(empresa, 'nombre_comercial', getattr(empresa, 'nombre', ''))
            }
        }, status=status.HTTP_200_OK)


class RegistrationView(APIView):
    """Crea una cuenta de usuario sin requerir autenticación previa."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                'detail': 'La cuenta fue creada correctamente.',
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'username': user.username,
                }
            },
            status=status.HTTP_201_CREATED
        )


class UserProfileView(APIView):
    """
    Endpoint para obtener y actualizar los datos del usuario logueado actualmente.
    Requiere header: Authorization: Bearer <access_token>
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        empresa_id = get_empresa_id_desde_request(request)
        profile = getattr(user, 'profile', None)
        telefono = getattr(profile, 'telefono', '') if profile else ''

        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'telefono': telefono,
            'is_staff': user.is_staff,
            'empresa_id': empresa_id,
        }, status=status.HTTP_200_OK)

    def patch(self, request):
        user = request.user
        serializer = UserProfileUpdateSerializer(
            instance=user,
            data=request.data,
            partial=True,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        profile = getattr(user, 'profile', None)
        telefono = getattr(profile, 'telefono', '') if profile else ''

        user_data = {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'telefono': telefono,
            'is_staff': user.is_staff,
        }

        return Response({
            'user': user_data,
            'detail': 'Perfil actualizado correctamente.'
        }, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """
    Permite al usuario autenticado cambiar su propia contraseña.
    Requiere la contraseña actual y la nueva.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"detail": "Contraseña actualizada correctamente."},
            status=status.HTTP_200_OK
        )


class PasswordResetRequestView(APIView):
    """
    Solicita un enlace de recuperación de contraseña enviado por correo.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = User.objects.filter(email__iexact=email).first()
        if user:
            # Generar identificador de usuario en base64 y token único
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = PasswordResetTokenGenerator().make_token(user)

            # Enlace que abrirá el frontend
            reset_url = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"

            # Enviar Email
            subject = "Restablece tu contraseña - Gestor de Taller"
            message = (
                f"Hola {user.first_name or user.username},\n\n"
                f"Has solicitado restablecer tu contraseña. Haz clic en el siguiente enlace:\n\n"
                f"{reset_url}\n\n"
                f"Si no solicitaste este cambio, puedes ignorar este mensaje."
            )
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else 'noreply@taller.com',
                [user.email],
                fail_silently=False,
            )

        return Response(
            {"detail": "Si el correo existe en el sistema, se ha enviado un enlace de recuperación."},
            status=status.HTTP_200_OK
        )


class PasswordResetConfirmView(APIView):
    """
    Procesa la nueva contraseña enviando uid, token y la contraseña nueva.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"detail": "La contraseña ha sido restablecida exitosamente."},
            status=status.HTTP_200_OK
        )


class UserManagementViewSet(viewsets.ModelViewSet):
    """
    ViewSet para CRUD completo de Usuarios (filtrado por la Empresa del usuario autenticado).
    """
    serializer_class = UserAdminSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return User.objects.none()
        usuario_ids = UsuarioEmpresa.objects.filter(
            empresa_id=empresa_id,
            is_active=True
        ).values_list('user_id', flat=True)
        return User.objects.filter(id__in=usuario_ids)


class EmpleadoViewSet(viewsets.ModelViewSet):
    """
    ViewSet para CRUD de empleados de la empresa actual.
    Trabaja sobre UsuarioEmpresa y expone datos anidados del User.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        query_empresa_id = self.request.query_params.get('empresa')

        if query_empresa_id:
            try:
                query_empresa_id = int(query_empresa_id)
            except (TypeError, ValueError):
                query_empresa_id = None

        final_empresa_id = query_empresa_id or empresa_id

        print(f"[EMPLEADO LIST] usuario={self.request.user.id}, empresa_id_header={empresa_id}, query_empresa={query_empresa_id}, final={final_empresa_id}")

        if not final_empresa_id:
            return UsuarioEmpresa.objects.none()

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'
        queryset = UsuarioEmpresa.objects.filter(empresa_id=final_empresa_id)
        print(f"[EMPLEADO LIST] queryset_count={queryset.count()}")
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        print(f"[EMPLEADO LIST] final_count={queryset.count()}")

        return queryset.select_related('user', 'empresa').prefetch_related('talleres', 'user__profile')

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return EmpleadoReadSerializer
        return EmpleadoWriteSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        empresa_id = get_empresa_id_desde_request(request)
        print(f"[EMPLEADO CREATE] usuario={request.user.id}, empresa_id={empresa_id}, empresa={empresa.id if empresa else None}")
        if not empresa_id:
            raise ValidationError({'empresa': 'No se pudo determinar la empresa activa.'})
        
        empresa = Empresa.objects.filter(id=empresa_id, is_active=True).first()
        print(f"[EMPLEADO CREATE] empresa encontrada={empresa.id if empresa else None}")
        if not empresa:
            raise ValidationError({'empresa': 'La empresa seleccionada no está activa.'})
        
        validated_data = serializer.validated_data
        email = validated_data['email'].lower().strip()
        username = email
        
        existing_user = User.objects.filter(username__iexact=username).first()
        if not existing_user:
            existing_user = User.objects.filter(email__iexact=email).first()
        
        if existing_user:
            es_empleado_empresa = UsuarioEmpresa.objects.filter(
                user=existing_user,
                empresa_id=empresa.id,
                is_active=True,
            ).exists()
            
            if es_empleado_empresa:
                raise ValidationError({
                    'email': 'Ya existe otro empleado registrado con este correo en el sistema.'
                })
            
            raise ValidationError({
                'email': 'Ya existe un usuario con este correo en el sistema.'
            })
        
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            password=None,
        )
        user.is_active = True
        user.save()
        
        UserProfile.objects.create(user=user, telefono=validated_data.get('telefono', ''))
        
        usuario_empresa = UsuarioEmpresa.objects.create(
            user=user,
            empresa=empresa,
            rol=validated_data['rol'],
            is_active=validated_data.get('is_active', True),
        )
        usuario_empresa.talleres.set(validated_data.get('talleres', []))
        
        token_generator = PasswordResetTokenGenerator()
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = token_generator.make_token(user)
        reset_url = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
        
        subject = "Establece tu contraseña - Gestor de Taller"
        message = (
            f"Hola {validated_data['first_name']},\n\n"
            f"Has sido registrado en el sistema de gestión de taller. "
            f"Para establecer tu contraseña, haz clic en el siguiente enlace:\n\n"
            f"{reset_url}\n\n"
            f"Este enlace expirará en 5 minutos por seguridad.\n\n"
            f"Si no solicitaste este registro, puedes ignorar este mensaje."
        )
        
        send_mail(
            subject,
            message,
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@taller.com'),
            [user.email],
            fail_silently=False,
        )
        
        read_serializer = EmpleadoReadSerializer(usuario_empresa, context={'request': request})
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_update(self, serializer):
        validated_data = serializer.validated_data
        usuario_empresa = serializer.instance

        user = usuario_empresa.user
        user.first_name = validated_data.get('first_name', user.first_name)
        user.last_name = validated_data.get('last_name', user.last_name)
        user.email = validated_data.get('email', user.email).lower().strip()
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.telefono = validated_data.get('telefono', profile.telefono)
        profile.save()

        usuario_empresa.rol = validated_data.get('rol', usuario_empresa.rol)
        usuario_empresa.is_active = validated_data.get('is_active', usuario_empresa.is_active)
        usuario_empresa.save()
        if 'talleres' in validated_data:
            usuario_empresa.talleres.set(validated_data.get('talleres', []))

    @action(detail=False, methods=['get'], url_path='talleres')
    def talleres(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=400)

        talleres_qs = Taller.objects.filter(empresa_id=empresa_id, is_active=True).order_by('nombre')
        data = [
            {'id': t.id, 'nombre': t.nombre, 'codigo_sucursal': t.codigo_sucursal}
            for t in talleres_qs
        ]
        return Response(data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        return Response({'detail': 'Empleado dado de baja correctamente.'}, status=200)
