# ERP/ventas/views.py

from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum

from django.template.loader import render_to_string
from weasyprint import HTML

from .models import (
    Cliente, PedidoVenta_Maestro, PedidoVenta_Detalle,
    Factura_Maestro, Factura_Detalle, ComplementoPago_Detalle
)
from .forms import ClienteForm, PedidoVenta_MaestroForm
from .services import (
    generar_folio_venta,
    resolver_tipo_cambio_pedido,
    convertir_cotizacion_a_pedido,
    verificar_disponibilidad_stock,
    reservar_stock_pedido,
    liberar_reserva_stock_pedido,
    surtir_pedido,
    numero_a_letras,
)
from .cfdi_services import (
    emitir_factura_desde_pedido,
    registrar_complemento_pago,
    generar_qr_sat_base64,
    construir_xml_cfdi40,
)

from inventario.models import Producto, Bodega, MovimientoInventario, Stock
from general.models import Moneda
from general.services import obtener_tipo_cambio_vigente


# ==============================================================================
# SECCIÓN 1: DIRECTORIO Y GESTIÓN DE CLIENTES
# ==============================================================================

@login_required
def clientes_catalogo_view(request):
    """
    Vista principal del directorio de clientes con filtrado y búsqueda en tiempo real vía HTMX.
    """
    query = request.GET.get('q', '').strip()
    filtro_estado = request.GET.get('estado', 'todos').strip()

    clientes = Cliente.objects.all().order_by('razon_social')

    # Filtro por texto
    if query:
        clientes = clientes.filter(
            Q(razon_social__icontains=query) |
            Q(nombre_comercial__icontains=query) |
            Q(rfc__icontains=query) |
            Q(correo__icontains=query) |
            Q(telefono__icontains=query)
        )

    # Filtro por estado activo/inactivo
    if filtro_estado == 'activos':
        clientes = clientes.filter(activo=True)
    elif filtro_estado == 'inactivos':
        clientes = clientes.filter(activo=False)

    # Petición HTMX retorna solo el fragmento de la tabla
    if request.headers.get('HX-Request'):
        return render(request, 'ventas/partials/_tabla_clientes.html', {
            'clientes': clientes,
            'query': query,
            'filtro_estado': filtro_estado,
        })

    # Petición estándar retorna la página completa
    total_clientes = Cliente.objects.count()
    activos_count = Cliente.objects.filter(activo=True).count()

    return render(request, 'ventas/clientes_catalogo.html', {
        'clientes': clientes,
        'query': query,
        'filtro_estado': filtro_estado,
        'total_clientes': total_clientes,
        'activos_count': activos_count,
    })


@login_required
def guardar_cliente_view(request, pk=None):
    """
    Crea o edita un cliente dentro del Offcanvas lateral mediante HTMX.
    """
    cliente = get_object_or_404(Cliente, pk=pk) if pk else None

    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            # Disparamos evento para cerrar Offcanvas y refrescar tabla sin recarga
            response = HttpResponse()
            response['HX-Trigger'] = 'clienteGuardado'
            return response
    else:
        form = ClienteForm(instance=cliente)

    return render(request, 'ventas/partials/_cliente_form.html', {
        'form': form,
        'cliente': cliente
    })


@login_required
def cliente_detalle_modal_view(request, pk):
    """
    Renderiza el modal con el expediente fiscal y comercial detallado del cliente.
    """
    cliente = get_object_or_404(Cliente, pk=pk)
    return render(request, 'ventas/partials/_modal_cliente_detalle.html', {
        'cliente': cliente
    })


@login_required
def cambiar_estado_cliente_view(request, pk):
    """
    Activa o desactiva un cliente mediante petición HTMX rápida.
    """
    cliente = get_object_or_404(Cliente, pk=pk)
    if request.method == 'POST':
        cliente.activo = not cliente.activo
        cliente.save()
        response = HttpResponse()
        response['HX-Trigger'] = 'clienteGuardado'
        return response

    return HttpResponse(status=405)


