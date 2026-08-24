from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets, permissions
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
from .utils import get_empresa_id_desde_request
from .models import UserProfile

from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny
from .models import UsuarioEmpresa

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
        user = self.request.user
        if hasattr(user, 'profile') and user.profile.empresa:
            # Retorna únicamente los usuarios que pertenecen a la misma Empresa
            return User.objects.filter(profile__empresa=user.profile.empresa)
        return User.objects.none()
