# apps/core/models.py
"""Modelos base abstractos: auditoría, borrado lógico y alcance multi-tenant.

Estados de un registro soft-deletable:

| Estado     | is_active | deleted_at | Listados del día a día | Bloquea duplicados |
|------------|-----------|------------|------------------------|--------------------|
| Operativo  | True      | NULL       | sí                     | sí                 |
| Suspendido | False     | NULL       | con ?estado=inactivo   | sí                 |
| Eliminado  | False     | timestamp  | no (solo all_objects)  | no                 |

- `objects` (manager por defecto) oculta los eliminados y conserva los
  suspendidos: los flujos de "reactivar" y `?estado=inactivo` siguen igual.
- `all_objects` incluye eliminados (numeración de documentos, admin, auditoría).
- `delete()` (instancia o queryset) es siempre lógico; el borrado físico
  requiere `hard_delete()` explícito.
- El acceso por FK hacia delante usa `_base_manager` (sin filtro), así un
  documento sigue resolviendo su cliente o vehículo aunque se haya eliminado.
"""
from django.db import models, transaction
from django.dispatch import Signal
from django.utils import timezone
from django.conf import settings
from .ids_publicos import UuidPublicoModel


class RegistroAuditoria(UuidPublicoModel):
    """Bitácora de operaciones HTTP exitosas; sin cuerpos ni credenciales.

    Los nombres se conservan aunque el usuario o taller se eliminen. No se
    publica ninguna operación de escritura sobre esta tabla en la API.
    """
    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.PROTECT)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    usuario_nombre = models.CharField(max_length=200)
    taller = models.ForeignKey('empresas.Taller', null=True, on_delete=models.SET_NULL)
    taller_nombre = models.CharField(max_length=150, blank=True)
    fecha = models.DateTimeField(default=timezone.now, editable=False)
    modulo = models.CharField(max_length=60)
    accion = models.CharField(max_length=100)
    registro_id = models.CharField(max_length=100, blank=True)
    metodo = models.CharField(max_length=10)

    class Meta:
        ordering = ['-fecha', '-id']
        indexes = [
            models.Index(fields=['empresa', '-fecha'], name='audit_empresa_fecha'),
            models.Index(fields=['empresa', 'modulo', '-fecha'], name='audit_empresa_modulo'),
        ]


class EventoDocumento(UuidPublicoModel):
    """Historial de cambios de un documento del flujo (ver `historial_documentos`).

    Complementa a `RegistroAuditoria`: aquí sí se guarda el valor anterior y el
    nuevo, pero solo de campos sensibles y solo cuando cambian. Las filas de un
    mismo guardado comparten `operacion`. Sin escritura desde la API.
    """

    class TipoDocumento(models.TextChoices):
        RECEPCION = 'recepcion', 'Recepción'
        INSPECCION = 'inspeccion', 'Inspección'
        COTIZACION = 'cotizacion', 'Cotización'
        ORDEN = 'orden', 'Orden de trabajo'

    class Evento(models.TextChoices):
        CREADO = 'creado', 'Creado'
        CAMPO = 'campo', 'Campo modificado'
        ESTADO = 'estado', 'Cambio de estado'
        LINEA_AGREGADA = 'linea_agregada', 'Línea agregada'
        LINEA_EDITADA = 'linea_editada', 'Línea modificada'
        LINEA_ELIMINADA = 'linea_eliminada', 'Línea eliminada'
        SINCRONIZADO = 'sincronizado', 'Sincronizado'
        COTIZACION_APLICADA = 'cotizacion_aplicada', 'Cotización aplicada'
        VINCULO = 'vinculo', 'Vínculo del flujo'
        FOTO_REEMPLAZADA = 'foto_reemplazada', 'Foto reemplazada'
        FOTO_ELIMINADA = 'foto_eliminada', 'Foto eliminada'
        PAGO = 'pago', 'Pago registrado'
        DEVOLUCION = 'devolucion', 'Devolución registrada'
        PAGO_ANULADO = 'pago_anulado', 'Pago anulado'
        DESPACHO = 'despacho', 'Repuesto despachado'
        DEVOLUCION_ALMACEN = 'devolucion_almacen', 'Repuesto devuelto al almacén'
        ELIMINADO = 'eliminado', 'Eliminado'

    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.PROTECT)
    taller = models.ForeignKey('empresas.Taller', null=True, on_delete=models.SET_NULL)
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices)
    documento = models.UUIDField(help_text='ID público del documento')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    usuario_nombre = models.CharField(max_length=200)
    fecha = models.DateTimeField(default=timezone.now, editable=False)
    operacion = models.UUIDField(help_text='Agrupa los eventos de un mismo guardado')
    evento = models.CharField(max_length=30, choices=Evento.choices)
    etiqueta = models.CharField(max_length=200, blank=True)
    valor_anterior = models.CharField(max_length=255, blank=True)
    valor_nuevo = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        indexes = [
            models.Index(fields=['empresa', 'tipo_documento', 'documento', '-fecha'], name='evento_doc_fecha'),
        ]

# Se emite tras un borrado lógico de instancia (kwargs: instance, deleted_at).
# Sirve para efectos laterales dentro del agregado sin acoplar los modelos.
post_soft_delete = Signal()


