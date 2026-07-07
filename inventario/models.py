from django.db import models

from django.db import models
from general.models import Moneda  # Importamos el modelo de la app general

class Producto(models.Model):
    UNIDADES_MEDIDA = [
        ('PZA', 'Pieza'),
        ('KG', 'Kilogramo'),
        ('LTS', 'Litro'),
        ('MET', 'Metro'),
    ]

    sku = models.CharField(max_length=50, unique=True, verbose_name="SKU / Código de Barras")
    nombre = models.CharField(max_length=150, verbose_name="Nombre del Producto")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción")
    unidad_medida = models.CharField(max_length=3, choices=UNIDADES_MEDIDA, default='PZA', verbose_name="Unidad de Medida")
    
    # Costos y Precios con su respectiva divisa
    moneda_costo = models.ForeignKey(Moneda, on_delete=models.PROTECT, related_name='productos_costo', verbose_name="Moneda de Costo")
    costo_ultimo = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name="Último Costo de Compra")
    costo_promedio = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name="Costo Promedio")
    
    moneda_venta = models.ForeignKey(Moneda, on_delete=models.PROTECT, related_name='productos_venta', verbose_name="Moneda de Venta")
    precio_venta = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name="Precio de Venta (Antes de Impuestos)")

    # Control de Existencias básico
    stock_actual = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Stock Actual")
    stock_minimo = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Stock Mínimo (Alerta)")
    
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"

    def __str__(self):
        return f"[{self.sku}] {self.nombre}"
