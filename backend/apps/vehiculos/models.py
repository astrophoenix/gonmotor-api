from django.db import models
from django.utils import timezone
from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import (
    AllObjectsManager,
    BaseEmpresaModel,
    BaseModel,
    SoftDeleteModel,
    TenantSoftDeleteManager,
    TenantSoftDeleteQuerySet,
)
from django_countries.fields import CountryField
from django.core.validators import MinValueValidator, MaxValueValidator
import datetime

# Mapeo de categorías a grupos de blueprints
GRUPO_BLUEPRINT_MAP = {
    # Livianos
    'AUTO': 'liviano',
    # SUV / Crossovers
    'JEEP': 'suv',
    # Camionetas
    'CAMN': 'camioneta',
    # Furgones
    'FURG': 'furgoneta',
    # Motos y similares
    'MOTO': 'motos',
    'MTNA': 'motos',
    'TRIC': 'motos',
    'CUAT': 'motos',
    # Buses
    'BUS': 'bus',
    'BUSE': 'bus',
    'MICR': 'bus',
    # Pesados
    'CAMI': 'pesado',
    'TRAC': 'pesado',
    'VOLQ': 'pesado',
    # Especiales
    'REMO': 'especial',
    'MAGR': 'especial',
    'MCAM': 'especial',
}
class VehiculoQuerySet(TenantSoftDeleteQuerySet):
    def de_empresa(self, empresa_id):
        """Vehículos con vínculo **no eliminado** a la empresa.

        El vehículo físico es global (placa única); cada empresa lo "elimina"
        borrando lógicamente su `VehiculoEmpresa`, sin afectar a las demás.
        Incluye vínculos suspendidos: el estado por empresa se filtra con
        `operativos_en` / `suspendidos_en`.
        """
        if not empresa_id:
            return self.none()
        return self.filter(
            pk__in=VehiculoEmpresa.objects.filter(empresa_id=empresa_id).values('vehiculo_id')
        )

    def operativos_en(self, empresa_id):
        """Vehículo activo globalmente y con vínculo activo en la empresa."""
        if not empresa_id:
            return self.none()
        return self.filter(
            is_active=True,
            deleted_at__isnull=True,
            pk__in=VehiculoEmpresa.objects.operativos()
            .filter(empresa_id=empresa_id).values('vehiculo_id'),
        )

    def suspendidos_en(self, empresa_id):
        """Vínculo suspendido en la empresa o vehículo suspendido globalmente."""
        if not empresa_id:
            return self.none()
        return self.de_empresa(empresa_id).filter(
            models.Q(is_active=False)
            | models.Q(
                pk__in=VehiculoEmpresa.objects.suspendidos()
                .filter(empresa_id=empresa_id).values('vehiculo_id')
            )
        )

    def por_estado_en(self, empresa_id, estado):
        """`?estado=activo|inactivo` del listado, evaluado en la empresa activa."""
        if estado == 'inactivo':
            return self.suspendidos_en(empresa_id)
        if estado == 'activo':
            return self.operativos_en(empresa_id)
        return self.de_empresa(empresa_id)


