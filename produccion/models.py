from django.db import models
from inventario.models import Producto

class ListaMaterialesBOM(models.Model):
    # El producto final que se va a obtener
    producto_terminado = models.OneToOneField(Producto, on_delete=models.CASCADE, related_name='receta_bom', verbose_name="Producto Terminado a Fabricar")
    cantidad_base = models.DecimalField(max_digits=10, decimal_places=2, default=1.00, verbose_name="Para producir (Cantidad base)")
    descripcion_proceso = models.TextField(blank=True, null=True, verbose_name="Instrucciones de Manufactura")

    class Meta:
        verbose_name = "Lista de Materiales (BOM)"
        verbose_name_plural = "Listas de Materiales (BOM)"

    def __str__(self):
        return f"Receta para: {self.producto_terminado.nombre}"

class InsumoBOM(models.Model):
    # Los componentes o materias primas individuales de la fórmula
    bom = models.ForeignKey(ListaMaterialesBOM, on_delete=models.CASCADE, related_name='insumos')
    materia_prima = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='usado_como_insumo', verbose_name="Materia Prima / Insumo")
    cantidad_requerida = models.DecimalField(max_digits=10, decimal_places=4, verbose_name="Cantidad Requerida")

    def __str__(self):
        return f"{self.cantidad_requerida} de {self.materia_prima.nombre} para {self.bom.producto_terminado.nombre}"

class OrdenProduccion(models.Model):
    ESTADOS_PRODUCCION = [
        ('PLA', 'Planeada / Cola de Espera'),
        ('PRO', 'En Proceso de Fabricación'),
        ('TER', 'Terminada (Stock Incrementado)'),
        ('CAN', 'Cancelada'),
    ]

    folio = models.CharField(max_length=20, unique=True, verbose_name="No. Orden de Producción")
    producto_a_fabricar = models.ForeignKey(Producto, on_delete=models.PROTECT, verbose_name="Producto a Fabricar")
    cantidad_a_producir = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Cantidad Solicitada")
    fecha_inicio = models.DateField(auto_now_add=True)
    estado = models.CharField(max_length=3, choices=ESTADOS_PRODUCCION, default='PLA')

    class Meta:
        verbose_name = "Orden de Producción"
        verbose_name_plural = "Órdenes de Producción"

    def __str__(self):
        return f"OP {self.folio} - {self.producto_a_fabricar.nombre} ({self.cantidad_a_producir})"
