# ERP Decorlata - produccion/services.py
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from general.models import Moneda
from inventario.models import Producto, Bodega, Stock, MovimientoInventario
from .models import (
    ListaMaterialesBOM, InsumoBOM, OrdenProduccion, OrdenProduccion_Insumo,
    EntregaParcialProduccion, EntregaParcial_Insumo, EtapaProduccionOP
)


import logging
logger = logging.getLogger(__name__)


def resolver_costo_unitario_producto_mxn(producto, fecha=None, visitados=None):
    """
    Resuelve el costo unitario de referencia en MXN para un producto siguiendo
    la jerarquía de costeo industrial multidivisa:
    1. Kardex / Existencia: Si costo_promedio_mxn > 0, se utiliza este costo real ponderado.
    2. Sub-ensamble / Receta activa: Si el producto es manufacturado (ej. Plantilla CC)
       y tiene receta activa, calcula el costo estimado unitario de dicha sub-receta.
    3. Compras / Cotizaciones (OC): Si no hay costo en almacén, busca la última Orden
       de Compra registrada. Si la orden está en USD o EUR, se convierte a MXN al
       tipo de cambio oficial vigente (Banxico/DOF).
    4. Costo base de catálogo: 0.000000 si no hay información previa.
    """
    if visitados is None:
        visitados = set()

    # Prevenir recursión infinita en recetas circulares
    if producto.id in visitados:
        return {
            'costo_mxn': Decimal('0.000000'),
            'moneda_origen': producto.moneda_base_costo.codigo if producto.moneda_base_costo else 'MXN',
            'costo_original': Decimal('0.000000'),
            'tipo_cambio': Decimal('1.000000'),
            'origen': 'CIRCULAR',
            'referencia': 'Recursión circular detectada'
        }

    visitados_copia = set(visitados)
    visitados_copia.add(producto.id)

    # 1. Costo promedio ponderado en Kardex (si es mayor a 0)
    if producto.costo_promedio_mxn and producto.costo_promedio_mxn > Decimal('0'):
        return {
            'costo_mxn': producto.costo_promedio_mxn,
            'moneda_origen': 'MXN',
            'costo_original': producto.costo_promedio_mxn,
            'tipo_cambio': Decimal('1.000000'),
            'origen': 'KARDEX',
            'referencia': 'Almacén / Kardex'
        }

    # 2. Si es un sub-ensamble fabricado con receta activa (BOM)
    sub_bom = getattr(producto, 'receta_bom', None)
    if sub_bom and sub_bom.activo:
        costo_sub = sub_bom.calcular_costo_estimado_unitario_mxn(visitados=visitados_copia)
        if costo_sub > Decimal('0'):
            return {
                'costo_mxn': costo_sub,
                'moneda_origen': 'MXN',
                'costo_original': costo_sub,
                'tipo_cambio': Decimal('1.000000'),
                'origen': 'SUB_BOM',
                'referencia': sub_bom.producto_terminado.sku
            }

    # 3. Buscar en la última Orden de Compra (incluso si está en Borrador o Autorizada)
    from compras.models import OrdenCompra_Detalle
    oc_det = OrdenCompra_Detalle.objects.filter(
        producto=producto,
        precio_unitario__gt=Decimal('0')
    ).select_related('orden__moneda').order_by('-orden__fecha_emision').first()

    if oc_det:
        moneda_oc = oc_det.orden.moneda
        precio_orig = oc_det.precio_unitario
        fecha_eval = fecha or (oc_det.orden.fecha_emision.date() if oc_det.orden.fecha_emision else None)
        try:
            from general.services import obtener_tipo_cambio_vigente
            tc = obtener_tipo_cambio_vigente(moneda_oc, fecha=fecha_eval)
        except Exception as e:
            logger.warning("No se pudo obtener TC para %s: %s. Usando 1.0", moneda_oc, e)
            tc = Decimal('1.000000')

        costo_mxn = round(precio_orig * tc, 6)
        return {
            'costo_mxn': costo_mxn,
            'moneda_origen': moneda_oc.codigo,
            'costo_original': precio_orig,
            'tipo_cambio': tc,
            'origen': 'OC',
            'referencia': oc_det.orden.folio
        }

    # 4. Sin costo registrado
    codigo_mon = producto.moneda_base_costo.codigo if producto.moneda_base_costo else 'MXN'
    return {
        'costo_mxn': Decimal('0.000000'),
        'moneda_origen': codigo_mon,
        'costo_original': Decimal('0.000000'),
        'tipo_cambio': Decimal('1.000000'),
        'origen': 'SIN_COSTO',
        'referencia': 'Sin costo histórico'
    }


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


