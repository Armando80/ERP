# ERP/ventas/services.py

from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from .models import PedidoVenta_Maestro, PedidoVenta_Detalle
from general.services import obtener_tipo_cambio_vigente, TipoCambioNoDisponibleError


def generar_folio_venta(tipo_documento='PED'):
    """
    Genera un folio secuencial anual con prefijo:
    - COT-YYYY-XXXX para Cotizaciones
    - PED-YYYY-XXXX para Pedidos de Venta
    """
    year = timezone.now().year
    prefijo = f"{tipo_documento}-{year}-"

    ultimo = PedidoVenta_Maestro.objects.filter(
        folio__startswith=prefijo
    ).order_by('-folio').first()

    if ultimo:
        try:
            secuencia = int(ultimo.folio.split('-')[-1]) + 1
        except (ValueError, IndexError):
            secuencia = 1
    else:
        secuencia = 1

    return f"{prefijo}{secuencia:04d}"


def resolver_tipo_cambio_pedido(moneda, fecha=None):
    """
    Obtiene el tipo de cambio oficial vigente (Banxico/DOF/BCE).
    Si ocurre alguna eventualidad, retorna 1.0 para MXN o fallback legal.
    """
    try:
        return obtener_tipo_cambio_vigente(moneda, fecha=fecha)
    except TipoCambioNoDisponibleError:
        return Decimal('1.000000')


def convertir_cotizacion_a_pedido(cotizacion_id):
    """
    Convierte una Cotización ('COT') en un Pedido de Venta en Firme ('PED').
    Asigna un nuevo folio PED y conserva el folio original en observaciones.
    """
    with transaction.atomic():
        orden = PedidoVenta_Maestro.objects.select_for_update().get(id=cotizacion_id)
        if orden.tipo_documento == PedidoVenta_Maestro.PEDIDO:
            return orden

        folio_cotizacion = orden.folio
        orden.tipo_documento = PedidoVenta_Maestro.PEDIDO
        orden.folio = generar_folio_venta('PED')
        orden.estado = PedidoVenta_Maestro.ESTADO_CONFIRMADO

        nota_conversion = f"[Origen: {folio_cotizacion} convertida a Pedido el {timezone.now().strftime('%d/%m/%Y %H:%M')}]"
        orden.observaciones = f"{nota_conversion}\n{orden.observaciones or ''}".strip()
        orden.save()
        return orden


# ==============================================================================
# FASE 3: INTEGRACIÓN ATÓMICA CON ALMACÉN (RESERVA DE STOCK Y KARDEX)
# ==============================================================================

from inventario.models import Stock, MovimientoInventario
from django.core.exceptions import ValidationError


def verificar_disponibilidad_stock(pedido):
    """
    Analiza la disponibilidad física y disponible de cada partida del pedido
    en la bodega de despacho seleccionada.
    Retorna un diccionario estructurado con el estado de cada partida y banderas globales.
    """
    partidas_info = []
    hay_stock_fisico_completo = True
    hay_disponible_completo = True

    detalles = pedido.detalles.select_related('producto', 'producto__unidad_medida').prefetch_related('ordenes_produccion').all()
    for d in detalles:
        stock = Stock.objects.filter(producto=d.producto, bodega=pedido.bodega_despacho).first()
        stock_fisico = stock.cantidad if stock else Decimal('0.000000')
        stock_reservado = stock.cantidad_reservada if stock else Decimal('0.000000')

        # Si el pedido ya tiene stock reservado, esa reserva ya protege su requerimiento
        if pedido.stock_reservado:
            disponible_efectivo = (stock_fisico - stock_reservado) + d.cantidad
        else:
            disponible_efectivo = stock_fisico - stock_reservado

        tiene_fisico = stock_fisico >= d.cantidad
        tiene_disponible = disponible_efectivo >= d.cantidad
        faltante_fisico = max(Decimal('0.000000'), d.cantidad - stock_fisico)
        faltante_disponible = max(Decimal('0.000000'), d.cantidad - disponible_efectivo)

        if not tiene_fisico:
            hay_stock_fisico_completo = False
        if not tiene_disponible:
            hay_disponible_completo = False

        partidas_info.append({
            'detalle': d,
            'producto': d.producto,
            'cantidad_requerida': d.cantidad,
            'stock_fisico': stock_fisico,
            'stock_reservado': stock_reservado,
            'stock_disponible': stock_fisico - stock_reservado,
            'disponible_efectivo': disponible_efectivo,
            'tiene_fisico': tiene_fisico,
            'tiene_disponible': tiene_disponible,
            'faltante_fisico': faltante_fisico,
            'faltante_disponible': faltante_disponible,
        })

    return {
        'partidas': partidas_info,
        'hay_stock_fisico_completo': hay_stock_fisico_completo,
        'hay_disponible_completo': hay_disponible_completo,
        'total_partidas': len(partidas_info)
    }