# ==============================================================================
# SECCIÓN 2: COTIZACIONES Y PEDIDOS DE VENTA (MAESTRO-DETALLE MULTIDIVISA)
# ==============================================================================

@login_required
def pedidos_catalogo_view(request):
    """
    Historial y catálogo general de Cotizaciones y Pedidos de Venta.
    Soporta filtrado dinámico por texto, tipo (COT/PED), estado y moneda vía HTMX.
    """
    query = request.GET.get('q', '').strip()
    filtro_tipo = request.GET.get('tipo', 'todos').strip()
    filtro_estado = request.GET.get('estado', 'todos').strip()
    filtro_moneda = request.GET.get('moneda', '').strip()

    pedidos = PedidoVenta_Maestro.objects.select_related(
        'cliente', 'bodega_despacho', 'moneda'
    ).all().order_by('-fecha_emision')

    # 1. Filtro por texto
    if query:
        pedidos = pedidos.filter(
            Q(folio__icontains=query) |
            Q(cliente__razon_social__icontains=query) |
            Q(cliente__nombre_comercial__icontains=query) |
            Q(cliente__rfc__icontains=query) |
            Q(referencia_cliente__icontains=query)
        )

    # 2. Filtro por tipo de documento
    if filtro_tipo in [PedidoVenta_Maestro.COTIZACION, PedidoVenta_Maestro.PEDIDO]:
        pedidos = pedidos.filter(tipo_documento=filtro_tipo)

    # 3. Filtro por estado
    if filtro_estado and filtro_estado != 'todos':
        pedidos = pedidos.filter(estado=filtro_estado)

    # 4. Filtro por moneda
    if filtro_moneda:
        pedidos = pedidos.filter(moneda_id=filtro_moneda)

    # Respuesta fragmentaria para HTMX
    if request.headers.get('HX-Request'):
        return render(request, 'ventas/partials/_tabla_pedidos.html', {
            'pedidos': pedidos,
            'query': query,
            'filtro_tipo': filtro_tipo,
            'filtro_estado': filtro_estado,
        })

    # Métricas para tarjetas de cabecera
    total_cotizaciones = PedidoVenta_Maestro.objects.filter(tipo_documento='COT').exclude(estado='CANCELADO').count()
    total_pedidos = PedidoVenta_Maestro.objects.filter(tipo_documento='PED').exclude(estado='CANCELADO').count()
    pedidos_confirmados = PedidoVenta_Maestro.objects.filter(estado='CONFIRMADO').count()

    cartera_mxn = PedidoVenta_Maestro.objects.filter(
        tipo_documento='PED'
    ).exclude(estado='CANCELADO').aggregate(total=Sum('total_mxn'))['total'] or Decimal('0.00')

    monedas = Moneda.objects.all().order_by('codigo')

    return render(request, 'ventas/pedidos_catalogo.html', {
        'pedidos': pedidos,
        'query': query,
        'filtro_tipo': filtro_tipo,
        'filtro_estado': filtro_estado,
        'filtro_moneda': filtro_moneda,
        'total_cotizaciones': total_cotizaciones,
        'total_pedidos': total_pedidos,
        'pedidos_confirmados': pedidos_confirmados,
        'cartera_mxn': cartera_mxn,
        'monedas': monedas,
    })