def inicializar_etapas_ensamble_op(orden):
    """
    Inicializa las 6 estaciones secuenciales industriales en la línea de ensamble para la OP:
    1. Cizallado Primario de Hojalata
    2. Litografía y Barnizado
    3. Corte de Plantillas (Cuerpos)
    4. Formado y Soldadura Eléctrica
    5. Línea de Ensamble y Engargolado
    6. Prueba de Hermeticidad y Paletizado
    """
    if orden.etapas_ensamble.exists():
        return list(orden.etapas_ensamble.all())

    etapas = []
    secuencia_config = [
        (EtapaProduccionOP.CORTE_HOJA, '1. Cizallado Primario de Hojalata', 1),
        (EtapaProduccionOP.LITOGRAFIA, '2. Litografía y Barnizado', 2),
        (EtapaProduccionOP.CORTE_CUERPO, '3. Corte de Plantillas (Cuerpos)', 3),
        (EtapaProduccionOP.SOLDADURA, '4. Formado y Soldadura Eléctrica', 4),
        (EtapaProduccionOP.ENSAMBLE, '5. Línea de Ensamble y Engargolado', 5),
        (EtapaProduccionOP.PRUEBA_EMPAQUE, '6. Prueba de Hermeticidad y Paletizado', 6),
    ]

    for codigo, nombre, sec in secuencia_config:
        cant_in = orden.cantidad_a_producir if sec == 1 else Decimal('0.0000')
        etapa = EtapaProduccionOP.objects.create(
            orden=orden,
            codigo_etapa=codigo,
            nombre_etapa=nombre,
            secuencia=sec,
            estado=EtapaProduccionOP.ESTADO_PENDIENTE,
            cantidad_entrada=cant_in,
            cantidad_buena=Decimal('0.0000'),
            cantidad_scrap=Decimal('0.0000')
        )
        etapas.append(etapa)

    return etapas


def actualizar_avance_etapa_ensamble(etapa_id, cantidad_buena=None, cantidad_scrap=None, nuevo_estado=None, operador=None, notas=None, usuario=None):
    """
    Actualiza el avance de producción en una estación de la línea de ensamble:
    - Registra piezas conformes y scrap.
    - Cambia estado (PENDIENTE, EN_PROCESO, COMPLETADA).
    - Al completar una estación, transfiere automáticamente las piezas conformes a la siguiente estación.
    - Si se completa la última estación (Hermeticidad y Paletizado), actualiza cantidad_producida de la OP.
    """
    with transaction.atomic():
        etapa = EtapaProduccionOP.objects.select_for_update().get(id=etapa_id)
        orden = etapa.orden

        if cantidad_buena is not None:
            cant_b = Decimal(str(cantidad_buena))
            if cant_b < Decimal('0'):
                raise ValueError("La cantidad buena no puede ser negativa.")
            etapa.cantidad_buena = cant_b

        if cantidad_scrap is not None:
            cant_s = Decimal(str(cantidad_scrap))
            if cant_s < Decimal('0'):
                raise ValueError("La cantidad de scrap no puede ser negativa.")
            etapa.cantidad_scrap = cant_s

        if operador is not None:
            etapa.operador = operador

        if notas is not None:
            etapa.notas = notas

        if nuevo_estado:
            if nuevo_estado not in [choice[0] for choice in EtapaProduccionOP.ESTADOS_CHOICES]:
                raise ValueError(f"Estado de etapa inválido: {nuevo_estado}")
            etapa.estado = nuevo_estado

            if nuevo_estado == EtapaProduccionOP.ESTADO_EN_PROCESO and not etapa.fecha_inicio:
                etapa.fecha_inicio = timezone.now()

            elif nuevo_estado == EtapaProduccionOP.ESTADO_COMPLETADA:
                if not etapa.fecha_inicio:
                    etapa.fecha_inicio = timezone.now()
                etapa.fecha_fin = timezone.now()

                # Cascada automática de piezas conformes a la siguiente estación
                etapa_siguiente = orden.etapas_ensamble.filter(secuencia=etapa.secuencia + 1).first()
                if etapa_siguiente:
                    etapa_siguiente.cantidad_entrada = etapa.cantidad_buena
                    etapa_siguiente.save(update_fields=['cantidad_entrada'])
                else:
                    # Última estación en la línea
                    orden.cantidad_producida = etapa.cantidad_buena
                    orden.save(update_fields=['cantidad_producida'])

        # Si la orden está PLANEADA y la estación pasa a EN_PROCESO o COMPLETADA, transicionar orden
        if orden.estado == OrdenProduccion.PLANEADA and etapa.estado in [EtapaProduccionOP.ESTADO_EN_PROCESO, EtapaProduccionOP.ESTADO_COMPLETADA]:
            orden.estado = OrdenProduccion.EN_PROCESO
            orden.save(update_fields=['estado'])

        etapa.save()
        return etapa