def reservar_stock_pedido(pedido):
    """
    Aparta formalmente en la bodega de despacho el stock físico requerido
    para cada partida del Pedido de Venta en Firme.
    Incrementa 'cantidad_reservada' en el modelo Stock.
    """
    if pedido.tipo_documento != PedidoVenta_Maestro.PEDIDO:
        raise ValidationError("Solo los Pedidos de Venta en Firme pueden apartar existencias.")

    if pedido.estado in [PedidoVenta_Maestro.CANCELADO, PedidoVenta_Maestro.SURTIDO]:
        raise ValidationError(f"No se puede reservar stock en un pedido con estado '{pedido.get_estado_display()}'.")

    if pedido.stock_reservado:
        raise ValidationError("El stock de este pedido ya se encuentra reservado en el almacén.")

    with transaction.atomic():
        detalles = pedido.detalles.select_related('producto').all()
        if not detalles.exists():
            raise ValidationError("El pedido no tiene partidas para reservar.")

        errores = []
        stocks_a_actualizar = []

        for d in detalles:
            stock = Stock.objects.select_for_update().filter(
                producto=d.producto,
                bodega=pedido.bodega_despacho
            ).first()

            if not stock or stock.cantidad_disponible < d.cantidad:
                disp = stock.cantidad_disponible if stock else Decimal('0')
                errores.append(
                    f"'{d.producto.nombre}' ({d.producto.sku}): Disponible {disp}, Requerido {d.cantidad}"
                )
            else:
                stock.cantidad_reservada += d.cantidad
                stocks_a_actualizar.append(stock)

        if errores:
            mensaje = "No es posible reservar el pedido por existencias insuficientes:\n" + "\n".join(errores)
            raise ValidationError(mensaje)

        for s in stocks_a_actualizar:
            s.save(update_fields=['cantidad_reservada'])

        pedido.stock_reservado = True
        pedido.save(update_fields=['stock_reservado'])
        return pedido


def liberar_reserva_stock_pedido(pedido):
    """
    Libera la reserva de stock previamente apartada para el pedido en la bodega de despacho.
    Restaura 'cantidad_reservada' en el modelo Stock.
    """
    with transaction.atomic():
        if not pedido.stock_reservado:
            return pedido

        detalles = pedido.detalles.select_related('producto').all()
        for d in detalles:
            stock = Stock.objects.select_for_update().filter(
                producto=d.producto,
                bodega=pedido.bodega_despacho
            ).first()
            if stock:
                stock.cantidad_reservada = max(Decimal('0.000000'), stock.cantidad_reservada - d.cantidad)
                stock.save(update_fields=['cantidad_reservada'])

        pedido.stock_reservado = False
        pedido.save(update_fields=['stock_reservado'])
        return pedido


def surtir_pedido(pedido, usuario):
    """
    Surtido y despacho físico formal del Pedido de Venta en la bodega de despacho:
    1. Valida que sea un pedido en firme y no esté cancelado ni surtido.
    2. Valida existencias físicas suficientes (Stock.cantidad >= d.cantidad) en la bodega de despacho.
    3. Si el pedido tenía stock reservado, libera la reserva en 'cantidad_reservada'.
    4. Genera el MovimientoInventario de tipo SALIDA ('S') en el Kardex.
       (El signal 'aplicar_movimiento_kardex' descuenta físicamente el stock).
    5. Actualiza 'cantidad_surtida' en cada partida.
    6. Marca el pedido como SURTIDO con fecha_surtido y usuario_surtio.
    Todo el proceso es 100% atómico; si algo falla, no se afecta el inventario.
    """
    if pedido.tipo_documento != PedidoVenta_Maestro.PEDIDO:
        raise ValidationError("Solo los Pedidos de Venta en Firme pueden ser surtidos.")

    if pedido.estado == PedidoVenta_Maestro.SURTIDO:
        raise ValidationError("Este pedido ya fue surtido y despachado del almacén.")

    if pedido.estado == PedidoVenta_Maestro.CANCELADO:
        raise ValidationError("No se puede surtir un pedido cancelado.")

    with transaction.atomic():
        detalles = list(pedido.detalles.select_related('producto').all())
        if not detalles:
            raise ValidationError("El pedido no contiene partidas para surtir.")

        # 1. Validación previa de existencia física para TODOS los productos
        errores = []
        for d in detalles:
            stock = Stock.objects.select_for_update().filter(
                producto=d.producto,
                bodega=pedido.bodega_despacho
            ).first()

            if not stock or stock.cantidad < d.cantidad:
                fisico = stock.cantidad if stock else Decimal('0')
                errores.append(
                    f"'{d.producto.nombre}' ({d.producto.sku}): Físico en bodega {fisico}, Requerido {d.cantidad}"
                )

        if errores:
            mensaje = "No se puede surtir el pedido por falta de existencias físicas en la bodega de despacho:\n" + "\n".join(errores)
            raise ValidationError(mensaje)

        # 2. Procesar salidas de Kardex y liberar reserva
        for d in detalles:
            stock = Stock.objects.select_for_update().filter(
                producto=d.producto,
                bodega=pedido.bodega_despacho
            ).first()

            # Liberar reserva si estaba apartado
            if pedido.stock_reservado and stock:
                stock.cantidad_reservada = max(Decimal('0.000000'), stock.cantidad_reservada - d.cantidad)
                stock.save(update_fields=['cantidad_reservada'])

            # Resolver costo contable unitario en MXN de salida desde el Kardex / producto
            costo_salida_mxn = d.producto.costo_promedio_mxn or Decimal('0.000000')

            # Registrar Movimiento de Salida en el Kardex
            MovimientoInventario.objects.create(
                producto=d.producto,
                bodega_origen=pedido.bodega_despacho,
                tipo_movimiento=MovimientoInventario.SALIDA,
                cantidad=d.cantidad,
                moneda_original=pedido.moneda,
                costo_unitario_original=d.precio_unitario,
                tipo_cambio_aplicado=pedido.tipo_cambio_aplicado,
                costo_unitario_mxn_capturado=costo_salida_mxn,
                referencia_operacion=f"PED-{pedido.folio}",
                usuario=usuario,
                observaciones=f"Salida por Surtido de Pedido {pedido.folio} a cliente: {pedido.cliente.razon_social}"
            )

            # Actualizar cantidad surtida en la partida
            d.cantidad_surtida = d.cantidad
            d.save(update_fields=['cantidad_surtida'])

        # 3. Actualizar estado y fecha en el maestro
        pedido.estado = PedidoVenta_Maestro.ESTADO_SURTIDO
        pedido.fecha_surtido = timezone.now()
        pedido.usuario_surtio = usuario
        pedido.stock_reservado = False
        pedido.save(update_fields=['estado', 'fecha_surtido', 'usuario_surtio', 'stock_reservado'])

        return pedido


