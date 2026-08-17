from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets, permissions
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .serializers import CustomTokenObtainPairSerializer

from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from django.conf import settings

from .serializers import PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from .serializers import UserAdminSerializer

class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Endpoint de Login.
    Recibe username/email y password, retorna access y refresh tokens.
    """
    serializer_class = CustomTokenObtainPairSerializer


class UserProfileView(APIView):
    """
    Endpoint para obtener los datos del usuario logueado actualmente.
    Requiere header: Authorization: Bearer <access_token>
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_staff': user.is_staff,
        }, status=status.HTTP_200_OK)

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