@login_required
def crear_pedido_view(request):
    """
    Formulario interactivo para captura de nueva Cotización o Pedido de Venta
    con partidas dinámicas, impuestos y resolución multidivisa oficial.
    """
    if request.method == 'POST':
        tipo_documento = request.POST.get('tipo_documento', PedidoVenta_Maestro.PEDIDO)
        cliente_id = request.POST.get('cliente')
        bodega_id = request.POST.get('bodega_despacho')
        moneda_id = request.POST.get('moneda')
        fecha_compromiso = request.POST.get('fecha_compromiso') or None
        condiciones_pago = request.POST.get('condiciones_pago', '').strip()
        referencia_cliente = request.POST.get('referencia_cliente', '').strip()
        observaciones = request.POST.get('observaciones', '').strip()
        tasa_iva_val = Decimal(str(request.POST.get('tasa_iva', '16.00') or '16.00'))

        # Partidas dinámicas
        productos = request.POST.getlist('producto[]')
        cantidades = request.POST.getlist('cantidad[]')
        precios = request.POST.getlist('precio_unitario[]')
        notas_partidas = request.POST.getlist('notas_linea[]')

        if not productos:
            messages.error(request, "Debe agregar al menos una partida de producto.")
            return redirect('ventas:pedido_crear')

        try:
            with transaction.atomic():
                cliente = get_object_or_404(Cliente, pk=cliente_id)
                moneda = get_object_or_404(Moneda, pk=moneda_id)
                bodega = get_object_or_404(Bodega, pk=bodega_id)

                # Resolver tipo de cambio oficial del día
                tipo_cambio_val = resolver_tipo_cambio_pedido(moneda)

                # Generar folio secuencial formal
                folio = generar_folio_venta(tipo_documento)

                estado_inicial = PedidoVenta_Maestro.ESTADO_COTIZADO if tipo_documento == PedidoVenta_Maestro.COTIZACION else PedidoVenta_Maestro.ESTADO_BORRADOR

                pedido = PedidoVenta_Maestro.objects.create(
                    folio=folio,
                    tipo_documento=tipo_documento,
                    cliente=cliente,
                    bodega_despacho=bodega,
                    moneda=moneda,
                    tipo_cambio_aplicado=tipo_cambio_val,
                    fecha_compromiso=fecha_compromiso,
                    estado=estado_inicial,
                    condiciones_pago=condiciones_pago or (f"{cliente.dias_credito} días" if cliente.dias_credito > 0 else "Contado"),
                    referencia_cliente=referencia_cliente,
                    observaciones=observaciones,
                    tasa_iva=tasa_iva_val,
                    uso_cfdi=cliente.uso_cfdi
                )

                # Crear cada una de las partidas
                for i, prod_id in enumerate(productos):
                    if not prod_id:
                        continue
                    cant = Decimal(str(cantidades[i] or '0'))
                    precio = Decimal(str(precios[i] or '0'))
                    nota = notas_partidas[i] if i < len(notas_partidas) else ''

                    if cant <= Decimal('0'):
                        continue

                    producto_obj = get_object_or_404(Producto, pk=prod_id)
                    PedidoVenta_Detalle.objects.create(
                        pedido=pedido,
                        producto=producto_obj,
                        cantidad=cant,
                        precio_unitario=precio,
                        notas_linea=nota
                    )

                # Recalcular totales con precisión monetaria
                pedido.recalcular_totales()

            tipo_nombre = "Cotización" if tipo_documento == 'COT' else "Pedido de Venta"
            messages.success(request, f"¡{tipo_nombre} {pedido.folio} registrada exitosamente!")
            return redirect('ventas:pedido_detalle', pk=pedido.id)

        except Exception as e:
            messages.error(request, f"Error al registrar la orden comercial: {str(e)}")
            return redirect('ventas:pedido_crear')

    # Petición GET: Carga de catálogos y tipos de cambio oficiales vigentes
    clientes = Cliente.objects.filter(activo=True).order_by('razon_social')
    bodegas = Bodega.objects.all().order_by('nombre')
    monedas = Moneda.objects.all().order_by('codigo')
    productos = Producto.objects.all().order_by('nombre')

    # Pre-cargar tipos de cambio oficiales del día
    tc_usd = Decimal('1.000000')
    tc_eur = Decimal('1.000000')
    try:
        tc_usd = obtener_tipo_cambio_vigente('USD')
    except Exception:
        tc_usd = Decimal('18.134300')

    try:
        tc_eur = obtener_tipo_cambio_vigente('EUR')
    except Exception:
        tc_eur = Decimal('20.312200')

    return render(request, 'ventas/pedido_form.html', {
        'clientes': clientes,
        'bodegas': bodegas,
        'monedas': monedas,
        'productos': productos,
        'tc_usd': tc_usd,
        'tc_eur': tc_eur,
    })


