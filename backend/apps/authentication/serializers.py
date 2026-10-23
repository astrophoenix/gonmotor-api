from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.crypto import constant_time_compare, salted_hmac
from django.db.models import Q
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes, force_str

from apps.core.ids_publicos import id_publico
from apps.empresas.models import Empresa, Taller
from .models import UserProfile
from .models import UsuarioEmpresa

from .permisos_repo import nombre_rol
from apps.core.serializers import ImagenProcesadaField, ModelSerializerPublico


def _taller_sesion_nombre(user, empresa):
    """Nombre del taller de la sesión actual.

    Devuelve el taller activo del perfil solo si está dentro del alcance del
    usuario para la empresa activa (empresa correcta + rol + talleres asignados)
    y, si no hay selección, el primer taller permitido. Nunca muestra un taller
    en el que el usuario no puede operar. La administración (que puede operar
    sin taller, «Todos») no tiene taller de sesión por defecto.
    """
    from .utils import contexto_de_usuario, es_admin_talleres, taller_de_usuario

    empresa_id = getattr(empresa, 'id', empresa)
    try:
        empresa_id = int(empresa_id) if empresa_id is not None else None
    except (TypeError, ValueError):
        empresa_id = None

    contexto = contexto_de_usuario(user, empresa_id)
    if es_admin_talleres(contexto):
        return ''

    taller_id = taller_de_usuario(user, empresa_id)
    if not taller_id:
        return ''
    return Taller.objects.filter(pk=taller_id).values_list('nombre', flat=True).first() or ''


def _taller_sesion_id(user, empresa):
    """Id del taller de sesión validado (perfil > primer permitido), o None.

    La administración (ADMIN_EMPRESA / ADMIN_SISTEMA o superusuario) no tiene
    taller por defecto: opera sin recorte («Todos»).
    """
    from .utils import contexto_de_usuario, es_admin_talleres, taller_de_usuario

    empresa_id = getattr(empresa, 'id', empresa)
    try:
        empresa_id = int(empresa_id) if empresa_id is not None else None
    except (TypeError, ValueError):
        empresa_id = None

    contexto = contexto_de_usuario(user, empresa_id)
    if es_admin_talleres(contexto):
        return None
    return taller_de_usuario(user, empresa_id)


def _taller_publico(taller_pk):
    """ID público del taller de sesión (la API no expone la pk)."""
    if not taller_pk:
        return None
    return id_publico(Taller.objects.filter(pk=taller_pk).only('uuid').first())


def _talleres_payload(user, empresa):
    """Talleres permitidos para `user` en `empresa`, con marca de predeterminado.

    Se incluye en la respuesta del login (cuando el usuario debe elegir empresa)
    para que el selector de taller funcione antes de tener sesión autenticada.
    La administración no marca predeterminado: su opción por defecto es «Todos».
    """
    from .utils import contexto_de_usuario, es_admin_talleres, taller_de_usuario, talleres_permitidos

    empresa_id = getattr(empresa, 'id', empresa)
    contexto = contexto_de_usuario(user, empresa_id)
    if not contexto.hay_contexto:
        return []

    predeterminado = None if es_admin_talleres(contexto) else taller_de_usuario(user, empresa_id)
    return [
        {
            'id': id_publico(taller),
            'nombre': taller.nombre,
            'ciudad': taller.ciudad or '',
            'codigo_sucursal': taller.codigo_sucursal or '',
            'predeterminado': taller.pk == predeterminado,
        }
        for taller in talleres_permitidos(user, contexto)
    ]


def _user_payload(user, empresa, rol, request=None):
    """Payload de usuario incluyendo datos del perfil (teléfono y avatar absoluto)."""
    from .utils import contexto_de_usuario, es_admin_talleres, talleres_permitidos

    profile = getattr(user, 'profile', None)
    telefono = getattr(profile, 'telefono', '') if profile else ''
    avatar_url = None
    if profile and profile.avatar:
        avatar_url = profile.avatar.url
        if request is not None:
            avatar_url = request.build_absolute_uri(avatar_url)
    base = {
        "id": id_publico(user),
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "rol": rol,
        "es_superusuario": user.is_superuser,
        "telefono": telefono,
        "avatar": avatar_url,
    }
    if empresa is None:
        # Sesión del administrador del SaaS sin empresa elegida (gestión de empresas).
        return {
            **base,
            "empresa_id": None,
            "empresa_nombre": '',
            "taller_nombre": '',
            "taller_id": None,
            "talleres_count": 0,
            "es_admin_talleres": False,
        }
    contexto = contexto_de_usuario(user, empresa.id)
    talleres_count = talleres_permitidos(user, contexto).count() if contexto.hay_contexto else 0
    return {
        **base,
        "empresa_id": id_publico(empresa),
        "empresa_nombre": getattr(empresa, 'nombre_comercial', getattr(empresa, 'nombre', '')),
        "taller_nombre": _taller_sesion_nombre(user, empresa),
        "taller_id": _taller_publico(_taller_sesion_id(user, empresa)),
        "talleres_count": talleres_count,
        "es_admin_talleres": es_admin_talleres(contexto),
    }

