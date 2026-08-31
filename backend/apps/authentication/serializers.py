from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes, force_str

from .models import UserProfile
from .models import UsuarioEmpresa

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    
    def validate(self, attrs):
        username = attrs.get(self.username_field, '')
        password = attrs.get('password', '')

        user = authenticate(
            request=self.context.get('request'),
            username=username,
            password=password
        )

        if user is None:
            raise serializers.ValidationError({
                "detail": "Correo o contraseña incorrectos. Por favor, verifica tus datos e intenta nuevamente."
            })

        if not user.is_active:
            raise serializers.ValidationError({
                "detail": "Tu cuenta está desactivada. Contacta al administrador del sistema."
            })

        self.user = user

        relaciones = UsuarioEmpresa.objects.filter(
            user=user,
            is_active=True,
            empresa__is_active=True
        ).select_related('empresa')
        
        if not relaciones.exists():
            raise serializers.ValidationError({
                "detail": "El usuario no tiene empresas asignadas o activas."
            })
        
        if relaciones.count() == 1:
            relacion = relaciones.first()
            empresa = relacion.empresa
            
            refresh = self.get_token_with_empresa(user, empresa.id, relacion.rol)
            
            data = {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'requires_company_selection': False,
                'user': {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "rol": relacion.rol,
                    "empresa_id": empresa.id,
                    "empresa_nombre": getattr(empresa, 'nombre_comercial', getattr(empresa, 'nombre', ''))
                }
            }
            return data

        empresas_list = [
            {
                "id": rel.empresa.id,
                "nombre_comercial": getattr(rel.empresa, 'nombre_comercial', getattr(rel.empresa, 'nombre', '')),
                "ruc": getattr(rel.empresa, 'ruc', ''),
                "rol": rel.rol
            }
            for rel in relaciones
        ]

        return {
            "requires_company_selection": True,
            "user_id": user.id,
            "empresas": empresas_list
        }

    @classmethod
    def get_token_with_empresa(cls, user, empresa_id, rol):
        token = super().get_token(user)
        # 🔒 Inyección del ID de empresa y del rol específico en la firma del JWT
        token['empresa_id'] = empresa_id
        token['rol'] = rol
        return token


class RegistrationSerializer(serializers.Serializer):
    """Valida y crea cuentas nuevas desde el registro público."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirmation = serializers.CharField(write_only=True, trim_whitespace=False)
    accepted_terms = serializers.BooleanField(write_only=True)

    def validate_email(self, value):
        value = value.lower().strip()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Ya existe una cuenta con este correo electrónico.')
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirmation']:
            raise serializers.ValidationError({
                'password_confirmation': 'Las contraseñas no coinciden.'
            })

        if not attrs['accepted_terms']:
            raise serializers.ValidationError({
                'accepted_terms': 'Debes aceptar los términos y condiciones.'
            })

        try:
            validate_password(attrs['password'])
        except DjangoValidationError as error:
            raise serializers.ValidationError({'password': list(error.messages)})

        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirmation')
        validated_data.pop('accepted_terms')
        email = validated_data['email']

        return User.objects.create_user(
            username=email,
            email=email,
            password=validated_data['password']
        )


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


class UserProfileUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, max_length=150)
    email = serializers.EmailField(required=False)
    first_name = serializers.CharField(required=False, max_length=150, allow_blank=True)
    last_name = serializers.CharField(required=False, max_length=150, allow_blank=True)
    telefono = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_email(self, value):
        user = self.context.get('request').user
        if User.objects.filter(email__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('Ya existe una cuenta con este correo electrónico.')
        return value

    def validate_username(self, value):
        user = self.context.get('request').user
        if User.objects.filter(username__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('Ya existe una cuenta con este nombre de usuario.')
        return value

    def update(self, instance, validated_data):
        profile_data = {}
        if 'telefono' in validated_data:
            profile_data['telefono'] = validated_data.pop('telefono')

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        if profile_data:
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()

        return instance


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


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)

    def validate_old_password(self, value):
        user = self.context.get('request').user
        if not user.check_password(value):
            raise serializers.ValidationError('La contraseña actual es incorrecta.')
        return value

    def validate_new_password(self, value):
        user = self.context.get('request').user
        validate_password(value, user=user)
        return value

    def save(self, **kwargs):
        user = self.context.get('request').user
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class EmpleadoWriteSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    telefono = serializers.CharField(required=False, allow_blank=True, default='')
    rol = serializers.ChoiceField(choices=UsuarioEmpresa.ROLES)
    talleres = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True,
    )
    is_active = serializers.BooleanField(default=True, required=False)

    def to_representation(self, instance):
        user = instance.user
        profile = getattr(user, 'profile', None)
        return {
            'id': instance.id,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_active': user.is_active,
                'telefono': getattr(profile, 'telefono', '') if profile else '',
            },
            'empresa': {
                'id': instance.empresa_id,
                'nombre': getattr(instance.empresa, 'nombre_comercial', getattr(instance.empresa, 'nombre', '')) if instance.empresa_id else None,
            } if instance.empresa_id else None,
            'rol': instance.rol,
            'rol_display': instance.get_rol_display(),
            'talleres': [
                {'id': t.id, 'nombre': t.nombre}
                for t in instance.talleres.all()
            ],
            'is_active': instance.is_active,
        }


class EmpleadoReadSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    talleres = serializers.SerializerMethodField()
    rol_display = serializers.SerializerMethodField()

    class Meta:
        model = UsuarioEmpresa
        fields = ['id', 'user', 'empresa', 'rol', 'rol_display', 'talleres', 'is_active']

    def get_user(self, obj):
        profile = getattr(obj.user, 'profile', None)
        return {
            'id': obj.user.id,
            'username': obj.user.username,
            'email': obj.user.email,
            'first_name': obj.user.first_name,
            'last_name': obj.user.last_name,
            'is_active': obj.user.is_active,
            'telefono': getattr(profile, 'telefono', '') if profile else '',
        }

    def get_rol_display(self, obj):
        return obj.get_rol_display()

    def get_talleres(self, obj):
        return [
            {'id': t.id, 'nombre': t.nombre}
            for t in obj.talleres.all()
        ]