class BaseModel(models.Model):
    """
    Modelo base abstracto que provee auditoría y estado para todas las tablas.

    No tiene borrado lógico: lo usan catálogos y configuración de acceso
    (roles, permisos, perfiles), que se borran y recrean físicamente.
    """
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activo",
        help_text="Indica si el registro está activo o fue deshabilitado/eliminado lógicamente"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Fecha de creación"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Fecha de última modificación"
    )

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    """Filtros por estado y borrado lógico masivo."""

    def vivos(self):
        """Operativos y suspendidos (todo lo no eliminado)."""
        return self.filter(deleted_at__isnull=True)

    def operativos(self):
        return self.filter(is_active=True, deleted_at__isnull=True)

    def suspendidos(self):
        return self.filter(is_active=False, deleted_at__isnull=True)

    def eliminados(self):
        return self.filter(deleted_at__isnull=False)

    def delete(self):
        """Borrado lógico masivo.

        Si el modelo declara `soft_delete_cascade` se recorre instancia a
        instancia para propagar la cascada y emitir `post_soft_delete`.
        """
        if getattr(self.model, 'soft_delete_cascade', ()):
            total = 0
            with transaction.atomic(using=self.db):
                for instancia in self.vivos():
                    instancia.delete()
                    total += 1
            return total, {self.model._meta.label: total}
        ahora = timezone.now()
        total = self.vivos().update(is_active=False, deleted_at=ahora, updated_at=ahora)
        return total, {self.model._meta.label: total}

    delete.alters_data = True
    delete.queryset_only = True

    def hard_delete(self):
        """Borrado físico (dispara los `on_delete` de la base)."""
        return super().delete()

    hard_delete.alters_data = True
    hard_delete.queryset_only = True

    def restaurar(self):
        return self.eliminados().update(
            is_active=True, deleted_at=None, updated_at=timezone.now()
        )

    restaurar.alters_data = True


class TenantSoftDeleteQuerySet(SoftDeleteQuerySet):
    """Recorte multi-tenant explícito.

    El contexto (empresa y talleres permitidos) se obtiene SIEMPRE de
    `apps/authentication/utils.py`; aquí solo se compone el filtro.
    """

    def de_empresa(self, empresa_id):
        """Registros de la empresa; sin empresa no se devuelve nada."""
        if not empresa_id:
            return self.none()
        lookup = getattr(self.model, 'empresa_lookup', None)
        if not lookup:
            raise NotImplementedError(
                f'{self.model.__name__} no define `empresa_lookup`.'
            )
        return self.filter(**{lookup: empresa_id})

    def de_talleres(self, talleres):
        """Recorte por taller: None = sin recorte, [] = sin datos, lista = recorte."""
        campo = getattr(self.model, 'taller_field', None)
        if not campo or talleres is None:
            return self
        if not talleres:
            return self.none()
        return self.filter(**{f'{campo}_id__in': talleres})

    def para_request(self, request, *, explicito=None):
        """Empresa activa + talleres permitidos de la petición.

        `explicito` es el taller pedido por query param (tiene prioridad sobre
        la cabecera `X-Taller-ID`); se valida igual contra el alcance del rol.
        """
        from apps.authentication.utils import (
            get_empresa_id_desde_request,
            talleres_filtro_de_request,
        )

        empresa_id = get_empresa_id_desde_request(request)
        queryset = self.de_empresa(empresa_id)
        if empresa_id and getattr(self.model, 'taller_field', None):
            queryset = queryset.de_talleres(
                talleres_filtro_de_request(request, explicito=explicito)
            )
        return queryset


class TenantSoftDeleteManager(models.Manager.from_queryset(TenantSoftDeleteQuerySet)):
    """Manager por defecto: oculta eliminados, conserva suspendidos."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class AllObjectsManager(models.Manager.from_queryset(TenantSoftDeleteQuerySet)):
    """Incluye eliminados: numeración, auditoría, admin y restauración."""


class SoftDeleteModel(BaseModel):
    """Registro con borrado lógico híbrido (`is_active` + `deleted_at`)."""

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Fecha de eliminación"
    )

    # Relaciones (managers inversos) del mismo agregado que se eliminan
    # lógicamente junto con el padre. Solo modelos soft-deletables.
    soft_delete_cascade = ()

    objects = TenantSoftDeleteManager()  # primer manager = _default_manager
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    @property
    def esta_eliminado(self):
        return self.deleted_at is not None

    @property
    def esta_operativo(self):
        return self.is_active and self.deleted_at is None

    def delete(self, using=None, keep_parents=False):
        """Borrado lógico: `is_active=False` + `deleted_at=now()`."""
        if self.deleted_at is not None:
            return 0, {}
        with transaction.atomic(using=using):
            for relacion in self.soft_delete_cascade:
                getattr(self, relacion).all().delete()
            self.is_active = False
            self.deleted_at = timezone.now()
            self.save(using=using, update_fields=['is_active', 'deleted_at', 'updated_at'])
            post_soft_delete.send(sender=type(self), instance=self, deleted_at=self.deleted_at)
        return 1, {self._meta.label: 1}

    def hard_delete(self, using=None, keep_parents=False):
        """Borrado físico explícito (admin, comandos de mantenimiento)."""
        return super().delete(using=using, keep_parents=keep_parents)

    def restaurar(self):
        self.is_active = True
        self.deleted_at = None
        self.save(update_fields=['is_active', 'deleted_at', 'updated_at'])


class BaseEmpresaModel(SoftDeleteModel):
    """Datos de la empresa compartidos entre sus talleres.

    No declara la FK `empresa`: cada modelo conserva la suya (con su
    `related_name` actual) para no romper relaciones inversas.
    """
    empresa_lookup = 'empresa_id'

    class Meta:
        abstract = True


class BaseTallerModel(BaseEmpresaModel):
    """Documentos transaccionales privados de un taller (sucursal)."""
    taller_field = 'sucursal'  # Cita y Gasto lo sobrescriben con 'taller'

    class Meta:
        abstract = True
