from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from .models import MovimientoInventario, Stock, Producto

@receiver(post_save, sender=MovimientoInventario)
def aplicar_movimiento_kardex(sender, instance, created, **kwargs):
    """
    Señal que se dispara AUTOMÁTICAMENTE cada vez que se guarda un MovimientoInventario.
    Calcula el nuevo Costo Promedio y actualiza las cantidades de Stock físico.
    """
    # Solo ejecutamos si el registro es nuevo (created = True). No en actualizaciones.
    if created:
        # Usamos atomic() para que si algo falla, NO se guarde nada a medias en la BD.
        with transaction.atomic():
            # select_for_update() "bloquea" la fila en la BD milisegundos para evitar 
            # condiciones de carrera si dos usuarios meten stock al mismo tiempo.
            producto = Producto.objects.select_for_update().get(id=instance.producto_id)

            # ==========================================
            # ESCENARIO 1: ENTRADA (Compras, Producción)
            # ==========================================
            if instance.tipo_movimiento == MovimientoInventario.ENTRADA:

                # A. CALCULAR COSTO PROMEDIO PONDERADO
                costo_entrada = instance.costo_unitario_mxn_capturado
                if costo_entrada and costo_entrada > Decimal('0'):
                    # Obtenemos el stock total actual en TODAS las bodegas de este producto
                    stock_total_actual = Stock.objects.filter(producto=producto).aggregate(
                        total=Sum('cantidad')
                    )['total'] or Decimal('0')

                    valor_inventario_actual = stock_total_actual * producto.costo_promedio_mxn
                    valor_nueva_entrada = instance.cantidad * costo_entrada

                    nuevo_stock_total = stock_total_actual + instance.cantidad

                    if nuevo_stock_total > Decimal('0'):
                        nuevo_costo = (valor_inventario_actual + valor_nueva_entrada) / nuevo_stock_total
                        # Actualizamos el producto con el nuevo costo
                        producto.costo_promedio_mxn = round(nuevo_costo, 6)
                        producto.save()

                # B. ACTUALIZAR STOCK FÍSICO
                stock, _ = Stock.objects.select_for_update().get_or_create(
                    producto=producto,
                    bodega=instance.bodega_destino,
                    defaults={'cantidad': Decimal('0.00')}
                )
                stock.cantidad += instance.cantidad
                stock.save()

            # ==========================================
            # ESCENARIO 2: SALIDA (Ventas, Consumo)
            # ==========================================
            elif instance.tipo_movimiento == MovimientoInventario.SALIDA:
                stock = Stock.objects.select_for_update().get(
                    producto=producto,
                    bodega=instance.bodega_origen
                )
                # Restamos del stock (La validación de no estar en negativo ya se hizo en models.py)
                stock.cantidad -= instance.cantidad
                stock.save()

            # ==========================================
            # ESCENARIO 3: TRANSFERENCIA (Entre bodegas)
            # ==========================================
            elif instance.tipo_movimiento == MovimientoInventario.TRANSFERENCIA:
                stock_origen = Stock.objects.select_for_update().get(
                    producto=producto,
                    bodega=instance.bodega_origen
                )
                stock_destino, _ = Stock.objects.select_for_update().get_or_create(
                    producto=producto,
                    bodega=instance.bodega_destino,
                    defaults={'cantidad': Decimal('0.00')}
                )

                stock_origen.cantidad -= instance.cantidad
                stock_origen.save()

                stock_destino.cantidad += instance.cantidad
                stock_destino.save()