# ==============================================================================
# FASE 4: SERVICIOS DE DOCUMENTACIÓN FORMAL Y WEASYPRINT
# ==============================================================================

def numero_a_letras(monto, moneda_codigo='MXN'):
    """
    Convierte una cantidad numérica a texto en español con formato legal y mercantil mexicano:
    Ejemplo: Decimal('1250.50') -> 'UN MIL DOSCIENTOS CINCUENTA PESOS 50/100 M.N.'
    Soporta MXN, USD y EUR.
    """
    if monto is None:
        return ""

    monto = Decimal(str(monto))
    entero = int(monto)
    centavos = int(round((monto - Decimal(entero)) * 100))

    unidades = ["", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"]
    decenas_10_19 = ["DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE"]
    decenas = ["", "DIEZ", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
    centenas = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]

    def _seccion(n):
        if n == 0:
            return ""
        if n == 100:
            return "CIEN"

        c = n // 100
        resto = n % 100
        partes = []

        if c > 0:
            partes.append(centenas[c])

        if resto >= 10 and resto <= 19:
            partes.append(decenas_10_19[resto - 10])
        elif resto >= 20 and resto <= 29:
            if resto == 20:
                partes.append("VEINTE")
            else:
                partes.append("VEINTI" + unidades[resto - 20])
        else:
            d = resto // 10
            u = resto % 10
            if d > 0:
                partes.append(decenas[d])
            if d > 0 and u > 0:
                partes.append("Y")
            if u > 0:
                partes.append(unidades[u])

        return " ".join([p for p in partes if p])

    if entero == 0:
        texto_entero = "CERO"
    else:
        partes_texto = []
        millones = entero // 1000000
        resto_millones = entero % 1000000
        miles = resto_millones // 1000
        unidades_resto = resto_millones % 1000

        if millones > 0:
            if millones == 1:
                partes_texto.append("UN MILLÓN")
            else:
                partes_texto.append(f"{_seccion(millones)} MILLONES")

        if miles > 0:
            if miles == 1:
                partes_texto.append("UN MIL")
            else:
                partes_texto.append(f"{_seccion(miles)} MIL")

        if unidades_resto > 0:
            partes_texto.append(_seccion(unidades_resto))

        texto_entero = " ".join(partes_texto)

    moneda_upper = (moneda_codigo or 'MXN').upper()
    if moneda_upper == 'USD':
        nombre_moneda = "DÓLAR" if entero == 1 else "DÓLARES"
        sufijo = f"{nombre_moneda} {centavos:02d}/100 USD"
    elif moneda_upper == 'EUR':
        nombre_moneda = "EURO" if entero == 1 else "EUROS"
        sufijo = f"{nombre_moneda} {centavos:02d}/100 EUR"
    else:
        nombre_moneda = "PESO" if entero == 1 else "PESOS"
        sufijo = f"{nombre_moneda} {centavos:02d}/100 M.N."

    return f"{texto_entero} {sufijo}".strip()