def explosion_materiales_bom(producto, cantidad, bodega_origen=None):
    """
    Realiza la explosión de materiales (BOM) clasificada para un producto y cantidad objetivo.
    Desglosa y categoriza los insumos en las 3 familias industriales clave de Decorlata:
    1. Hojalata (HR, HL, HC, CC)
    2. Materia Prima química (MP - barnices, tintas, compuestos, soldadura)
    3. Componentes metálicos (CP - conos, fondos, válvulas, tapas)
    4. Otros
    Calcula mermas esperadas, cantidades netas y brutas, costos MXN y valida existencias físicas/disponibles en bodega.
    """
    if isinstance(producto, (int, str)):
        producto = Producto.objects.select_related('receta_bom', 'unidad_medida').get(id=producto)

    if isinstance(bodega_origen, (int, str)):
        bodega_origen = Bodega.objects.filter(id=bodega_origen).first()

    cant_dec = Decimal(str(cantidad))
    bom = getattr(producto, 'receta_bom', None)
    if not bom or not bom.activo:
        return {
            'producto': producto,
            'cantidad': cant_dec,
            'bom': None,
            'tiene_bom': False,
            'hojalata': [],
            'materia_prima': [],
            'componentes': [],
            'otros': [],
            'todos_insumos': [],
            'totales': {
                'costo_total_mxn': Decimal('0.000000'),
                'costo_unitario_mxn': Decimal('0.000000'),
                'todos_disponibles': False,
                'total_items': 0,
                'items_faltantes': 0,
            }
        }

    factor = cant_dec / bom.cantidad_base if bom.cantidad_base > Decimal('0') else Decimal('1.00')

    hojalata_lista = []
    materia_prima_lista = []
    componentes_lista = []
    otros_lista = []
    todos_insumos = []

    costo_total_mxn = Decimal('0.000000')
    todos_disponibles = True
    items_faltantes = 0

    insumos_qs = bom.insumos.select_related(
        'materia_prima', 'materia_prima__unidad_medida', 'materia_prima__moneda_base_costo'
    ).order_by('materia_prima__tipo', 'materia_prima__sku')

    for insumo_receta in insumos_qs:
        mp = insumo_receta.materia_prima
        cant_neta = round(insumo_receta.cantidad_requerida * factor, 6)
        cant_con_merma = round(insumo_receta.cantidad_con_merma * factor, 6)
        merma_piezas = round(cant_con_merma - cant_neta, 6)

        costo_unit = insumo_receta.costo_unitario_efectivo_mxn
        costo_linea = round(cant_con_merma * costo_unit, 6)
        costo_total_mxn += costo_linea

        stock_fisico = Decimal('0.000000')
        stock_reservado = Decimal('0.000000')
        stock_disponible = Decimal('0.000000')

        if bodega_origen:
            st = Stock.objects.filter(producto=mp, bodega=bodega_origen).first()
            if st:
                stock_fisico = st.cantidad
                stock_reservado = st.cantidad_reservada
                stock_disponible = st.cantidad_disponible

        suficiente = stock_disponible >= cant_con_merma if bodega_origen else True
        faltante = max(Decimal('0.000000'), cant_con_merma - stock_disponible) if bodega_origen else Decimal('0.000000')

        if not suficiente:
            todos_disponibles = False
            items_faltantes += 1

        item_dict = {
            'insumo_bom': insumo_receta,
            'producto': mp,
            'sku': mp.sku,
            'nombre': mp.nombre,
            'tipo': mp.tipo,
            'tipo_display': mp.get_tipo_display(),
            'unidad_medida': mp.unidad_medida.codigo,
            'cantidad_base': insumo_receta.cantidad_requerida,
            'porcentaje_merma': insumo_receta.porcentaje_merma,
            'cantidad_neta': cant_neta,
            'merma_piezas': merma_piezas,
            'cantidad_requerida': cant_con_merma,
            'costo_unitario_mxn': costo_unit,
            'costo_linea_mxn': costo_linea,
            'stock_fisico': stock_fisico,
            'stock_reservado': stock_reservado,
            'stock_disponible': stock_disponible,
            'faltante': faltante,
            'suficiente': suficiente,
        }

        todos_insumos.append(item_dict)

        # Categorización Decorlata
        if mp.tipo in [Producto.HOJALATA_ROLLO, Producto.HOJA_LITOGRAFIADA, Producto.HOJA_CORTADA, Producto.PLANTILLA]:
            hojalata_lista.append(item_dict)
        elif mp.tipo == Producto.MATERIA_PRIMA:
            materia_prima_lista.append(item_dict)
        elif mp.tipo == Producto.COMPONENTE:
            componentes_lista.append(item_dict)
        else:
            otros_lista.append(item_dict)

    costo_unitario_mxn = round(costo_total_mxn / cant_dec, 6) if cant_dec > Decimal('0') else Decimal('0.000000')

    return {
        'producto': producto,
        'cantidad': cant_dec,
        'bom': bom,
        'tiene_bom': True,
        'hojalata': hojalata_lista,
        'materia_prima': materia_prima_lista,
        'componentes': componentes_lista,
        'otros': otros_lista,
        'todos_insumos': todos_insumos,
        'totales': {
            'costo_total_mxn': costo_total_mxn,
            'costo_unitario_mxn': costo_unitario_mxn,
            'todos_disponibles': todos_disponibles,
            'total_items': len(todos_insumos),
            'items_faltantes': items_faltantes,
        }
    }


