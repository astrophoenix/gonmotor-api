from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel


class Repuesto(BaseModel):
    """Repuesto / insumo con control de stock del taller.

    Multi-tenant por empresa. Los precios son referenciales (no fijos):
    en cotizaciones y órdenes el usuario podrá ajustarlos. Incluye campos
    preparados para la integración contable con Contífico.
    """

    class Categoria(models.TextChoices):
        FILTROS = 'FILTROS', 'Filtros'
        ACEITES = 'ACEITES', 'Aceites y Lubricantes'
        FRENOS = 'FRENOS', 'Sistema de Frenos'
        MOTOR = 'MOTOR', 'Motor'
        ELECTRICO = 'ELECTRICO', 'Sistema Eléctrico'
        SUSPENSION = 'SUSPENSION', 'Suspensión y Dirección'
        TRANSMISION = 'TRANSMISION', 'Transmisión'
        CARROCERIA = 'CARROCERIA', 'Carrocería'
        ILUMINACION = 'ILUMINACION', 'Iluminación'
        REFRIGERACION = 'REFRIGERACION', 'Refrigeración'
        OTROS = 'OTROS', 'Otros'

    class UnidadMedida(models.TextChoices):
        UNIDAD = 'UNIDAD', 'Unidad'
        LITRO = 'LITRO', 'Litro'
        GALON = 'GALON', 'Galón'
        KILOGRAMO = 'KG', 'Kilogramo'
        METRO = 'METRO', 'Metro'

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='repuestos',
    )
    sucursal = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='repuestos',
        verbose_name='Taller',
        help_text='Taller donde se almacena o comercializa el repuesto (opcional)',
    )
    codigo = models.CharField(max_length=50, verbose_name='Código del repuesto')
    nombre = models.CharField(max_length=150, verbose_name='Nombre del repuesto')
    descripcion = models.TextField(blank=True, null=True, verbose_name='Descripción')
    categoria = models.CharField(
        max_length=30,
        choices=Categoria.choices,
        default=Categoria.OTROS,
        verbose_name='Categoría',
    )
    marca = models.CharField(max_length=100, blank=True, default='', verbose_name='Marca / Fabricante')
    numero_parte = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Número de parte original',
    )
    unidad_medida = models.CharField(
        max_length=10,
        choices=UnidadMedida.choices,
        default=UnidadMedida.UNIDAD,
        verbose_name='Unidad de medida',
    )
    costo_referencial = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Costo referencial (compra)',
    )
    precio_venta = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Precio de venta sugerido (sin IVA)',
    )
    aplica_iva = models.BooleanField(default=True, verbose_name='Sujeto a IVA')
    stock_actual = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Stock actual',
    )
    stock_minimo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Stock mínimo',
        help_text='Por debajo de este valor el repuesto se marca como stock bajo',
    )
    ubicacion = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Ubicación / Estante / Bodega',
    )
    proveedor = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Proveedor habitual',
    )
    contifico_producto_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='ID de producto en Contífico',
        help_text='Código del producto en el sistema contable Contífico (integración)',
    )
    contifico_cuenta_contable = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Cuenta contable Contífico',
        help_text='Código de la cuenta contable asociada en Contífico (integración)',
    )

    class Meta:
        verbose_name = 'Repuesto'
        verbose_name_plural = 'Repuestos'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'codigo'],
                condition=models.Q(is_active=True),
                name='repuesto_codigo_activo_por_empresa',
            )
        ]

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    @property
    def stock_bajo(self) -> bool:
        return self.stock_actual <= self.stock_minimo

    def save(self, *args, **kwargs):
        if self.codigo:
            self.codigo = self.codigo.strip().upper()
        super().save(*args, **kwargs)


class Servicio(BaseModel):
    """Servicio / mano de obra del taller (catálogo de tareas estándar).

    No maneja stock. Se enfoca en tiempos estimados y en el alcance de cada
    tarea para que el mecánico lo seleccione al diagnosticar y cotizar.
    """

    class Categoria(models.TextChoices):
        MECANICA = 'MECANICA', 'Mecánica General'
        ELECTRICO = 'ELECTRICO', 'Eléctrico / Electrónica'
        MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento Preventivo'
        DIAGNOSTICO = 'DIAGNOSTICO', 'Diagnóstico / Escaneo'
        ENDEREZADA = 'ENDEREZADA', 'Enderezada / Pintura'
        GARANTIA = 'GARANTIA', 'Garantía'

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='servicios_catalogo',
    )
    sucursal = models.ForeignKey(
        'empresas.Taller',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='servicios_catalogo',
        verbose_name='Taller',
        help_text='Taller donde se presta el servicio (opcional)',
    )
    codigo = models.CharField(max_length=50, verbose_name='Código del servicio')
    nombre = models.CharField(max_length=150, verbose_name='Nombre del servicio')
    descripcion = models.TextField(blank=True, null=True, verbose_name='Descripción')
    categoria = models.CharField(
        max_length=30,
        choices=Categoria.choices,
        default=Categoria.MECANICA,
        verbose_name='Categoría',
    )
    tiempo_estimado_minutos = models.PositiveIntegerField(
        default=60,
        verbose_name='Tiempo estimado (minutos)',
        help_text='Tiempo estándar estimado para ejecutar la tarea',
    )
    tareas_estandar = models.TextField(
        blank=True,
        null=True,
        verbose_name='Tareas estándar incluidas',
        help_text='Detalle de las actividades que incluye el servicio',
    )
    precio_referencial = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Precio referencial de mano de obra',
    )
    contifico_producto_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='ID de producto/servicio en Contífico',
        help_text='Código del servicio en el sistema contable Contífico (integración)',
    )

    class Meta:
        verbose_name = 'Servicio (Mano de Obra)'
        verbose_name_plural = 'Servicios (Mano de Obra)'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'codigo'],
                condition=models.Q(is_active=True),
                name='servicio_codigo_activo_por_empresa',
            )
        ]

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    def save(self, *args, **kwargs):
        if self.codigo:
            self.codigo = self.codigo.strip().upper()
        super().save(*args, **kwargs)