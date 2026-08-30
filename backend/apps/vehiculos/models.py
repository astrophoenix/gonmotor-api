from django.db import models
from apps.core.models import BaseModel
from django_countries.fields import CountryField
from django.core.validators import MinValueValidator, MaxValueValidator
import datetime

# Mapeo de categorías a grupos de blueprints
GRUPO_BLUEPRINT_MAP = {
    # Livianos
    'AUTO': 'liviano',
    'JEEP': 'liviano',
    # Camionetas / Furgones
    'CAMN': 'camioneta',
    'FURG': 'camioneta',
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
class Vehiculo(BaseModel):

    #🚗 Opciones del tipo de transmisión con TextChoices
    class TipoTransmision(models.TextChoices):
        MANUAL = 'M', 'Manual / Mecánica'
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
        unique=True,
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
        help_text="JPG, PNG o WebP. Máximo 2MB. Se optimiza automáticamente."
    )
    empresas = models.ManyToManyField(
        'empresas.Empresa',
        related_name='vehiculos',
        blank=True,
        verbose_name="Empresas"
    )

    class Meta:
        verbose_name = "Vehículo"
        verbose_name_plural = "Vehículos"
        ordering = ['-created_at']

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