@login_required
def detalle_pedido_view(request, pk):
    """
    Vista detallada de la Cotización o Pedido de Venta con desglose de partidas,
    valuación contable en MXN, verificación de stock en tiempo real y panel de acciones comerciales.
    """
    pedido = get_object_or_404(
        PedidoVenta_Maestro.objects.select_related('cliente', 'bodega_despacho', 'moneda', 'usuario_surtio'),
        pk=pk
    )
    detalles = pedido.detalles.select_related('producto', 'producto__unidad_medida').all()

    # Análisis de inventario y disponibilidad en tiempo real
    disponibilidad = verificar_disponibilidad_stock(pedido)

    # Movimientos de Kardex asociados si ya fue surtido
    movimientos_kardex = []
    if pedido.estado == PedidoVenta_Maestro.ESTADO_SURTIDO:
        movimientos_kardex = MovimientoInventario.objects.filter(
            referencia_operacion=f"PED-{pedido.folio}"
        ).select_related('producto', 'usuario', 'bodega_origen').order_by('fecha')

    return render(request, 'ventas/pedido_detalle.html', {
        'pedido': pedido,
        'detalles': detalles,
        'disponibilidad': disponibilidad,
        'movimientos_kardex': movimientos_kardex,
    })


@login_required
def cambiar_estado_pedido_view(request, pk):
    """
    Actualiza el estado operativo del pedido (Borrador, Cotizado, Confirmado, Cancelado).
    Si se cancela un pedido con stock apartado, libera la reserva automáticamente.
    """
    pedido = get_object_or_404(PedidoVenta_Maestro, pk=pk)
    if request.method == 'POST':
        nuevo_estado = request.POST.get('nuevo_estado', '').strip()
        estados_validos = dict(PedidoVenta_Maestro.ESTADO_CHOICES)

        if pedido.estado == PedidoVenta_Maestro.ESTADO_SURTIDO and nuevo_estado == PedidoVenta_Maestro.ESTADO_CANCELADO:
            messages.error(request, "No es posible cancelar un pedido que ya fue surtido físicamente. Se requiere devolución formal en almacén.")
            return redirect('ventas:pedido_detalle', pk=pedido.id)

        if nuevo_estado in estados_validos:
            if nuevo_estado == PedidoVenta_Maestro.ESTADO_CANCELADO and pedido.stock_reservado:
                liberar_reserva_stock_pedido(pedido)
                messages.info(request, "Se ha liberado la reserva de existencias en bodega por cancelación del pedido.")

            pedido.estado = nuevo_estado
            pedido.save(update_fields=['estado'])
            messages.success(request, f"Estado del documento {pedido.folio} actualizado a '{estados_validos[nuevo_estado]}'.")
        else:
            messages.error(request, "Estado no válido.")

    return redirect('ventas:pedido_detalle', pk=pedido.id)


@login_required
def convertir_cotizacion_view(request, pk):
    """
    Convierte una Cotización ('COT') en Pedido en Firme ('PED') mediante servicio atómico.
    """
    pedido = get_object_or_404(PedidoVenta_Maestro, pk=pk)
    if request.method == 'POST':
        pedido_convertido = convertir_cotizacion_a_pedido(pedido.id)
        messages.success(
            request,
            f"¡Cotización convertida con éxito en Pedido en Firme con Folio {pedido_convertido.folio}!"
        )
        return redirect('ventas:pedido_detalle', pk=pedido_convertido.id)

    return redirect('ventas:pedido_detalle', pk=pedido.id)


# ==============================================================================
# SECCIÓN 3: ACCIONES DE ALMACÉN Y SURTIDO DE PEDIDOS (FASE 3)
# ==============================================================================

