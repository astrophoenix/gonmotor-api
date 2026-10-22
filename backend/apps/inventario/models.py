from decimal import Decimal

from django.db import models

from apps.core.ids_publicos import UuidPublicoModel
from apps.core.models import BaseEmpresaModel


class Repuesto(UuidPublicoModel, BaseEmpresaModel):
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
    iva_porcentaje_defecto = models.DecimalField(
        max_digits=5, 
        decimal_places=4, 
        default=Decimal('0.1500'), # Configurable según el país default de la empresa
        verbose_name="Porcentaje de IVA por defecto"
    )
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
                condition=models.Q(deleted_at__isnull=True),
                name='repuesto_codigo_vivo_por_empresa',
            )
        ]
        indexes = [
            models.Index(
                fields=['empresa', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='repuesto_emp_vivos_idx',
            ),
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


class RepuestoImagen(UuidPublicoModel, models.Model):
    """Imagen asociada a un repuesto (mínimo 1, máximo 3 por repuesto).

    Una y solo una imagen por repuesto es la principal (`es_principal`); es la
    que se muestra como foto representativa del repuesto en el listado.
    """

    MAX_IMAGENES = 3

    repuesto = models.ForeignKey(
        Repuesto,
        on_delete=models.CASCADE,
        related_name='imagenes',
        verbose_name='Repuesto',
    )
    imagen = models.ImageField(
        upload_to='repuestos/imagenes/',
        verbose_name='Imagen',
        help_text='Se guarda en WebP, máximo 1200 px por lado.',
    )
    miniatura = models.ImageField(upload_to='repuestos/imagenes/miniaturas/', blank=True, null=True)
    es_principal = models.BooleanField(
        default=False,
        verbose_name='Imagen principal',
        help_text='Solo una imagen por repuesto puede ser la principal.',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')

    class Meta:
        verbose_name = 'Imagen de Repuesto'
        verbose_name_plural = 'Imágenes de Repuestos'
        ordering = ['-es_principal', 'created_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['repuesto'],
                condition=models.Q(es_principal=True),
                name='una_imagen_principal_por_repuesto',
            )
        ]

    def __str__(self):
        return f'Repuesto #{self.repuesto_id} - imagen {self.pk}'

    def save(self, *args, **kwargs):
        # Garantiza una única imagen principal por repuesto y que siempre
        # exista una principal cuando el repuesto tiene imágenes.
        if self.es_principal and self.repuesto_id:
            RepuestoImagen.objects.filter(
                repuesto_id=self.repuesto_id, es_principal=True
            ).exclude(pk=self.pk).update(es_principal=False)
        super().save(*args, **kwargs)
        if self.repuesto_id and not RepuestoImagen.objects.filter(
            repuesto_id=self.repuesto_id, es_principal=True
        ).exists():
            RepuestoImagen.objects.filter(pk=self.pk).update(es_principal=True)
            self.es_principal = True

    @classmethod
    def asegurar_principal(cls, repuesto_id):
        """Promueve la imagen más antigua como principal si quedó sin ninguna."""
        if not repuesto_id:
            return
        if cls.objects.filter(repuesto_id=repuesto_id, es_principal=True).exists():
            return
        primera = cls.objects.filter(repuesto_id=repuesto_id).order_by('created_at', 'id').first()
        if primera:
            primera.es_principal = True
            primera.save(update_fields=['es_principal'])


class Servicio(UuidPublicoModel, BaseEmpresaModel):
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
    costo_referencial = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Costo referencial (mano de obra)',
        help_text='Costo interno estimado para cálculo de margen',
    )
    iva_porcentaje_defecto = models.DecimalField(
        max_digits=5, 
        decimal_places=4, 
        default=Decimal('0.1500'), 
        verbose_name="Porcentaje de IVA por defecto"
    )
    intervalo_km = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Repetir cada (km)',
        help_text='Kilómetros hasta el próximo mantenimiento sugerido tras este servicio.',
    )
    intervalo_meses = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Repetir cada (meses)',
        help_text='Meses hasta el próximo mantenimiento sugerido tras este servicio.',
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
                condition=models.Q(deleted_at__isnull=True),
                name='servicio_codigo_vivo_por_empresa',
            )
        ]
        indexes = [
            models.Index(
                fields=['empresa', '-created_at'],
                condition=models.Q(deleted_at__isnull=True),
                name='servicio_emp_vivos_idx',
            ),
        ]

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    def save(self, *args, **kwargs):
        if self.codigo:
            self.codigo = self.codigo.strip().upper()
        super().save(*args, **kwargs)