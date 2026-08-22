# ERP/compras/models.py

from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from general.models import Moneda, TipoCambio
from inventario.models import Producto, Bodega, MovimientoInventario

class Proveedor(models.Model):
    """Catálogo de Proveedores."""
    rfc = models.CharField(max_length=15, unique=True, verbose_name="RFC / Tax ID")
    razon_social = models.CharField(max_length=255)
    nombre_comercial = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    direccion = models.TextField(blank=True, null=True)
    dias_credito = models.IntegerField(default=0, help_text="Días de crédito otorgados por el proveedor")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"

    def __str__(self):
        return f"{self.razon_social} ({self.rfc})"


class OrdenCompra_Maestro(models.Model):
    """Encabezado de la Orden de Compra."""

    ESTADO_CHOICES = [
        ('BORRADOR', 'Borrador'),
        ('AUTORIZADA', 'Autorizada (Pendiente de Recepción)'),
        ('RECIBIDA', 'Recibida (Ingresada a Almacén)'),
        ('CANCELADA', 'Cancelada'),
    ]

    folio = models.CharField(max_length=20, unique=True, editable=False)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    fecha_esperada = models.DateField(blank=True, null=True)

    # Destino logístico
    bodega_destino = models.ForeignKey(Bodega, on_delete=models.PROTECT, help_text="Almacén donde se recibirá la mercancía")

    # Manejo Multidivisa
    moneda = models.ForeignKey(Moneda, on_delete=models.PROTECT)
    tipo_cambio_aplicado = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1.000000'),
        help_text="Se captura automáticamente al autorizar/recibir si la moneda no es local"
    )

    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default='BORRADOR')
    observaciones = models.TextField(blank=True, null=True)

    # Totales (Se calcularán dinámicamente)
    subtotal = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'))
    impuestos = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'))
    total = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'))

    class Meta:
        verbose_name = "Orden de Compra"
        verbose_name_plural = "Órdenes de Compra"
        ordering = ['-fecha_emision']

    def __str__(self):
        return f"OC {self.folio} - {self.proveedor.razon_social}"


class OrdenCompra_Detalle(models.Model):
    """Filas de productos dentro de la Orden de Compra."""
    orden = models.ForeignKey(OrdenCompra_Maestro, related_name='detalles', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)

    cantidad = models.DecimalField(max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.01'))])

    # Precios en la moneda de la Orden de Compra
    precio_unitario = models.DecimalField(max_digits=18, decimal_places=6)
    subtotal_linea = models.DecimalField(max_digits=18, decimal_places=6, editable=False)

    # Trazabilidad
    cantidad_recibida = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0.000000'))

    def save(self, *args, **kwargs):
        # Auto-calcular el subtotal de la línea antes de guardar
        self.subtotal_linea = self.cantidad * self.precio_unitario
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cantidad} x {self.producto.sku} (OC {self.orden.folio})"