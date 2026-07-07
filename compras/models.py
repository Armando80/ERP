from django.db import models

from general.models import Moneda
from inventario.models import Producto

class Proveedor(models.Model):
    rfc = models.CharField(max_length=13, unique=True, verbose_name="RFC del Proveedor")
    razon_social = models.CharField(max_length=200, verbose_name="Razón Social")
    contacto_nombre = models.CharField(max_length=100, blank=True, null=True, verbose_name="Nombre de Contacto")
    correo = models.EmailField(verbose_name="Correo Electrónico")
    telefono = models.CharField(max_length=15, verbose_name="Teléfono")

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"

    def __str__(self):
        return f"{self.rfc} - {self.razon_social}"

class OrdenCompra(models.Model):
    ESTADOS_COMPRA = [
        ('BOR', 'Borrador'),
        ('SOL', 'Solicitada'),
        ('REC', 'Recibida Totalmente'),
        ('CAN', 'Cancelada'),
    ]

    folio = models.CharField(max_length=20, unique=True, verbose_name="Folio de Compra")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, verbose_name="Proveedor")
    fecha_pedido = models.DateField(auto_now_add=True)
    moneda = models.ForeignKey(Moneda, on_delete=models.PROTECT, verbose_name="Moneda de la Operación")
    tipo_cambio_vuelo = models.DecimalField(max_digits=10, decimal_places=4, default=1.0000, verbose_name="Tipo de Cambio Aplicado")
    estado = models.CharField(max_length=3, choices=ESTADOS_COMPRA, default='BOR')
    total = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    class Meta:
        verbose_name = "Orden de Compra"
        verbose_name_plural = "Órdenes de Compra"

    def __str__(self):
        return f"Compra {self.folio} - {self.proveedor.razon_social}"

class DetalleOrdenCompra(models.Model):
    orden = models.ForeignKey(OrdenCompra, on_delete=models.CASCADE, related_name='detalles')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, verbose_name="Producto")
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Cantidad")
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio Unitario (Divisa Original)")

    def __str__(self):
        return f"{self.cantidad} x {self.producto.nombre} en Orden {self.orden.folio}"
