# ERP Decorlata - produccion/models.py
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.models import User
from django.utils import timezone
from inventario.models import Producto, Bodega


class ListaMaterialesBOM(models.Model):
    """
    Lista de Materiales (Bill of Materials - BOM).
    Define la fórmula o receta para fabricar una cantidad base de un producto
    (Producto Terminado, Hoja Litografiada, Plantilla o Hoja Cortada).
    """
    producto_terminado = models.OneToOneField(
        Producto,
        on_delete=models.CASCADE,
        related_name='receta_bom',
        verbose_name="Producto a Fabricar"
    )
    cantidad_base = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('1.0000'),
        validators=[MinValueValidator(Decimal('0.0001'))],
        verbose_name="Cantidad Base de Fabricación",
        help_text="Ej: 1 para 1 pza, 1000 para un millar de botes"
    )
    descripcion_proceso = models.TextField(
        blank=True,
        null=True,
        verbose_name="Instrucciones y Ruta de Manufactura"
    )
    activo = models.BooleanField(
        default=True,
        verbose_name="Receta Activa"
    )
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lista de Materiales (BOM)"
        verbose_name_plural = "Listas de Materiales (BOM)"
        ordering = ['producto_terminado__nombre']

    def __str__(self):
        return f"BOM: {self.producto_terminado.sku} - {self.producto_terminado.nombre} (Base: {self.cantidad_base})"

    @property
    def costo_estimado_total_mxn(self):
        """Calcula el costo total estimado de los insumos para la cantidad base."""
        total = Decimal('0.000000')
        for insumo in self.insumos.select_related('materia_prima'):
            total += insumo.costo_estimado_linea_mxn
        return round(total, 6)

    @property
    def costo_estimado_unitario_mxn(self):
        """Calcula el costo unitario estimado (por pieza) de fabricar este producto."""
        if self.cantidad_base and self.cantidad_base > Decimal('0'):
            return round(self.costo_estimado_total_mxn / self.cantidad_base, 6)
        return Decimal('0.000000')


class InsumoBOM(models.Model):
    """
    Componente o materia prima individual requerida dentro de la receta (BOM).
    """
    bom = models.ForeignKey(
        ListaMaterialesBOM,
        on_delete=models.CASCADE,
        related_name='insumos',
        verbose_name="Receta BOM"
    )
    materia_prima = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='usado_como_insumo',
        verbose_name="Materia Prima / Insumo"
    )
    cantidad_requerida = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name="Cantidad Requerida"
    )
    porcentaje_merma = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="% Merma / Scrap Esperado",
        help_text="Porcentaje adicional contemplado por mermas del proceso"
    )

    class Meta:
        verbose_name = "Insumo de Receta"
        verbose_name_plural = "Insumos de Receta"
        unique_together = ('bom', 'materia_prima')

    def __str__(self):
        return f"{self.cantidad_requerida} x {self.materia_prima.sku} para {self.bom.producto_terminado.sku}"

    @property
    def cantidad_con_merma(self):
        """Retorna la cantidad efectiva requerida considerando el factor de merma."""
        factor = Decimal('1.00') + (self.porcentaje_merma / Decimal('100.00'))
        return round(self.cantidad_requerida * factor, 6)

    @property
    def costo_estimado_linea_mxn(self):
        """Calcula el costo proyectado de este insumo con base en su costo promedio ponderado en MXN."""
        costo_unitario = self.materia_prima.costo_promedio_mxn or Decimal('0.000000')
        return round(self.cantidad_con_merma * costo_unitario, 6)


class OrdenProduccion(models.Model):
    """
    Orden de Producción (OP). Controla la fabricación de un lote de producto,
    el seguimiento de estados y la inyección final al Kardex de inventario.
    """
    PLANEADA = 'PLA'
    EN_PROCESO = 'PRO'
    TERMINADA = 'TER'
    CANCELADA = 'CAN'

    ESTADOS_PRODUCCION = [
        (PLANEADA, 'Planeada / En Espera'),
        (EN_PROCESO, 'En Proceso de Fabricación'),
        (TERMINADA, 'Terminada (Stock Afectado)'),
        (CANCELADA, 'Cancelada'),
    ]

    folio = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        verbose_name="No. Orden de Producción"
    )
    producto_a_fabricar = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='ordenes_fabricacion',
        verbose_name="Producto a Fabricar"
    )
    bom = models.ForeignKey(
        ListaMaterialesBOM,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ordenes_produccion',
        verbose_name="Fórmula / BOM Utilizada"
    )
    cantidad_a_producir = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        verbose_name="Cantidad Solicitada"
    )
    cantidad_producida = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        verbose_name="Cantidad Producida Real"
    )

    # Ubicaciones de Almacén
    bodega_origen_insumos = models.ForeignKey(
        Bodega,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_origen',
        verbose_name="Bodega de Insumos / Materia Prima",
        help_text="Almacén del cual se tomarán las materias primas y componentes"
    )
    bodega_destino_pt = models.ForeignKey(
        Bodega,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_destino',
        verbose_name="Bodega Destino (Producto Fabricado)",
        help_text="Almacén al cual ingresará el lote terminado"
    )

    # Tiempos
    fecha_inicio = models.DateTimeField(
        default=timezone.now,
        verbose_name="Fecha de Creación"
    )
    fecha_compromiso = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha Compromiso de Entrega"
    )
    fecha_finalizacion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de Finalización"
    )

    estado = models.CharField(
        max_length=3,
        choices=ESTADOS_PRODUCCION,
        default=PLANEADA,
        verbose_name="Estado de Producción"
    )

    # Costos Industriales Acumulados
    costo_total_insumos_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Total Insumos (MXN)"
    )
    costo_unitario_final_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Unitario Resultante (MXN)"
    )

    # Trazabilidad Industrial
    lote_fabricacion = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Lote de Fabricación"
    )
    usuario_creacion = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_creadas',
        verbose_name="Registrado por"
    )
    usuario_finalizacion = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_finalizadas',
        verbose_name="Finalizado por"
    )
    observaciones = models.TextField(
        blank=True,
        null=True,
        verbose_name="Observaciones del Proceso"
    )

    class Meta:
        verbose_name = "Orden de Producción"
        verbose_name_plural = "Órdenes de Producción"
        ordering = ['-fecha_inicio']

    def __str__(self):
        return f"OP {self.folio} - {self.producto_a_fabricar.sku} ({self.get_estado_display()})"


class OrdenProduccion_Insumo(models.Model):
    """
    Desglose congelado de insumos requeridos y consumidos para una Orden de Producción específica.
    Garantiza inmutabilidad histórica frente a futuros cambios en la receta general (BOM).
    """
    orden = models.ForeignKey(
        OrdenProduccion,
        on_delete=models.CASCADE,
        related_name='insumos_detalle',
        verbose_name="Orden de Producción"
    )
    insumo = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='usos_en_ordenes',
        verbose_name="Insumo / Materia Prima"
    )
    cantidad_estimada = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        verbose_name="Cantidad Proyectada"
    )
    cantidad_consumida = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Cantidad Real Consumida"
    )
    costo_unitario_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Unitario Capturado (MXN)"
    )
    costo_total_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Total Línea (MXN)"
    )

    class Meta:
        verbose_name = "Insumo de Orden de Producción"
        verbose_name_plural = "Insumos de Orden de Producción"
        unique_together = ('orden', 'insumo')

    def __str__(self):
        return f"{self.insumo.sku} para {self.orden.folio}"

