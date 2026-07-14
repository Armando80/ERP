# ERP_Mexico/inventario/models.py
# Modulo de Inventario - Fase 2

from django.db import models
from decimal import Decimal
from django.core.validators import MinValueValidator
from general.models import Moneda, TipoCambio  # Integración crítica de la Fase 1

# --- Definición de Catálogos ---

class UnidadMedida(models.Model):
    """Catálogo de Unidades de Medida (Pza, Kg, Lts)."""
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=50)

    class Meta:
        verbose_name = "Unidad de Medida"
        verbose_name_plural = "Unidades de Medida"

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"


class Bodega(models.Model):
    """Modelo estructurado para ubicaciones de almacén."""
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Bodega / Almacén"
        verbose_name_plural = "Bodegas / Almacenes"

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    """
    Modelo Maestro de Productos.
    Diferencia Materia Prima de Producto Terminado según tus catálogos.
    """
    
    # ENUM para tipo de producto (basado en CatalogoDeMateriales y MatPrima)
    MATERIA_PRIMA = 'MP'
    COMPONENTE = 'CP'
    PRODUCTO_TERMINADO = 'PT'
    TIPO_PRODUCTO_CHOICES = [
        (MATERIA_PRIMA, 'Materia Prima (Químicos/Hojalata)'),
        (COMPONENTE, 'Componente (Válvulas/Tapas)'),
        (PRODUCTO_TERMINADO, 'Producto Terminado (Botes/Aerosol)'),
    ]

    nombre = models.CharField(max_length=255)
    sku = models.CharField(max_length=50, unique=True, help_text="Ej: AE-001 (Aerosol) o MP-BAR-EPO (Barniz)")
    descripcion = models.TextField(blank=True, null=True)
    tipo = models.CharField(max_length=2, choices=TIPO_PRODUCTO_CHOICES, default=PRODUCTO_TERMINADO)
    unidad_medida = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, help_text="Ej: Pza, Kg")

    # --- Configuración Multidivisa ---
    # Costo se rastrea en USD/EUR, pero la venta suele ser en MXN o USD.
    # Moneda Base de Costo y Venta son requerimientos de Fase 2.
    moneda_base_costo = models.ForeignKey(
        Moneda, on_delete=models.PROTECT, related_name='productos_costo', help_text="Moneda en la que se rastrea el costo promedio"
    )
    moneda_base_venta = models.ForeignKey(
        Moneda, on_delete=models.PROTECT, related_name='productos_venta', help_text="Moneda de referencia para precios de venta"
    )

    costo_promedio_mxn = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0.000000'), help_text="Costo promedio unitario en MXN para contabilidad"
    )

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Catálogo de Productos"

    def __str__(self):
        return f"{self.nombre} ({self.sku})"


class Stock(models.Model):
    """Stock actual y ubicaciones específicas."""
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    bodega = models.ForeignKey(Bodega, on_delete=models.PROTECT)
    cantidad = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0.000000'), validators=[MinValueValidator(Decimal('0.000000'))]
    )
    ubicacion_especifica = models.CharField(max_length=50, blank=True, null=True, help_text="Ej: Pasillo A, Estante 4")
    stock_minimo = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0.000000'), help_text="Alerta de stock mínimo")

    class Meta:
        verbose_name = "Existencia (Stock)"
        verbose_name_plural = "Existencias por Ubicación"
        # Garantiza que solo haya un registro de stock por producto por bodega
        unique_together = ('producto', 'bodega')

    def __str__(self):
        return f"{self.producto.sku} en {self.bodega.nombre} ({self.cantidad})"


class MovimientoInventario(models.Model):
    """Kardex: Registro detallado de entradas, salidas y transferencias."""
    
    ENTRADA = 'E'
    SALIDA = 'S'
    TRANSFERENCIA = 'T'
    AJUSTE = 'A'
    TIPO_MOVIMIENTO_CHOICES = [
        (ENTRADA, 'Entrada (Compra/Producción)'),
        (SALIDA, 'Salida (Venta/Consumo)'),
        (TRANSFERENCIA, 'Transferencia entre Bodegas'),
        (AJUSTE, 'Ajuste Manual'),
    ]

    fecha = models.DateTimeField(auto_now_add=True)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    
    # Origen y Destino para transferencias y trazabilidad
    bodega_origen = models.ForeignKey(Bodega, on_delete=models.PROTECT, related_name='movimientos_origen', blank=True, null=True)
    bodega_destino = models.ForeignKey(Bodega, on_delete=models.PROTECT, related_name='movimientos_destino', blank=True, null=True)
    
    tipo_movimiento = models.CharField(max_length=1, choices=TIPO_MOVIMIENTO_CHOICES)
    cantidad = models.DecimalField(max_digits=18, decimal_places=6)
    
    # Costo asociado al movimiento (crucial para recalcular costo promedio)
    # Este costo debe estar en MXN, capturado al momento del movimiento.
    # Si viene de Compras (Fase 3), se convierte usando TipoCambio del día de la OC.
    costo_unitario_mxn_capturado = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0.000000'), help_text="Costo unitario en MXN al momento del movimiento"
    )

    # Referencia cruzada (vinculará OC de Compras (Fase 3) o OP de Producción (Fase 5))
    referencia_operacion = models.CharField(max_length=100, blank=True, null=True, help_text="Ej: Folio OC-2026-004 o OP-2026-001")
    usuario = models.ForeignKey('auth.User', on_delete=models.PROTECT, help_text="Usuario que registró el movimiento")
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Movimiento de Inventario (Kardex)"
        verbose_name_plural = "Movimientos de Inventario (Kardex)"
        ordering = ['-fecha'] # Ordenar por fecha descendente

    def __str__(self):
        return f"{self.fecha} - {self.producto.sku} ({self.get_tipo_movimiento_display()})"
