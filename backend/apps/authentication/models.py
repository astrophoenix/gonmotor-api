from django.db import models
from django.contrib.auth.models import User
from apps.core.models import BaseModel


class UserProfile(BaseModel):
    """
    Perfil global del usuario (datos personales / preferencias).
    Las empresas y roles específicos se manejan en UsuarioEmpresa.
    """
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='profile'
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default=""
    )
    identificacion = models.CharField(
        max_length=13,
        blank=True,
        default="",
        verbose_name="Identificación (Cédula/RUC/Pasaporte)"
    )
    direccion = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Dirección"
    )
    avatar = models.ImageField(
        upload_to='perfiles/avatares/',
        blank=True,
        null=True,
        verbose_name="Foto de perfil"
    )
    # Taller en el que está trabajando actualmente durante su sesión actual
    taller_activo = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usuarios_activos',
        verbose_name="Taller Activo en Sesión"
    )

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuarios"

    def __str__(self):
        return f"Perfil de {self.user.username}"


class UsuarioEmpresa(BaseModel):
    """
    Tabla intermedia que permite a un usuario pertenecer a N Empresas (RUCs distintos)
    y tener un rol específico en cada una de ellas.
    """
    ROLES = [
        ('ADMIN_SISTEMA', 'Superadmin SaaS'),
        ('ADMIN_EMPRESA', 'Administrador'),
        ('ADMIN_TALLER', 'Jefe de Taller'),
        ('ASESOR', 'Asesor'),
        ('MECANICO', 'Mecánico'),
        ('CAJERO', 'Cajero'),
    ]

    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='empresas_asociadas'
    )
    empresa = models.ForeignKey(
        'empresas.Empresa', 
        on_delete=models.CASCADE, 
        related_name='usuarios_asociados'
    )
    rol = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        default='MECANICO',
        verbose_name="Rol",
        help_text=(
            "Código del rol (sistema o personalizado de la empresa). "
            "Solo aplica si el empleado tiene acceso al sistema."
        ),
    )
    tiene_acceso = models.BooleanField(
        default=True,
        verbose_name="¿Tiene acceso al sistema?",
        help_text="Si se desactiva, la persona existe como empleado pero no puede iniciar sesión."
    )
    # Sucursales de ESTA empresa específica a las que tiene acceso el usuario
    talleres = models.ManyToManyField(
        'empresas.Taller', 
        related_name='usuarios_asignados',
        blank=True,
        verbose_name="Talleres Asignados"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Acceso Activo"
    )

    class Meta:
        verbose_name = "Asignación Usuario-Empresa"
        verbose_name_plural = "Asignaciones Usuarios-Empresas"
        # Evita duplicar la relación usuario - empresa
        unique_together = ('user', 'empresa')

    def __str__(self):
        return f"{self.user.username} - {self.empresa} ({self.rol or 'Sin acceso'})"


class RolPermiso(models.Model):
    """Permiso concreto (recurso, accion) concedido a un rol.

    Se siembra desde `permisos_repo.MATRIZ_ROLES` tras el migrate y se edita
    por checkboxes desde la pantalla de roles (solo rol ADMIN_SISTEMA).
    `permisos_repo` conserva la línea base y los niveles; esta tabla es la
    fuente de verdad en tiempo de ejecución.
    """
    rol = models.CharField(
        max_length=20,
        choices=UsuarioEmpresa.ROLES,
        verbose_name="Rol"
    )
    recurso = models.CharField(
        max_length=50,
        verbose_name="Recurso"
    )
    accion = models.CharField(
        max_length=50,
        verbose_name="Acción"
    )

    class Meta:
        verbose_name = "Permiso de rol"
        verbose_name_plural = "Permisos de roles"
        unique_together = ('rol', 'recurso', 'accion')

    def __str__(self):
        return f'{self.rol}: {self.recurso}.{self.accion}'


class Rol(BaseModel):
    """Catálogo de roles: los 6 de sistema y los personalizados por empresa.

    `UsuarioEmpresa.rol` guarda el `codigo` (compatible con los códigos
    históricos de los roles de sistema); esta tabla aporta el resto de la
    semántica (nombre, nivel, alcance de talleres y dueño).
    """
    codigo = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Código",
        help_text="Identificador estable del rol (los de sistema conservan sus códigos históricos)."
    )
    nombre = models.CharField(
        max_length=120,
        verbose_name="Nombre"
    )
    descripcion = models.TextField(
        blank=True,
        default="",
        verbose_name="Descripción"
    )
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='roles',
        verbose_name="Empresa propietaria",
        help_text="Solo los roles personalizados pertenecen a una empresa."
    )
    es_sistema = models.BooleanField(
        default=False,
        verbose_name="Rol del sistema"
    )
    nivel = models.PositiveIntegerField(
        default=1,
        verbose_name="Nivel jerárquico"
    )
    ver_todos_talleres = models.BooleanField(
        default=False,
        verbose_name="Ver todos los talleres"
    )

    class Meta:
        verbose_name = "Rol"
        verbose_name_plural = "Roles"
        ordering = ['es_sistema', 'nivel', 'nombre']

    def __str__(self):
        return self.nombre


