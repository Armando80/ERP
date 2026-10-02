from django.db import models
from django.utils import timezone

class Moneda(models.Model):
    # Ejemplo: 'MXN', 'USD', 'EUR'
    codigo = models.CharField(max_length=3, unique=True, verbose_name="Código ISO")
    nombre = models.CharField(max_length=50, verbose_name="Nombre de la Moneda")
    simbolo = models.CharField(max_length=5, verbose_name="Simbolo ($, €, etc.)")

    class Meta:
        verbose_name = "Moneda"
        verbose_name_plural = "Monedas"

    def __str__(self):
        return f"{self.codigo} ({self.simbolo})"

class TipoCambio(models.Model):
    moneda_origen = models.ForeignKey(Moneda, on_delete=models.CASCADE, related_name='cambios')
    # Guardamos el valor frente al MXN (Ej. 1 USD = 18.50 MXN)
    # Usamos DecimalField para evitar errores de redondeo en operaciones financieras
    valor_en_mxn = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Valor en MXN")
    fecha = models.DateField(default=timezone.now, verbose_name="Fecha del Tipo de Cambio")
    fuente = models.CharField(max_length=50, default="BANXICO_FIX", verbose_name="Fuente / Mecanismo")

    class Meta:
        verbose_name = "Tipo de Cambio"
        verbose_name_plural = "Tipos de Cambio"
        # Evita tener más de un tipo de cambio para la misma moneda en el mismo día
        unique_together = ('moneda_origen', 'fecha')
        ordering = ['-fecha', 'moneda_origen']

    def __str__(self):
        return f"1 {self.moneda_origen.codigo} = {self.valor_en_mxn} MXN ({self.fecha})"