class Vehiculo(UuidPublicoModel, SoftDeleteModel):

    #🚗 Opciones del tipo de transmisión con TextChoices
    class TipoTransmision(models.TextChoices):
        MANUAL = 'M', 'Manual'
        AUTOMATICA = 'A', 'Automática'
        CVT = 'C', 'CVT'

    # ⛽ Opciones del tipo de combustible con TextChoices
    class TipoCombustible(models.TextChoices):
        GASOLINA = 'GAS', 'Gasolina'
        DIESEL = 'DIE', 'Diésel'
        HIBRIDO = 'HIB', 'Híbrido'
        ELECTRICO = 'ELE', 'Eléctrico'
        GNV = 'GNV', 'Gas Natural Vehicular (GNV)' # Opción futura fácil de integrar

    class TipoVehiculo(models.TextChoices):
        # Particular y Pasajeros
        AUTOMOVIL = 'AUTO', 'Automóvil'
        JEEP = 'JEEP', 'Jeep'
        CAMIONETA = 'CAMN', 'Camioneta'
        
        # Dos y Tres Ruedas
        MOTOCICLETA = 'MOTO', 'Motocicleta'
        MOTONETA = 'MTNA', 'Motoneta'
        TRICIMOTO = 'TRIC', 'Tricimoto'
        CUATRIMOTO = 'CUAT', 'Cuatrimoto'
        
        # Transporte Comercial
        BUS = 'BUS', 'Bus / Autobús'
        BUSETA = 'BUSE', 'Buseta'
        MICROBUS = 'MICR', 'Microbús / Furgoneta'
        
        # Carga
        CAMION = 'CAMI', 'Camión'
        TRACTOCAMION = 'TRAC', 'Tractocamión'
        VOLQUETA = 'VOLQ', 'Volqueta'
        FURGON = 'FURG', 'Furgón'
        
        # Especiales
        REMOLQUE = 'REMO', 'Remolque / Semirremolque'
        MAQUINARIA_AGRI = 'MAGR', 'Maquinaria Agrícola'
        MAQUINARIA_CAMI = 'MCAM', 'Maquinaria Caminera'


    placa = models.CharField(
        verbose_name="Placa",
        max_length=10,
        help_text="Ejemplo: PBA1234 (Sin guiones)"
    )
    vin = models.CharField(
        verbose_name="Número de Chasis / VIN",
        max_length=30,
        blank=True,
        default=""
    )
    numero_motor = models.CharField(
        verbose_name="Número de Motor",
        max_length=50,
        blank=True,
        default=""
    )
    marca = models.CharField(
        verbose_name="Marca",
        max_length=50
    )
    modelo = models.CharField(
        verbose_name="Modelo",
        max_length=50
    )
    anio = models.PositiveIntegerField(
        verbose_name="Año de Fabricación",
        blank=True,
        null=True,
        validators=[
            MinValueValidator(1900),
            MaxValueValidator(datetime.date.today().year + 1)
        ]
    )
    color = models.CharField(
        verbose_name="Color",
        max_length=30,
        blank=True,
        default=""
    )
    transmision = models.CharField(
        max_length=1,
        choices=TipoTransmision.choices,
        default=TipoTransmision.MANUAL
    )
    combustible = models.CharField(
        max_length=10,
        choices=TipoCombustible.choices,
        default=TipoCombustible.GASOLINA
    )
    kilometraje_actual = models.PositiveIntegerField(
        verbose_name="Kilometraje Actual",
        default=0
    )
    proximo_mantenimiento_km = models.PositiveIntegerField(
        verbose_name="Próximo mantenimiento (km)",
        blank=True,
        null=True,
        help_text=(
            "Kilometraje en el que corresponde el siguiente mantenimiento "
            "preventivo. Se usa para recordatorios por WhatsApp."
        )
    )
    proxima_mantenimiento_fecha = models.DateField(
        verbose_name="Próximo mantenimiento (fecha)",
        blank=True,
        null=True,
        help_text=(
            "Fecha estimada del siguiente mantenimiento preventivo. Se usa "
            "para recordatorios por WhatsApp."
        )
    )
    observaciones = models.TextField(
        verbose_name="Observaciones",
        blank=True,
        default=""
    )
    tipo = models.CharField(
        max_length=10,
        choices=TipoVehiculo.choices,
        default=TipoVehiculo.AUTOMOVIL,
    )
    pais_origen = CountryField(
        default='EC',
        help_text="País de fabricación o procedencia original"
    )
    imagen = models.ImageField(
        verbose_name="Imagen del Vehículo",
        upload_to='vehiculos/%Y/%m/%d',
        blank=True,
        null=True,
        help_text="Se guarda en WebP, máximo 1600 px por lado."
    )
    miniatura = models.ImageField(upload_to='vehiculos/miniaturas/', blank=True, null=True)
    # Las empresas que trabajan con el vehículo. El vínculo tiene estado
    # propio (VehiculoEmpresa): es lo que cada empresa suspende o elimina.
    # Para escribir usar `vincular_empresa` / `desvincular_empresa`.
    empresas = models.ManyToManyField(
        'empresas.Empresa',
        through='vehiculos.VehiculoEmpresa',
        related_name='vehiculos',
        blank=True,
        verbose_name="Empresas"
    )

    objects = TenantSoftDeleteManager.from_queryset(VehiculoQuerySet)()
    all_objects = AllObjectsManager.from_queryset(VehiculoQuerySet)()

    class Meta:
        verbose_name = "Vehículo"
        verbose_name_plural = "Vehículos"
        ordering = ['-created_at']
        constraints = [
            # La placa identifica al vehículo físico en todo el sistema.
            models.UniqueConstraint(
                fields=['placa'],
                condition=models.Q(deleted_at__isnull=True),
                name='vehiculo_placa_viva_unica',
            ),
        ]

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"

    @property
    def grupo_blueprint(self) -> str:
        """Devuelve la clave del blueprint/diagrama SVG que le corresponde."""
        return GRUPO_BLUEPRINT_MAP.get(self.tipo, 'liviano')
    
    def save(self, *args, **kwargs):
        if self.placa:
            self.placa = self.placa.strip().upper()
        super().save(*args, **kwargs)

    def vinculo_de(self, empresa_id):
        """Vínculo no eliminado con la empresa (o None)."""
        return VehiculoEmpresa.objects.filter(vehiculo=self, empresa_id=empresa_id).first()

    def activo_en(self, empresa_id):
        vinculo = self.vinculo_de(empresa_id)
        return bool(vinculo and vinculo.is_active and self.esta_operativo)