class Recurso(BaseModel):
    """Catálogo central de recursos: un módulo o una funcionalidad.

    Un recurso puede ser un módulo completo (p. ej. `clientes`) o una
    funcionalidad dentro de una pantalla (p. ej. `clientes.anular`). La
    dualidad se expresa con `tipo` y `padre`.
    """
    TIPOS = [
        ('MODULO', 'Módulo'),
        ('FUNCIONALIDAD', 'Funcionalidad'),
    ]

    codigo = models.CharField(
        max_length=60,
        unique=True,
        verbose_name="Código"
    )
    nombre = models.CharField(
        max_length=120,
        verbose_name="Nombre"
    )
    tipo = models.CharField(
        max_length=20,
        choices=TIPOS,
        default='MODULO',
        verbose_name="Tipo"
    )
    padre = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='hijos',
        verbose_name="Recurso padre"
    )
    orden = models.PositiveIntegerField(
        default=0,
        verbose_name="Orden"
    )

    class Meta:
        verbose_name = "Recurso"
        verbose_name_plural = "Recursos"
        ordering = ['orden', 'codigo']

    def __str__(self):
        return self.nombre


class Permiso(BaseModel):
    """Permisos generales (ver/modificar) de un rol sobre un recurso."""

    rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name='permisos',
        verbose_name="Rol"
    )
    recurso = models.ForeignKey(
        Recurso,
        on_delete=models.CASCADE,
        related_name='permisos',
        verbose_name="Recurso"
    )
    ver = models.BooleanField(default=False, verbose_name="Ver")
    modificar = models.BooleanField(default=False, verbose_name="Modificar")

    class Meta:
        verbose_name = "Permiso de rol"
        verbose_name_plural = "Permisos de roles"
        unique_together = ('rol', 'recurso')

    def __str__(self):
        return f'{self.rol.codigo}: {self.recurso.codigo} (ver={self.ver}, modificar={self.modificar})'


class AccionEspecial(BaseModel):
    """Catálogo central de acciones especiales por recurso.

    El cliente no crea ni edita este catálogo: se alimenta desde el código
    del sistema y sirve para conceder (por rol) acciones que no caben en la
    grilla ver/modificar (p. ej. "Anular cliente").
    """
    codigo = models.CharField(
        max_length=60,
        unique=True,
        verbose_name="Código"
    )
    nombre = models.CharField(
        max_length=120,
        verbose_name="Nombre"
    )
    descripcion = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Descripción"
    )
    recurso = models.ForeignKey(
        Recurso,
        on_delete=models.CASCADE,
        related_name='acciones_especiales',
        verbose_name="Recurso"
    )

    class Meta:
        verbose_name = "Acción especial"
        verbose_name_plural = "Acciones especiales"
        ordering = ['recurso__orden', 'nombre']

    def __str__(self):
        return self.nombre


class PermisoEspecial(BaseModel):
    """Acciones especiales concedidas a un rol."""

    rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name='permisos_especiales',
        verbose_name="Rol"
    )
    accion = models.ForeignKey(
        AccionEspecial,
        on_delete=models.CASCADE,
        related_name='roles',
        verbose_name="Acción especial"
    )

    class Meta:
        verbose_name = "Permiso especial de rol"
        verbose_name_plural = "Permisos especiales de roles"
        unique_together = ('rol', 'accion')

    def __str__(self):
        return f'{self.rol.codigo}: {self.accion.codigo}'


class PermisoEmpresa(BaseModel):
    """Overrides de ver/modificar de un rol de sistema por empresa.

    Si no hay fila para (empresa, rol, recurso) la empresa hereda el valor
    global de `Permiso`. Si hay fila, ese valor reemplaza al global: permite
    conceder o denegar recurso por recurso dentro de una sola empresa sin
    tocar la matriz que comparten las demás. Los roles personalizados no
    usan esta tabla: sus permisos ya viven por rol.
    """
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='permisos_overrides',
        verbose_name="Empresa"
    )
    rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name='permisos_empresa',
        verbose_name="Rol"
    )
    recurso = models.ForeignKey(
        Recurso,
        on_delete=models.CASCADE,
        related_name='permisos_empresa',
        verbose_name="Recurso"
    )
    ver = models.BooleanField(default=False, verbose_name="Ver")
    modificar = models.BooleanField(default=False, verbose_name="Modificar")

    class Meta:
        verbose_name = "Permiso de rol por empresa"
        verbose_name_plural = "Permisos de roles por empresa"
        unique_together = ('empresa', 'rol', 'recurso')

    def __str__(self):
        return f'{self.empresa_id}:{self.rol.codigo}:{self.recurso.codigo} (ver={self.ver}, modificar={self.modificar})'


class PermisoEspecialEmpresa(BaseModel):
    """Overrides de acciones especiales de un rol de sistema por empresa.

    La fila explicita `permitido` (concede o deniega) sobre la herencia del
    global `PermisoEspecial`. Ausencia de fila = hereda el global. Así la
    empresa puede tanto conceder como revocar una acción dentro de su cuenta.
    """
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='permisos_especiales_overrides',
        verbose_name="Empresa"
    )
    rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name='permisos_especiales_empresa',
        verbose_name="Rol"
    )
    accion = models.ForeignKey(
        AccionEspecial,
        on_delete=models.CASCADE,
        related_name='roles_empresa',
        verbose_name="Acción especial"
    )
    permitido = models.BooleanField(default=True, verbose_name="Permitido")

    class Meta:
        verbose_name = "Permiso especial de rol por empresa"
        verbose_name_plural = "Permisos especiales de roles por empresa"
        unique_together = ('empresa', 'rol', 'accion')

    def __str__(self):
        return f'{self.empresa_id}:{self.rol.codigo}:{self.accion.codigo} permitido={self.permitido}'