from django.db import models

from general.models import Moneda
from inventario.models import Producto

class Cliente(models.Model):
    # Campos estrictamente obligatorios para CFDI 4.0 en México
    rfc = models.CharField(max_length=13, unique=True, verbose_name="RFC del Cliente")
    razon_social = models.CharField(max_length=200, verbose_name="Nombre o Razón Social (Exacto SAT)")
    regimen_fiscal = models.CharField(max_length=3, verbose_name="Código de Régimen Fiscal (Ej. 601)")
    codigo_postal = models.CharField(max_length=5, verbose_name="Código Postal del Domicilio Fiscal")
    correo = models.EmailField(verbose_name="Correo para Envío de Facturas")

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self):
        return f"{self.rfc} - {self.razon_social}"

class Venta(models.Model):
    ESTADOS_VENTA = [
        ('PEN', 'Pendiente de Pago'),
        ('PAG', 'Pagada'),
        ('FAC', 'Facturada (CFDI Emitido)'),
        ('CAN', 'Cancelada'),
    ]
    USO_CFDI_CHOICES = [
        ('G01', 'Adquisición de mercancías'),
        ('G03', 'Gastos en general'),
        ('P01', 'Por definir'), # Nota: Verificar vigencia en catálogos del SAT actualizados
        ('CP01', 'Pagos'),
    ]

    folio = models.CharField(max_length=20, unique=True, verbose_name="Folio de Venta")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, verbose_name="Cliente")
    fecha = models.DateTimeField(auto_now_add=True)
    moneda = models.ForeignKey(Moneda, on_delete=models.PROTECT, verbose_name="Moneda de Venta")
    tipo_cambio_vuelo = models.DecimalField(max_digits=10, decimal_places=4, default=1.0000, verbose_name="Tipo de Cambio del Día")
    estado = models.CharField(max_length=3, choices=ESTADOS_VENTA, default='PEN')
    uso_cfdi = models.CharField(max_length=4, choices=USO_CFDI_CHOICES, default='G03', verbose_name="Uso de CFDI")
    
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    iva = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="IVA (16%)")
    total = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    # Datos que regresará el PAC tras el timbrado fiscal
    cfdi_uuid = models.UUIDField(blank=True, null=True, verbose_name="UUID Fiscal (Folio Fiscal SAT)")
    xml_file = models.FileField(upload_to='cfdi_xmls/', blank=True, null=True, verbose_name="Archivo XML SAT")

    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"

    def __str__(self):
        return f"Venta {self.folio} - {self.cliente.razon_social}"

class DetalleVenta(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='detalles')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio Cobrado")

    def __str__(self):
        return f"{self.cantidad} x {self.producto.nombre} en {self.venta.folio}"