class VehiculoEmpresa(BaseEmpresaModel):
    """Vínculo vehículo–empresa con estado propio.

    Suspender o eliminar un vehículo desde una empresa actúa sobre este
    vínculo; el vehículo físico (y su historial en otras empresas) no cambia.
    Reutiliza la tabla del antiguo M2M automático.
    """
    vehiculo = models.ForeignKey(
        Vehiculo,
        on_delete=models.CASCADE,
        related_name='vinculos_empresa',
    )
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='vinculos_vehiculo',
    )

    class Meta:
        db_table = 'vehiculos_vehiculo_empresas'
        verbose_name = "Vínculo Vehículo-Empresa"
        verbose_name_plural = "Vínculos Vehículo-Empresa"
        constraints = [
            models.UniqueConstraint(
                fields=['vehiculo', 'empresa'],
                condition=models.Q(deleted_at__isnull=True),
                name='vehiculo_empresa_vivo_unico',
            ),
        ]
        indexes = [
            models.Index(
                fields=['empresa', 'vehiculo'],
                condition=models.Q(deleted_at__isnull=True),
                name='vehemp_emp_vivos_idx',
            ),
        ]

    def __str__(self):
        return f"{self.vehiculo_id} @ {self.empresa_id}"


def vincular_empresa(vehiculo, empresa_id, *, activar=True):
    """Crea o restaura el vínculo del vehículo con la empresa.

    Sustituye a `vehiculo.empresas.add()`: si el vínculo fue eliminado se
    restaura (no se duplica) y, con `activar`, un vínculo suspendido se reactiva.
    """
    vinculo = VehiculoEmpresa.objects.filter(vehiculo=vehiculo, empresa_id=empresa_id).first()
    if vinculo is None:
        eliminado = (
            VehiculoEmpresa.all_objects.eliminados()
            .filter(vehiculo=vehiculo, empresa_id=empresa_id)
            .order_by('-deleted_at')
            .first()
        )
        if eliminado is None:
            return VehiculoEmpresa.objects.create(vehiculo=vehiculo, empresa_id=empresa_id)
        eliminado.restaurar()
        return eliminado
    if activar and not vinculo.is_active:
        vinculo.is_active = True
        vinculo.save(update_fields=['is_active', 'updated_at'])
    return vinculo


def propietarios_actuales_en(empresa_id):
    """Relaciones de dueño vigentes cuyo cliente es de `empresa_id`.

    El vehículo es global (placa única) y varias empresas pueden atenderlo,
    pero cada cliente es de una sola empresa: el «dueño actual» es POR EMPRESA.
    Toda lectura del dueño debe pasar por aquí (o filtrar igual) para no
    mostrar ni usar el cliente de otra empresa.
    """
    return VehiculoPropietario.objects.filter(es_actual=True, cliente__empresa_id=empresa_id)


def propietario_actual(vehiculo, empresa_id):
    """Dueño actual del vehículo en `empresa_id` (o None)."""
    if not empresa_id:
        return None
    return propietarios_actuales_en(empresa_id).filter(vehiculo=vehiculo).select_related('cliente').first()


def asignar_propietario_actual(vehiculo, cliente):
    """Deja a `cliente` como único dueño actual del vehículo EN SU EMPRESA.

    Cierra (con fecha de fin) las otras relaciones vigentes de esa misma
    empresa; los dueños de otras empresas no se tocan. Lo usan la pantalla de
    vehículos, el alta desde la ficha del cliente y la importación.
    """
    actuales = vehiculo.propietarios.filter(es_actual=True, cliente__empresa_id=cliente.empresa_id)
    actuales.exclude(cliente=cliente).update(es_actual=False, fecha_fin=timezone.localdate())
    if not actuales.filter(cliente=cliente).exists():
        VehiculoPropietario.objects.update_or_create(
            vehiculo=vehiculo,
            cliente=cliente,
            es_actual=True,
            defaults={'fecha_fin': None},
        )


def sincronizar_empresas(vehiculo, empresa_ids):
    """Equivalente a `vehiculo.empresas.set()` con borrado lógico de los sobrantes."""
    empresa_ids = {int(getattr(e, 'pk', e)) for e in empresa_ids}
    for empresa_id in empresa_ids:
        vincular_empresa(vehiculo, empresa_id, activar=False)
    VehiculoEmpresa.objects.filter(vehiculo=vehiculo).exclude(empresa_id__in=empresa_ids).delete()


class VehiculoPropietario(BaseModel):
    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo', 
        on_delete=models.CASCADE, 
        related_name='propietarios'
    )
    cliente = models.ForeignKey(
        'clientes.Cliente', 
        on_delete=models.CASCADE, 
        related_name='vehiculos_asociados'
    )
    es_actual = models.BooleanField(default=True, verbose_name="¿Es el dueño activo?")
    fecha_inicio = models.DateField(auto_now_add=True)
    fecha_fin = models.DateField(null=True, blank=True)

    @property
    def cliente_nombre_estado(self):
        """Nombre del dueño, marcado como '(inactivo)' si el cliente lo está."""
        if not self.cliente_id:
            return ''
        nombre = self.cliente.nombre or ''
        if not self.cliente.is_active:
            return f'{nombre} (inactivo)'
        return nombre

    class Meta:
        verbose_name = "Relación Vehículo-Propietario"
        constraints = [
            # ⛔ REGLA CLAVE: Evita registrar el mismo vehículo 2 veces 
            # con el mismo cliente mientras la relación esté activa.
            models.UniqueConstraint(
                fields=['vehiculo', 'cliente'],
                condition=models.Q(es_actual=True),
                name='unique_vehiculo_cliente_activo'
            )
        ]