# --- Token de selección de empresa ---------------------------------------
# El login multiempresa no entrega JWT hasta que el usuario elige empresa; en su
# lugar emite este token firmado y de vida corta que `select-company` canjea.
# Así el endpoint no confía en un `user_id` enviado por el cliente.
SELECCION_EMPRESA_SALT = 'gonmotor.auth.seleccion-empresa'
SELECCION_EMPRESA_MAX_AGE = 300  # segundos


def _huella_password(user):
    """Huella del hash de contraseña: cambiarla invalida los tokens emitidos."""
    return salted_hmac(SELECCION_EMPRESA_SALT, user.password).hexdigest()[:16]


def emitir_token_seleccion(user):
    return signing.dumps(
        {'uid': user.pk, 'pwd': _huella_password(user)}, salt=SELECCION_EMPRESA_SALT
    )


def usuario_de_token_seleccion(token):
    """Usuario activo dueño del token, o None si es inválido, expiró o caducó."""
    if not token or not isinstance(token, str):
        return None
    try:
        datos = signing.loads(
            token, salt=SELECCION_EMPRESA_SALT, max_age=SELECCION_EMPRESA_MAX_AGE
        )
    except signing.BadSignature:  # incluye SignatureExpired
        return None
    user = User.objects.filter(pk=datos.get('uid'), is_active=True).first()
    if user is None or not constant_time_compare(datos.get('pwd', ''), _huella_password(user)):
        return None
    return user