@login_required
def surtir_pedido_view(request, pk):
    """
    Despacha y surte formalmente el pedido de venta, descontando del inventario
    e insertando los movimientos de salida en el Kardex.
    """
    pedido = get_object_or_404(PedidoVenta_Maestro, pk=pk)
    if request.method == 'POST':
        try:
            surtir_pedido(pedido, usuario=request.user)
            messages.success(
                request,
                f"¡Éxito! El pedido {pedido.folio} ha sido surtido y la mercancía descontada físicamente del Kardex en '{pedido.bodega_despacho.nombre}'."
            )
        except Exception as e:
            messages.error(request, f"No se pudo surtir el pedido: {str(e)}")

    return redirect('ventas:pedido_detalle', pk=pedido.id)


@login_required
def reservar_stock_pedido_view(request, pk):
    """
    Aparta formalmente en almacén las existencias de este pedido.
    """
    pedido = get_object_or_404(PedidoVenta_Maestro, pk=pk)
    if request.method == 'POST':
        try:
            reservar_stock_pedido(pedido)
            messages.success(
                request,
                f"¡Stock apartado exitosamente para el pedido {pedido.folio} en '{pedido.bodega_despacho.nombre}'!"
            )
        except Exception as e:
            messages.error(request, f"No se pudo apartar el stock: {str(e)}")

    return redirect('ventas:pedido_detalle', pk=pedido.id)


@login_required
def liberar_reserva_stock_pedido_view(request, pk):
    """
    Libera el stock apartado en almacén para este pedido.
    """
    pedido = get_object_or_404(PedidoVenta_Maestro, pk=pk)
    if request.method == 'POST':
        try:
            liberar_reserva_stock_pedido(pedido)
            messages.info(
                request,
                f"Se ha liberado la reserva de stock para el pedido {pedido.folio}."
            )
        except Exception as e:
            messages.error(request, f"Error al liberar la reserva: {str(e)}")

    return redirect('ventas:pedido_detalle', pk=pedido.id)


# ==============================================================================
# SECCIÓN 4: GENERACIÓN FORMAL DE PDF CON WEASYPRINT (FASE 4)
# ==============================================================================

