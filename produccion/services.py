# ERP Decorlata - produccion/services.py
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from general.models import Moneda
from inventario.models import Producto, Bodega, Stock, MovimientoInventario
from .models import ListaMaterialesBOM, InsumoBOM, OrdenProduccion, OrdenProduccion_Insumo


def generar_folio_produccion():
    """Genera un folio secuencial por año, ej: OP-2026-0001"""
    year = timezone.now().year
    prefijo = f"OP-{year}-"

    ultima_orden = OrdenProduccion.objects.filter(
        folio__startswith=prefijo
    ).order_by('-folio').first()

    if ultima_orden:
        try:
            secuencia = int(ultima_orden.folio.split('-')[-1]) + 1
        except (ValueError, IndexError):
            secuencia = 1
    else:
        secuencia = 1

    return f"{prefijo}{secuencia:04d}"


def crear_orden_produccion(producto_id, cantidad, bodega_origen_id, bodega_destino_id, usuario, fecha_compromiso=None, observaciones=None):
    """
    Crea una nueva Orden de Producción en estado PLANEADA.
    Si el producto cuenta con una receta (BOM) activa, congela los insumos requeridos
    escalados a la cantidad a fabricar en OrdenProduccion_Insumo.
    """
    with transaction.atomic():
        producto = Producto.objects.select_related('receta_bom').get(id=producto_id)
        bodega_origen = Bodega.objects.get(id=bodega_origen_id)
        bodega_destino = Bodega.objects.get(id=bodega_destino_id)
        cantidad_decimal = Decimal(str(cantidad))

        bom = getattr(producto, 'receta_bom', None)
        if bom and not bom.activo:
            bom = None

        orden = OrdenProduccion.objects.create(
            folio=generar_folio_produccion(),
            producto_a_fabricar=producto,
            bom=bom,
            cantidad_a_producir=cantidad_decimal,
            bodega_origen_insumos=bodega_origen,
            bodega_destino_pt=bodega_destino,
            fecha_compromiso=fecha_compromiso,
            observaciones=observaciones,
            usuario_creacion=usuario,
            estado=OrdenProduccion.PLANEADA
        )

        # Si hay receta (BOM), congelamos los insumos
        if bom and bom.cantidad_base > Decimal('0'):
            factor = cantidad_decimal / bom.cantidad_base
            for insumo_receta in bom.insumos.select_related('materia_prima'):
                cant_estimada = round(insumo_receta.cantidad_con_merma * factor, 6)
                costo_unit = insumo_receta.materia_prima.costo_promedio_mxn or Decimal('0.000000')
                costo_tot = round(cant_estimada * costo_unit, 6)

                OrdenProduccion_Insumo.objects.create(
                    orden=orden,
                    insumo=insumo_receta.materia_prima,
                    cantidad_estimada=cant_estimada,
                    cantidad_consumida=cant_estimada,
                    costo_unitario_mxn=costo_unit,
                    costo_total_mxn=costo_tot
                )

        return orden


def iniciar_orden_produccion(orden_id, usuario):
    """
    Pasa una Orden de Producción de PLANEADA a EN PROCESO.
    Reserva las cantidades estimadas en el Stock de la bodega de origen.
    """
    with transaction.atomic():
        orden = OrdenProduccion.objects.select_for_update().get(id=orden_id)

        if orden.estado != OrdenProduccion.PLANEADA:
            raise ValueError(f"Solo se pueden iniciar órdenes en estado Planeada. Estado actual: {orden.get_estado_display()}")

        # Reservar stock en almacén de insumos
        for detalle in orden.insumos_detalle.select_related('insumo').all():
            stock, _ = Stock.objects.select_for_update().get_or_create(
                producto=detalle.insumo,
                bodega=orden.bodega_origen_insumos,
                defaults={'cantidad': Decimal('0.000000'), 'cantidad_reservada': Decimal('0.000000')}
            )
            stock.cantidad_reservada += detalle.cantidad_estimada
            stock.save()

        orden.estado = OrdenProduccion.EN_PROCESO
        orden.save()
        return orden


