from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers

from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes, force_str

from .models import UserProfile


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Serializer personalizado de JWT para aceptar username o email en el mismo campo.
    """
    def validate(self, attrs):
        # attrs contiene 'username' y 'password' enviados desde el cliente
        data = super().validate(attrs)

        # Opcional: Agregar información útil del usuario a la respuesta del Login
        data['user'] = {
            'id': self.user.id,
            'username': self.user.username,
            'email': self.user.email,
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'rol': self.user.profile.rol if hasattr(self.user, 'profile') else None,
            'empresa_id': self.user.profile.empresa_id if hasattr(self.user, 'profile') else None,
        }
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Serializer para solicitar el token de recuperación por email.
    """
    email = serializers.EmailField()

    def validate_email(self, value):
        value = value.lower().strip()
        if not User.objects.filter(email__iexact=value).exists():
            # Por seguridad no revelamos explícitamente si el correo no existe, 
            # pero puedes lanzar validación si prefieres en desarrollo.
            pass
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs):
        # A. Validar Usuario (uid)
        try:
            uid = force_str(urlsafe_base64_decode(attrs['uid']))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError({"uid": "Usuario no válido o enlace alterado."})

        # B. Validar Token de recuperación
        token_generator = PasswordResetTokenGenerator()
        if not token_generator.check_token(user, attrs['token']):
            raise serializers.ValidationError({"token": "El token es inválido o ha expirado (recuerda que dura 5 minutos)."})

        # C. Validar Contraseña contra los AUTH_PASSWORD_VALIDATORS de settings.py
        new_password = attrs.get('new_password')
        try:
            validate_password(password=new_password, user=user)
        except DjangoValidationError as error:
            # Pasa los errores de validación de Django hacia DRF para retornar un HTTP 400 claro
            raise serializers.ValidationError({"new_password": list(error.messages)})

        attrs['user'] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data['user']
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ['rol', 'telefono', 'empresa', 'talleres', 'taller_activo']


class UserAdminSerializer(serializers.ModelSerializer):
    """
    Serializer para el CRUD administrativo de usuarios.
    """
    profile = UserProfileSerializer(required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_active', 'password', 'profile']

    def validate_password(self, value):
        if value:
            try:
                validate_password(password=value)
            except DjangoValidationError as error:
                raise serializers.ValidationError(list(error.messages))
        return value

    def create(self, validated_data):
        profile_data = validated_data.pop('profile', {})
        password = validated_data.pop('password', None)

        user = User.objects.create(**validated_data)
        if password:
            user.set_password(password)
            user.save()

        # Actualizar el perfil que creó el signal automáticamente
        if profile_data and hasattr(user, 'profile'):
            talleres = profile_data.pop('talleres', [])
            for attr, val in profile_data.items():
                setattr(user.profile, attr, val)
            user.profile.save()
            if talleres:
                user.profile.talleres.set(talleres)

        return user

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', {})
        password = validated_data.pop('password', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()

        if profile_data and hasattr(instance, 'profile'):
            talleres = profile_data.pop('talleres', None)
            for attr, val in profile_data.items():
                setattr(instance.profile, attr, val)
            instance.profile.save()
            if talleres is not None:
                instance.profile.talleres.set(talleres)

        return instance