@login_required
def descargar_pedido_pdf_view(request, pk):
    """
    Genera y descarga el documento formal en PDF para Cotizaciones y Pedidos de Venta
    utilizando WeasyPrint, membrete industrial de Decorlata, términos comerciales
    y pagaré mercantil.
    """
    pedido = get_object_or_404(
        PedidoVenta_Maestro.objects.select_related(
            'cliente', 'bodega_despacho', 'moneda', 'usuario_surtio'
        ),
        pk=pk
    )
    detalles = pedido.detalles.select_related('producto', 'producto__unidad_medida').all()

    total_en_letras = numero_a_letras(pedido.total, pedido.moneda.codigo)

    context = {
        'pedido': pedido,
        'detalles': detalles,
        'total_en_letras': total_en_letras,
    }

    html_string = render_to_string('ventas/pedido_pdf.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf_bytes = html.write_pdf()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    nombre_archivo = f"{pedido.folio}.pdf"
    response['Content-Disposition'] = f'inline; filename="{nombre_archivo}"'
    return response


# ==============================================================================
# SECCIÓN 5: FACTURACIÓN ELECTRÓNICA SAT (CFDI 4.0) Y COMPLEMENTO DE PAGOS (FASE 5)
# ==============================================================================

@login_required
def facturas_catalogo_view(request):
    """
    Catálogo de Facturas y CFDI 4.0 con filtros reactivos por tipo, estado y buscador HTMX.
    """
    query = request.GET.get('q', '').strip()
    tipo_filtro = request.GET.get('tipo', '').strip()
    estado_filtro = request.GET.get('estado', '').strip()

    facturas = Factura_Maestro.objects.select_related(
        'cliente', 'moneda', 'pedido'
    ).prefetch_related('doctos_relacionados__factura_origen').all()

    if tipo_filtro:
        facturas = facturas.filter(tipo_comprobante=tipo_filtro)

    if estado_filtro:
        facturas = facturas.filter(estado=estado_filtro)

    if query:
        facturas = facturas.filter(
            Q(folio__icontains=query) |
            Q(serie__icontains=query) |
            Q(uuid__icontains=query) |
            Q(cliente__razon_social__icontains=query) |
            Q(cliente__rfc__icontains=query)
        )

    # Indicadores consolidados
    total_facturado_mxn = facturas.filter(
        tipo_comprobante=Factura_Maestro.TIPO_INGRESO,
        estado=Factura_Maestro.ESTADO_TIMBRADA
    ).aggregate(tot=Sum('total_mxn'))['tot'] or Decimal('0.00')

    total_saldo_pendiente = facturas.filter(
        tipo_comprobante=Factura_Maestro.TIPO_INGRESO,
        estado=Factura_Maestro.ESTADO_TIMBRADA,
        metodo_pago='PPD'
    ).aggregate(sal=Sum('saldo_insoluto'))['sal'] or Decimal('0.00')

    context = {
        'facturas': facturas,
        'query': query,
        'tipo_filtro': tipo_filtro,
        'estado_filtro': estado_filtro,
        'total_facturado_mxn': total_facturado_mxn,
        'total_saldo_pendiente': total_saldo_pendiente,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'ventas/partials/_tabla_facturas.html', context)

    return render(request, 'ventas/facturas_catalogo.html', context)


@login_required
def factura_detalle_view(request, pk):
    """
    Expediente fiscal completo de una factura o complemento de pago.
    Muestra datos del CFDI 4.0, desglose de conceptos o documentos relacionados,
    cadena original, timbrado SAT y opciones de descarga y cobranza.
    """
    factura = get_object_or_404(
        Factura_Maestro.objects.select_related('cliente', 'moneda', 'pedido'),
        pk=pk
    )

    detalles = factura.detalles.select_related('producto', 'producto__unidad_medida').all()
    pagos_recibidos = factura.pagos_recibidos.select_related('pago_cfdi').all()
    doctos_relacionados = factura.doctos_relacionados.select_related('factura_origen').all()

    qr_code_base64 = generar_qr_sat_base64(factura)

    monto_referencia = factura.total if factura.tipo_comprobante == Factura_Maestro.TIPO_INGRESO else sum(
        (dr.imp_pagado for dr in doctos_relacionados), Decimal('0.00')
    )
    importe_en_letras = numero_a_letras(monto_referencia, factura.moneda.codigo)

    context = {
        'factura': factura,
        'detalles': detalles,
        'pagos_recibidos': pagos_recibidos,
        'doctos_relacionados': doctos_relacionados,
        'qr_code_base64': qr_code_base64,
        'importe_en_letras': importe_en_letras,
    }
    return render(request, 'ventas/factura_detalle.html', context)


@login_required
def emitir_factura_pedido_view(request, pk):
    """
    Emite una Factura Fiscal Digital (CFDI 4.0) a partir de un Pedido de Venta en Firme surtido.
    """
    pedido = get_object_or_404(
        PedidoVenta_Maestro.objects.select_related('cliente', 'moneda', 'bodega_despacho'),
        pk=pk
    )

    if request.method == 'POST':
        metodo_pago = request.POST.get('metodo_pago', 'PPD')
        forma_pago = request.POST.get('forma_pago', '99' if metodo_pago == 'PPD' else '03')
        uso_cfdi = request.POST.get('uso_cfdi', 'G01')
        notas = request.POST.get('notas', '').strip()

        try:
            factura = emitir_factura_desde_pedido(
                pedido=pedido,
                usuario=request.user,
                metodo_pago=metodo_pago,
                forma_pago=forma_pago,
                uso_cfdi=uso_cfdi,
                notas=notas
            )
            messages.success(
                request,
                f"¡Factura {factura.folio_completo} emitida y timbrada exitosamente ante el SAT! UUID: {factura.uuid}"
            )
            return redirect('ventas:factura_detalle', pk=factura.id)
        except Exception as e:
            messages.error(request, f"Error al emitir factura: {str(e)}")
            return redirect('ventas:pedido_detalle', pk=pedido.id)

    # GET para renderizar el modal HTMX
    context = {'pedido': pedido}
    return render(request, 'ventas/partials/_modal_emitir_factura.html', context)


@login_required
def descargar_factura_xml_view(request, pk):
    """
    Descarga el archivo XML oficial timbrado del CFDI 4.0.
    """
    factura = get_object_or_404(
        Factura_Maestro.objects.select_related('cliente', 'moneda'),
        pk=pk
    )

    if factura.xml_firmado:
        try:
            with factura.xml_firmado.open('rb') as f:
                xml_content = f.read()
        except Exception:
            xml_content = construir_xml_cfdi40(factura).encode('utf-8')
    else:
        xml_content = construir_xml_cfdi40(factura).encode('utf-8')

    response = HttpResponse(xml_content, content_type='application/xml')
    nombre_archivo = f"CFDI_{factura.serie}_{factura.folio}_{factura.uuid}.xml"
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


@login_required
def descargar_factura_pdf_view(request, pk):
    """
    Genera y descarga la Representación Impresa en PDF con WeasyPrint
    conforme al formato industrial de Decorlata S.A. de C.V.
    """
    factura = get_object_or_404(
        Factura_Maestro.objects.select_related('cliente', 'moneda', 'pedido'),
        pk=pk
    )

    qr_code_base64 = generar_qr_sat_base64(factura)

    if factura.tipo_comprobante == Factura_Maestro.TIPO_PAGO:
        doctos = factura.doctos_relacionados.select_related('factura_origen', 'factura_origen__moneda').all()
        monto_total = sum((dr.imp_pagado for dr in doctos), Decimal('0.00'))
        importe_en_letras = numero_a_letras(monto_total, factura.moneda.codigo)
        template_name = 'ventas/recibo_pago_pdf.html'
        context = {
            'factura': factura,
            'monto_total_pagos': monto_total,
            'importe_en_letras': importe_en_letras,
            'qr_code_base64': qr_code_base64,
        }
    else:
        importe_en_letras = numero_a_letras(factura.total, factura.moneda.codigo)
        template_name = 'ventas/factura_pdf.html'
        context = {
            'factura': factura,
            'importe_en_letras': importe_en_letras,
            'qr_code_base64': qr_code_base64,
        }

    html_string = render_to_string(template_name, context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf_bytes = html.write_pdf()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    nombre_archivo = f"CFDI_{factura.serie}_{factura.folio}.pdf"
    response['Content-Disposition'] = f'inline; filename="{nombre_archivo}"'
    return response


@login_required
def registrar_pago_factura_view(request, pk):
    """
    Registra un abono o liquidación total contra una factura PPD,
    generando el Recibo Electrónico de Pago con Complemento de Pagos 2.0 (CFDI tipo 'P').
    """
    factura_origen = get_object_or_404(
        Factura_Maestro.objects.select_related('cliente', 'moneda'),
        pk=pk
    )

    if request.method == 'POST':
        try:
            monto_pago = Decimal(request.POST.get('monto_pago', '0.00'))
            forma_pago = request.POST.get('forma_pago', '03')
            num_operacion = request.POST.get('num_operacion', '').strip()
            notas = request.POST.get('notas', '').strip()

            pago_cfdi = registrar_complemento_pago(
                factura_origen=factura_origen,
                monto_pago=monto_pago,
                forma_pago=forma_pago,
                num_operacion=num_operacion,
                usuario=request.user,
                notas=notas
            )
            messages.success(
                request,
                f"¡Complemento de Pago {pago_cfdi.folio_completo} timbrado con éxito! Abono: ${monto_pago:.2f}. Saldo insoluto restante: ${factura_origen.saldo_insoluto:.2f}"
            )
            return redirect('ventas:factura_detalle', pk=factura_origen.id)
        except Exception as e:
            messages.error(request, f"Error al emitir complemento de pago: {str(e)}")
            return redirect('ventas:factura_detalle', pk=factura_origen.id)

    # GET para renderizar el modal HTMX
    context = {'factura': factura_origen}
    return render(request, 'ventas/partials/_modal_registrar_pago.html', context)