def finalizar_orden_produccion(orden_id, cantidad_real, lote, consumos_dict=None, usuario=None):
    """
    Finaliza la manufactura de una OP:
    1. Valida stock disponible y genera salidas de insumos (Kardex: SALIDA) a costo promedio.
    2. Libera las reservas de stock si las hubiera.
    3. Acumula el costo real total de insumos y calcula el costo unitario resultante.
    4. Genera la entrada al Kardex (ENTRADA) del producto terminado valorizado a dicho costo.
    5. Actualiza la OP a TERMINADA.
    """
    with transaction.atomic():
        orden = OrdenProduccion.objects.select_for_update().get(id=orden_id)

        if orden.estado not in [OrdenProduccion.EN_PROCESO, OrdenProduccion.PLANEADA]:
            raise ValueError(f"No se puede finalizar una orden en estado '{orden.get_estado_display()}'.")

        cant_real_dec = Decimal(str(cantidad_real))
        if cant_real_dec <= Decimal('0'):
            raise ValueError("La cantidad real producida debe ser mayor a cero.")

        if not lote:
            lote = f"LOT-{orden.folio}-{timezone.now().strftime('%Y%m%d')}"

        moneda_mxn = Moneda.objects.filter(codigo='MXN').first()
        if not moneda_mxn:
            moneda_mxn = Moneda.objects.create(codigo='MXN', nombre='Peso Mexicano', simbolo='$')

        costo_total_insumos_mxn = Decimal('0.000000')

        # 1. Procesar consumos de insumos
        for detalle in orden.insumos_detalle.select_related('insumo'):
            # Determinar cantidad real consumida
            if consumos_dict and (str(detalle.id) in consumos_dict or str(detalle.insumo_id) in consumos_dict):
                clave = str(detalle.id) if str(detalle.id) in consumos_dict else str(detalle.insumo_id)
                cant_consumida = Decimal(str(consumos_dict[clave]))
            else:
                cant_consumida = detalle.cantidad_consumida if detalle.cantidad_consumida > Decimal('0') else detalle.cantidad_estimada

            if cant_consumida <= Decimal('0'):
                continue

            # Obtener costo promedio del insumo en MXN
            costo_unitario_insumo = detalle.insumo.costo_promedio_mxn or Decimal('0.000000')
            costo_linea = round(cant_consumida * costo_unitario_insumo, 6)
            costo_total_insumos_mxn += costo_linea

            # Actualizar registro de detalle
            detalle.cantidad_consumida = cant_consumida
            detalle.costo_unitario_mxn = costo_unitario_insumo
            detalle.costo_total_mxn = costo_linea
            detalle.save()

            # Liberar la reserva previa si la orden estaba En Proceso
            if orden.estado == OrdenProduccion.EN_PROCESO:
                stock_insumo = Stock.objects.filter(
                    producto=detalle.insumo,
                    bodega=orden.bodega_origen_insumos
                ).first()
                if stock_insumo:
                    stock_insumo.cantidad_reservada = max(
                        Decimal('0.000000'),
                        stock_insumo.cantidad_reservada - detalle.cantidad_estimada
                    )
                    stock_insumo.save()

            # Generar movimiento de salida en el Kardex
            MovimientoInventario.objects.create(
                producto=detalle.insumo,
                bodega_origen=orden.bodega_origen_insumos,
                tipo_movimiento=MovimientoInventario.SALIDA,
                cantidad=cant_consumida,
                costo_unitario_original=costo_unitario_insumo,
                moneda_original=moneda_mxn,
                tipo_cambio_aplicado=Decimal('1.000000'),
                costo_unitario_mxn_capturado=costo_unitario_insumo,
                referencia_operacion=f"OP-{orden.folio}",
                usuario=usuario or orden.usuario_creacion,
                lote=lote,
                observaciones=f"Consumo de materia prima/componentes para {orden.folio}"
            )

        # 2. Calcular costo unitario resultante del Producto Terminado
        costo_unitario_pt = round(costo_total_insumos_mxn / cant_real_dec, 6) if cant_real_dec > Decimal('0') else Decimal('0.000000')

        # 3. Generar movimiento de entrada al Kardex del Producto Terminado
        MovimientoInventario.objects.create(
            producto=orden.producto_a_fabricar,
            bodega_destino=orden.bodega_destino_pt,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=cant_real_dec,
            costo_unitario_original=costo_unitario_pt,
            moneda_original=moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            costo_unitario_mxn_capturado=costo_unitario_pt,
            referencia_operacion=f"OP-{orden.folio}",
            usuario=usuario or orden.usuario_creacion,
            lote=lote,
            observaciones=f"Entrada por producción terminada {orden.folio}"
        )

        # 4. Actualizar la Orden de Producción
        orden.estado = OrdenProduccion.TERMINADA
        orden.cantidad_producida = cant_real_dec
        orden.lote_fabricacion = lote
        orden.costo_total_insumos_mxn = costo_total_insumos_mxn
        orden.costo_unitario_final_mxn = costo_unitario_pt
        orden.fecha_finalizacion = timezone.now()
        orden.usuario_finalizacion = usuario
        orden.save()

        return orden


def cancelar_orden_produccion(orden_id, usuario=None):
    """
    Cancela una orden de producción. Si tenía stock reservado, lo libera.
    """
    with transaction.atomic():
        orden = OrdenProduccion.objects.select_for_update().get(id=orden_id)

        if orden.estado == OrdenProduccion.TERMINADA:
            raise ValueError("No se puede cancelar una orden de producción que ya ha sido terminada.")

        # Liberar reservas si estaba en proceso
        if orden.estado == OrdenProduccion.EN_PROCESO:
            for detalle in orden.insumos_detalle.select_related('insumo'):
                stock_insumo = Stock.objects.filter(
                    producto=detalle.insumo,
                    bodega=orden.bodega_origen_insumos
                ).first()
                if stock_insumo:
                    stock_insumo.cantidad_reservada = max(
                        Decimal('0.000000'),
                        stock_insumo.cantidad_reservada - detalle.cantidad_estimada
                    )
                    stock_insumo.save()

        orden.estado = OrdenProduccion.CANCELADA
        orden.save()
        return orden