def _es_cuenta_desactivada(username, password):
    """True si las credenciales son correctas pero la cuenta está inactiva.

    `authenticate()` devuelve None tanto para una contraseña errónea como para
    un usuario inactivo. Solo se distingue el segundo caso cuando la contraseña
    coincide, para no revelar qué correos existen a quien no la conoce.
    """
    candidatos = User.objects.filter(
        Q(username__iexact=username) | Q(email__iexact=username),
        is_active=False,
    )
    return any(candidato.check_password(password) for candidato in candidatos)


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
            if _es_cuenta_desactivada(username, password):
                raise serializers.ValidationError({
                    "detail": "Tu cuenta está desactivada. Contacta al administrador de tu empresa para reactivarla."
                })
            raise serializers.ValidationError({
                "detail": "Correo o contraseña incorrectos. Por favor, verifica tus datos e intenta nuevamente."
            })

        self.user = user

        # Administrador del SaaS (superusuario): siempre elige entre ir a una
        # empresa (todas las activas, aunque sea una o ninguna) o gestionar
        # empresas. Para lo segundo recibe tokens sin empresa en sus claims.
        if user.is_superuser:
            empresas_qs = Empresa.objects.filter(is_active=True).order_by('nombre_comercial')
            empresas_list = [
                {
                    "id": id_publico(emp),
                    "nombre_comercial": emp.nombre_comercial,
                    "ruc": emp.ruc,
                    "rol": 'ADMIN_SISTEMA',
                    "talleres": _talleres_payload(user, emp),
                }
                for emp in empresas_qs
            ]
            refresh = self.get_token(user)
            refresh['rol'] = 'ADMIN_SISTEMA'
            return {
                "requires_company_selection": True,
                "es_admin_saas": True,
                "selection_token": emitir_token_seleccion(user),
                "empresas": empresas_list,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": _user_payload(user, None, 'ADMIN_SISTEMA', self.context.get('request')),
            }

        relaciones = UsuarioEmpresa.objects.filter(
            user=user,
            is_active=True,
            tiene_acceso=True,
            empresa__is_active=True
        ).select_related('empresa')
        
        if not relaciones.exists():
            raise serializers.ValidationError({
                "detail": "El usuario no tiene empresas asignadas o activas."
            })
        
        if relaciones.count() == 1:
            relacion = relaciones.first()
            empresa = relacion.empresa
            from apps.core.auditoria import registrar_acceso
            if self.context.get('request') is not None:
                registrar_acceso(self.context['request'], user, empresa)
            
            refresh = self.get_token_with_empresa(user, empresa, relacion.rol)
            
            data = {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'requires_company_selection': False,
                'user': _user_payload(user, empresa, relacion.rol, self.context.get('request')),
            }
            return data

        empresas_list = [
            {
                "id": id_publico(rel.empresa),
                "nombre_comercial": getattr(rel.empresa, 'nombre_comercial', getattr(rel.empresa, 'nombre', '')),
                "ruc": getattr(rel.empresa, 'ruc', ''),
                "rol": rel.rol,
                "talleres": _talleres_payload(user, rel.empresa),
            }
            for rel in relaciones
        ]

        return {
            "requires_company_selection": True,
            "selection_token": emitir_token_seleccion(user),
            "empresas": empresas_list
        }

    @classmethod
    def get_token_with_empresa(cls, user, empresa, rol):
        token = super().get_token(user)
        # 🔒 Empresa (ID público: el payload del JWT es legible) y rol en la firma.
        token['empresa_id'] = id_publico(empresa)
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
            raise serializers.ValidationError({"token": "El token es inválido o ha expirado (expira en 1 hora)."})

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
    avatar = ImagenProcesadaField(perfil='avatar', required=False, allow_null=True)
    remove_avatar = serializers.BooleanField(required=False, default=False)

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

        remove_avatar = validated_data.pop('remove_avatar', False)
        avatar = validated_data.pop('avatar', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        if profile_data or remove_avatar or avatar is not None:
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            if 'telefono' in profile_data:
                profile.telefono = profile_data['telefono']
            if remove_avatar or avatar is None and 'avatar' in self.initial_data:
                # El archivo se borra al confirmar la transacción (apps/core/archivos.py).
                profile.avatar = None
            elif avatar is not None:
                profile.avatar = avatar
            profile.save()

        return instance


class UserProfileSerializer(ModelSerializerPublico):
    """Perfil global.

    `rol`, `empresa` y `talleres` viven en `UsuarioEmpresa`, no en el perfil:
    declararlos aquí hacía estallar el serializer con `ImproperlyConfigured`.
    """

    avatar = ImagenProcesadaField(perfil='avatar', required=False, allow_null=True)

    class Meta:
        model = UserProfile
        fields = ['telefono', 'identificacion', 'direccion', 'avatar', 'taller_activo']

    def validate_taller_activo(self, value):
        if value is None:
            return value
        from .utils import get_contexto_desde_request, talleres_permitidos

        request = self.context.get('request')
        contexto = get_contexto_desde_request(request) if request else None
        if not contexto or not contexto.hay_contexto:
            raise serializers.ValidationError(
                'No se pudo determinar la empresa activa.'
            )
        if value.empresa_id != contexto.empresa_id:
            raise serializers.ValidationError(
                'El taller no pertenece a la empresa activa.'
            )
        if not talleres_permitidos(request.user, contexto).filter(pk=value.pk).exists():
            raise serializers.ValidationError(
                'El usuario no tiene acceso a ese taller.'
            )
        return value


class UserAdminSerializer(ModelSerializerPublico):
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

        if profile_data:
            perfil, _ = UserProfile.objects.get_or_create(user=user)
            for attr, val in profile_data.items():
                setattr(perfil, attr, val)
            perfil.save()

        return user

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', {})
        password = validated_data.pop('password', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()

        if profile_data:
            perfil, _ = UserProfile.objects.get_or_create(user=instance)
            for attr, val in profile_data.items():
                setattr(perfil, attr, val)
            perfil.save()

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
    comision_porcentaje = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False,
    )

    def validate_comision_porcentaje(self, value):
        from .permissions import TienePermiso
        request = self.context.get('request')
        if not request or not TienePermiso('empleados', 'modificar')().has_permission(request, None):
            raise serializers.ValidationError('No tienes permiso para configurar comisiones de empleados.')
        return value

    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    telefono = serializers.CharField(required=False, allow_blank=True, default='')
    identificacion = serializers.CharField(required=False, allow_blank=True, default='', max_length=13)
    direccion = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    rol = serializers.CharField(max_length=50, required=False, allow_null=True)
    talleres = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )
    is_active = serializers.BooleanField(default=True, required=False)
    tiene_acceso = serializers.BooleanField(default=True, required=False)

    def validate_talleres(self, value):
        if not value:
            return value
        from .utils import get_contexto_desde_request, validar_talleres_asignables

        request = self.context.get('request')
        contexto = get_contexto_desde_request(request) if request else None
        if not contexto or not contexto.hay_contexto:
            raise serializers.ValidationError(
                'No se pudo determinar la empresa activa.'
            )
        permitidos, rechazados = validar_talleres_asignables(request.user, contexto, value)
        if rechazados:
            raise serializers.ValidationError(
                'Algunos talleres no pertenecen a la empresa activa o están inactivos.'
            )
        # A partir de aquí se trabaja con pks internas (M2M.set()).
        return permitidos

    def validate_rol(self, value):
        from .permisos_repo import _rol_por_codigo, puede_otorgar_bd
        from .utils import get_contexto_desde_request

        if value is None:
            return value
        request = self.context.get('request')
        if not request or not getattr(request, 'user', None):
            return value
        if request.user.is_authenticated and request.user.is_superuser:
            return value
        rol = _rol_por_codigo(value)
        if rol is None or not rol.is_active:
            raise serializers.ValidationError('El rol indicado no existe o está inactivo.')
        contexto = get_contexto_desde_request(request)
        if contexto.hay_contexto and not puede_otorgar_bd(contexto.rol, value):
            raise serializers.ValidationError(
                'No puedes asignar un rol igual o superior al tuyo.'
            )
        return value

    def validate(self, attrs):
        # Persona sin acceso: no lleva rol ni talleres y puede omitirlos.
        if attrs.get('tiene_acceso') is False:
            attrs['rol'] = None
            attrs['talleres'] = []
            return attrs
        # Con acceso el rol es obligatorio (alta siempre; baja solo si se
        # envía explícitamente, para no romper ediciones parciales).
        if attrs.get('rol') in (None, ''):
            if self.instance is None or 'rol' in attrs:
                raise serializers.ValidationError({'rol': 'Indica el rol del usuario.'})
        return attrs

    def to_representation(self, instance):
        user = instance.user
        profile = getattr(user, 'profile', None)
        return {
            'id': id_publico(instance),
            'user': {
                'id': id_publico(user),
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_active': user.is_active,
                'telefono': getattr(profile, 'telefono', '') if profile else '',
                'identificacion': getattr(profile, 'identificacion', '') if profile else '',
                'direccion': getattr(profile, 'direccion', '') if profile else '',
            },
            'empresa': {
                'id': id_publico(instance.empresa),
                'nombre': getattr(instance.empresa, 'nombre_comercial', getattr(instance.empresa, 'nombre', '')) if instance.empresa_id else None,
            } if instance.empresa_id else None,
            'comision_porcentaje': str(instance.comision_porcentaje),
            'rol': instance.rol,
            'rol_display': nombre_rol(instance.rol),
            'tiene_acceso': instance.tiene_acceso,
            'talleres': [
                {'id': id_publico(t), 'nombre': t.nombre}
                for t in instance.talleres.all()
            ],
            'is_active': instance.is_active,
        }


class EmpleadoReadSerializer(ModelSerializerPublico):
    user = serializers.SerializerMethodField()
    talleres = serializers.SerializerMethodField()
    rol_display = serializers.SerializerMethodField()

    class Meta:
        model = UsuarioEmpresa
        fields = ['id', 'user', 'empresa', 'rol', 'rol_display', 'talleres', 'is_active', 'tiene_acceso', 'comision_porcentaje']

    def get_user(self, obj):
        profile = getattr(obj.user, 'profile', None)
        return {
            'id': id_publico(obj.user),
            'username': obj.user.username,
            'email': obj.user.email,
            'first_name': obj.user.first_name,
            'last_name': obj.user.last_name,
            'is_active': obj.user.is_active,
            'telefono': getattr(profile, 'telefono', '') if profile else '',
            'identificacion': getattr(profile, 'identificacion', '') if profile else '',
            'direccion': getattr(profile, 'direccion', '') if profile else '',
        }

    def get_rol_display(self, obj):
        return nombre_rol(obj.rol)

    def get_talleres(self, obj):
        return [
            {'id': id_publico(t), 'nombre': t.nombre}
            for t in obj.talleres.all()
        ]