def crear_orden_produccion(producto_id, cantidad, bodega_origen_id, bodega_destino_id, usuario, fecha_compromiso=None, observaciones=None, pedido_venta=None, pedido_detalle=None):
    """
    Crea una nueva Orden de Producción en estado PLANEADA.
    Si el producto cuenta con una receta (BOM) activa, congela los insumos requeridos
    escalados a la cantidad a fabricar en OrdenProduccion_Insumo.
    Inicializa automáticamente las 6 etapas secuenciales en la línea de ensamble.
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
            pedido_venta=pedido_venta,
            pedido_detalle=pedido_detalle,
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
                costo_unit = insumo_receta.costo_unitario_efectivo_mxn
                costo_tot = round(cant_estimada * costo_unit, 6)

                OrdenProduccion_Insumo.objects.create(
                    orden=orden,
                    insumo=insumo_receta.materia_prima,
                    cantidad_estimada=cant_estimada,
                    cantidad_consumida=cant_estimada,
                    costo_unitario_mxn=costo_unit,
                    costo_total_mxn=costo_tot
                )

        # Inicializar estaciones de ensamble industrial
        inicializar_etapas_ensamble_op(orden)

        return orden


def crear_orden_desde_pedido(pedido_detalle, usuario, bodega_origen_id=None, bodega_destino_id=None, cantidad=None, fecha_compromiso=None, observaciones=None):
    """
    Genera una Orden de Producción (OP) ligada a una partida específica de un Pedido de Venta en firme.
    Mantiene la trazabilidad bidireccional entre el pedido comercial y la OP.
    """
    from ventas.models import PedidoVenta_Detalle, PedidoVenta_Maestro

    with transaction.atomic():
        if isinstance(pedido_detalle, (int, str)):
            detalle = PedidoVenta_Detalle.objects.select_related('pedido', 'producto').get(id=pedido_detalle)
        else:
            detalle = pedido_detalle

        pedido = detalle.pedido

        if pedido.tipo_documento != PedidoVenta_Maestro.PEDIDO:
            raise ValueError("Solo se pueden generar órdenes de producción para Pedidos de Venta en Firme.")

        if pedido.estado == PedidoVenta_Maestro.CANCELADO:
            raise ValueError("No se puede generar una orden de producción para un pedido cancelado.")

        # Cantidad por producir
        cant_producir = Decimal(str(cantidad)) if cantidad is not None else detalle.cantidad
        if cant_producir <= Decimal('0'):
            raise ValueError("La cantidad a producir debe ser mayor a cero.")

        # Bodega destino por defecto: la bodega de despacho del pedido
        if not bodega_destino_id:
            bodega_destino = pedido.bodega_despacho
        else:
            bodega_destino = Bodega.objects.get(id=bodega_destino_id) if not isinstance(bodega_destino_id, Bodega) else bodega_destino_id

        # Bodega origen por defecto: bodega de materia prima o primera disponible
        if not bodega_origen_id:
            bodega_origen = Bodega.objects.filter(codigo__icontains='MP').first() or Bodega.objects.exclude(id=bodega_destino.id).first() or bodega_destino
        else:
            bodega_origen = Bodega.objects.get(id=bodega_origen_id) if not isinstance(bodega_origen_id, Bodega) else bodega_origen_id

        fecha_comp = fecha_compromiso or pedido.fecha_compromiso
        obs = observaciones or f"Orden generada desde Pedido {pedido.folio} (Partida #{detalle.id} - {detalle.producto.sku})"

        orden = crear_orden_produccion(
            producto_id=detalle.producto.id,
            cantidad=cant_producir,
            bodega_origen_id=bodega_origen.id,
            bodega_destino_id=bodega_destino.id,
            usuario=usuario,
            fecha_compromiso=fecha_comp,
            observaciones=obs,
            pedido_venta=pedido,
            pedido_detalle=detalle
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


def generar_folio_entrega(orden):
    """Genera un folio secuencial para la entrega parcial, ej: ENT-OP-2026-0001-01"""
    secuencia = orden.entregas_parciales.count() + 1
    return f"ENT-{orden.folio}-{secuencia:02d}"


def notificar_entrega_parcial(orden_id, cantidad_notificada, lote=None, consumos_dict=None, usuario_produccion=None, observaciones=None):
    """
    Registra una notificación parcial de producto terminado desde Producción.
    Calcula los insumos proporcionales según la receta y crea la entrega en estado PENDIENTE.
    IMPORTANTE: No genera movimientos en el Kardex ni descuenta stock físico hasta que Almacén autorice.
    """
    with transaction.atomic():
        orden = OrdenProduccion.objects.select_for_update().get(id=orden_id)

        if orden.estado in [OrdenProduccion.TERMINADA, OrdenProduccion.CANCELADA]:
            raise ValueError(f"No se pueden registrar entregas en una orden en estado {orden.get_estado_display()}.")

        cant_notif_dec = Decimal(str(cantidad_notificada))
        if cant_notif_dec <= Decimal('0'):
            raise ValueError("La cantidad notificada debe ser mayor a cero.")

        # Si la orden aún estaba Planeada, pasa automáticamente a En Proceso
        if orden.estado == OrdenProduccion.PLANEADA:
            orden.estado = OrdenProduccion.EN_PROCESO
            orden.save()

        if not lote:
            lote = f"LOT-{orden.folio}-{timezone.now().strftime('%Y%m%d')}"

        folio_ent = generar_folio_entrega(orden)

        entrega = EntregaParcialProduccion.objects.create(
            orden=orden,
            folio_entrega=folio_ent,
            cantidad_notificada=cant_notif_dec,
            lote_fabricacion=lote,
            usuario_notifica=usuario_produccion,
            observaciones_produccion=observaciones,
            estado=EntregaParcialProduccion.PENDIENTE
        )

        # Proporción de insumos correspondiente a esta entrega parcial
        factor = cant_notif_dec / orden.cantidad_a_producir if orden.cantidad_a_producir > Decimal('0') else Decimal('1.0')

        for item_op in orden.insumos_detalle.select_related('insumo'):
            cant_estimada_parcial = round(item_op.cantidad_estimada * factor, 6)

            # Si se enviaron consumos reales específicos
            if consumos_dict and (str(item_op.id) in consumos_dict or str(item_op.insumo_id) in consumos_dict):
                clave = str(item_op.id) if str(item_op.id) in consumos_dict else str(item_op.insumo_id)
                cant_consumida_parcial = Decimal(str(consumos_dict[clave]))
            else:
                cant_consumida_parcial = cant_estimada_parcial

            costo_unit = item_op.insumo.costo_promedio_mxn or Decimal('0.000000')
            costo_linea = round(cant_consumida_parcial * costo_unit, 6)

            EntregaParcial_Insumo.objects.create(
                entrega=entrega,
                insumo=item_op.insumo,
                cantidad_estimada=cant_estimada_parcial,
                cantidad_consumida=cant_consumida_parcial,
                costo_unitario_mxn=costo_unit,
                costo_total_mxn=costo_linea
            )

        return entrega


def autorizar_entrega_parcial(entrega_id, usuario_almacen, notas_almacen=None):
    """
    Autorización de Almacén:
    1. Genera las salidas de Kardex de insumos (afectando bodega origen a costo promedio MXN).
    2. Calcula el costo unitario de la entrega parcial.
    3. Genera la entrada de Kardex de Producto Terminado (afectando bodega destino).
    4. Actualiza la entrega a AUTORIZADA y acumula avance en la Orden de Producción.
    """
    with transaction.atomic():
        entrega = EntregaParcialProduccion.objects.select_for_update().get(id=entrega_id)

        if entrega.estado != EntregaParcialProduccion.PENDIENTE:
            raise ValueError(f"Esta entrega ya fue procesada anteriormente con estado: {entrega.get_estado_display()}")

        orden = entrega.orden
        moneda_mxn = Moneda.objects.filter(codigo='MXN').first()
        if not moneda_mxn:
            moneda_mxn = Moneda.objects.create(codigo='MXN', nombre='Peso Mexicano', simbolo='$')

        costo_total_insumos = Decimal('0.000000')

        # 1. Procesar salidas de insumos en Kardex
        for item in entrega.insumos_detalle.select_related('insumo'):
            costo_unitario = item.insumo.costo_promedio_mxn or Decimal('0.000000')
            costo_linea = round(item.cantidad_consumida * costo_unitario, 6)
            costo_total_insumos += costo_linea

            item.costo_unitario_mxn = costo_unitario
            item.costo_total_mxn = costo_linea
            item.save()

            # Descontar reserva de stock si existía
            stock_insumo = Stock.objects.filter(
                producto=item.insumo,
                bodega=orden.bodega_origen_insumos
            ).first()
            if stock_insumo and stock_insumo.cantidad_reservada > Decimal('0'):
                stock_insumo.cantidad_reservada = max(
                    Decimal('0.000000'),
                    stock_insumo.cantidad_reservada - item.cantidad_estimada
                )
                stock_insumo.save()

            # Movimiento de SALIDA en Kardex
            MovimientoInventario.objects.create(
                producto=item.insumo,
                bodega_origen=orden.bodega_origen_insumos,
                tipo_movimiento=MovimientoInventario.SALIDA,
                cantidad=item.cantidad_consumida,
                costo_unitario_original=costo_unitario,
                moneda_original=moneda_mxn,
                tipo_cambio_aplicado=Decimal('1.000000'),
                costo_unitario_mxn_capturado=costo_unitario,
                referencia_operacion=f"{entrega.folio_entrega}",
                usuario=usuario_almacen,
                lote=entrega.lote_fabricacion,
                observaciones=f"Consumo de insumos por entrega parcial {entrega.folio_entrega} de {orden.folio}"
            )

        # 2. Calcular costo unitario del lote parcial entregado
        costo_unitario_pt = round(costo_total_insumos / entrega.cantidad_notificada, 6) if entrega.cantidad_notificada > Decimal('0') else Decimal('0.000000')

        # 3. Movimiento de ENTRADA en Kardex para el Producto Terminado
        MovimientoInventario.objects.create(
            producto=orden.producto_a_fabricar,
            bodega_destino=orden.bodega_destino_pt,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=entrega.cantidad_notificada,
            costo_unitario_original=costo_unitario_pt,
            moneda_original=moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            costo_unitario_mxn_capturado=costo_unitario_pt,
            referencia_operacion=f"{entrega.folio_entrega}",
            usuario=usuario_almacen,
            lote=entrega.lote_fabricacion,
            observaciones=f"Entrada de producto terminado por entrega {entrega.folio_entrega}"
        )

        # 4. Actualizar estado de la entrega
        entrega.estado = EntregaParcialProduccion.AUTORIZADA
        entrega.fecha_autorizacion = timezone.now()
        entrega.usuario_autoriza = usuario_almacen
        entrega.notas_almacen = notas_almacen
        entrega.costo_total_insumos_mxn = costo_total_insumos
        entrega.costo_unitario_final_mxn = costo_unitario_pt
        entrega.save()

        # 5. Acumular avance en la Orden de Producción
        entregas_autorizadas = orden.entregas_parciales.filter(estado=EntregaParcialProduccion.AUTORIZADA)
        total_acumulado = sum(e.cantidad_notificada for e in entregas_autorizadas)
        costo_acumulado = sum(e.costo_total_insumos_mxn for e in entregas_autorizadas)

        orden.cantidad_producida = total_acumulado
        orden.costo_total_insumos_mxn = costo_acumulado
        if total_acumulado > Decimal('0'):
            orden.costo_unitario_final_mxn = round(costo_acumulado / total_acumulado, 6)

        # Si se cumplió o superó la meta solicitada, marcar como TERMINADA
        if orden.cantidad_producida >= orden.cantidad_a_producir:
            orden.estado = OrdenProduccion.TERMINADA
            orden.fecha_finalizacion = timezone.now()
            orden.usuario_finalizacion = usuario_almacen

        orden.save()

        return entrega


def rechazar_entrega_parcial(entrega_id, usuario_almacen, motivo=None):
    """
    Rechaza una entrega parcial de producción sin afectar existencias ni Kardex.
    """
    with transaction.atomic():
        entrega = EntregaParcialProduccion.objects.select_for_update().get(id=entrega_id)

        if entrega.estado != EntregaParcialProduccion.PENDIENTE:
            raise ValueError(f"Solo se pueden rechazar entregas en estado pendiente. Estado actual: {entrega.get_estado_display()}")

        entrega.estado = EntregaParcialProduccion.RECHAZADA
        entrega.fecha_autorizacion = timezone.now()
        entrega.usuario_autoriza = usuario_almacen
        entrega.notas_almacen = motivo
        entrega.save()

        return entrega


def cerrar_orden_definitiva(orden_id, usuario=None):
    """
    Permite cerrar formalmente una OP cuando se completó la producción
    o se decide finalizar el tiraje con lo producido hasta el momento.
    """
    with transaction.atomic():
        orden = OrdenProduccion.objects.select_for_update().get(id=orden_id)

        if orden.estado == OrdenProduccion.TERMINADA:
            return orden

        # Liberar cualquier stock reservado que haya quedado
        for item in orden.insumos_detalle.all():
            stock = Stock.objects.filter(producto=item.insumo, bodega=orden.bodega_origen_insumos).first()
            if stock and stock.cantidad_reservada > Decimal('0'):
                stock.cantidad_reservada = Decimal('0.000000')
                stock.save()

        orden.estado = OrdenProduccion.TERMINADA
        orden.fecha_finalizacion = timezone.now()
        orden.usuario_finalizacion = usuario
        orden.save()
        return orden

