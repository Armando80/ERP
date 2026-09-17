# ERP/inventario/views.py

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.db import transaction
from .models import Producto, MovimientoInventario, Stock, Bodega
from .forms import ProductoForm, MovimientoForm
from produccion.models import EntregaParcialProduccion
from produccion.services import autorizar_entrega_parcial, rechazar_entrega_parcial


@login_required
def catalogo_view(request):
    """
    Vista principal que renderiza la pantalla del catálogo de productos.
    """
    productos = Producto.objects.all().order_by('sku')

    # 1. Capturar los parámetros de búsqueda y filtro enviados por el cliente
    query = request.GET.get('q', '').strip()
    tipo_filtro = request.GET.get('tipo', '').strip()

    # 2. Aplicar filtro por texto (SKU o Nombre)
    if query:
        productos = productos.filter(
            Q(sku__icontains=query) | Q(nombre__icontains=query)
        )

    # 3. Aplicar filtro por clasificación/tipo de producto (si se selecciona)
    if tipo_filtro:
        productos = productos.filter(tipo=tipo_filtro)

    # CORRECCIÓN: Usamos request.headers para detectar HTMX de forma nativa
    if request.headers.get('HX-Request'):
        return render(request, 'inventario/partials/_tabla_productos.html', {'productos': productos})

    return render(request, 'inventario/catalogo.html', {
        'productos': productos,
        'query': query,
        'tipo_filtro': tipo_filtro
    })

@login_required
@permission_required('inventario.change_producto', raise_exception=True) # Seguridad basada en roles
def guardar_producto_view(request, pk=None):
    """
    Vista que procesa el formulario del Offcanvas para crear o editar un producto.
    """
    # Si recibimos un PK, editamos el producto existente. Si no, creamos uno nuevo.
    if pk:
        producto = get_object_or_404(Producto, pk=pk)
    else:
        producto = Producto()

    if request.method == 'POST':
        form = ProductoForm(request.POST, instance=producto)
        if form.is_valid():
            form.save()

            # CONFIGURACIÓN ESPECIAL HTMX:
            # Le decimos al navegador que cierre el Offcanvas
            response = HttpResponse()
            response['HX-Trigger'] = 'productoGuardado'
            return response
    else:
        form = ProductoForm(instance=producto)

    # Renderizamos solo el pedacito de HTML del formulario para inyectarlo en el Offcanvas
    return render(request, 'inventario/partials/_producto_form.html', {'form': form, 'producto': producto})
    pass

@login_required
def movimientos_view(request):
    """
    Renderiza el historial del Kardex y procesa los filtros dinámicos.
    """
    # Consulta base optimizada
    movimientos = MovimientoInventario.objects.select_related(
        'producto', 'bodega_origen', 'bodega_destino', 'usuario'
    ).all().order_by('-fecha')

    # 1. Capturar los parámetros enviados por HTMX
    query = request.GET.get('q', '').strip()
    bodega_id = request.GET.get('bodega', '')
    tipo = request.GET.get('tipo', '')

    # 2. Aplicar los filtros si existen
    if query:
        movimientos = movimientos.filter(
            Q(producto__sku__icontains=query) |
            Q(referencia_operacion__icontains=query) |
            Q(producto__nombre__icontains=query)
        )

    if bodega_id:
        # Buscamos la bodega tanto si fue origen como si fue destino
        movimientos = movimientos.filter(
            Q(bodega_origen_id=bodega_id) | Q(bodega_destino_id=bodega_id)
        )

    if tipo:
        movimientos = movimientos.filter(tipo_movimiento=tipo)

    # 3. Retornar solo la tabla si es petición HTMX
    if request.headers.get('HX-Request'):
        return render(request, 'inventario/partials/_tabla_movimientos.html', {'movimientos': movimientos})

    # 4. Retornar página completa si es carga normal
    bodegas = Bodega.objects.all().order_by('nombre')
    total_pendientes = EntregaParcialProduccion.objects.filter(estado=EntregaParcialProduccion.PENDIENTE).count()

    return render(request, 'inventario/movimientos.html', {
        'movimientos': movimientos,
        'bodegas': bodegas,
        'query': query,
        'total_pendientes': total_pendientes
    })


@login_required
@permission_required('inventario.add_movimientoinventario', raise_exception=True)
def registrar_movimiento_view(request):
    """
    Guarda el registro de movimiento.
    Toda la lógica de Stock y Costo Promedio la maneja automáticamente
    el archivo signals.py en segundo plano.
    """
    if request.method == 'POST':
        form = MovimientoForm(request.POST)
        if form.is_valid():
            movimiento = form.save(commit=False)
            movimiento.usuario = request.user

            # Al ejecutar save(), se dispara la señal de signals.py que hace toda la magia
            movimiento.save()

            # Avisamos a HTMX para que cierre el panel y actualice tablas
            response = HttpResponse()
            response['HX-Trigger'] = 'movimientoGuardado'
            return response

    else:
        form = MovimientoForm()

    return render(request, 'inventario/partials/_movimiento_form.html', {'form': form})

# Asegúrate de importar Stock en la parte superior si aún no está
# from .models import Producto, Stock, MovimientoInventario

@login_required
@permission_required('inventario.view_stock', raise_exception=True)
def producto_stock_view(request, pk):
    """
    Recupera el stock desglosado por bodega para un producto y lo inyecta en un modal.
    """
    producto = get_object_or_404(Producto, pk=pk)

    # Traer existencias y usar select_related para optimizar la consulta a la base de datos
    existencias = Stock.objects.filter(producto=producto).select_related('bodega')

    # Calcular totales rápidos en Python
    total_fisico = sum(e.cantidad for e in existencias)
    total_reservado = sum(e.cantidad_reservada for e in existencias)
    total_disponible = total_fisico - total_reservado

    context = {
        'producto': producto,
        'existencias': existencias,
        'total_fisico': total_fisico,
        'total_reservado': total_reservado,
        'total_disponible': total_disponible,
    }

    # Retornamos únicamente el fragmento HTML del modal
    return render(request, 'inventario/partials/_modal_stock.html', context)

@login_required
def existencias_view(request):
    """
    Renderiza la tabla global de existencias con filtros por texto y bodega vía HTMX.
    """
    # Consulta base optimizada para no saturar la base de datos
    existencias = Stock.objects.select_related('producto', 'bodega').all().order_by('producto__sku', 'bodega__nombre')

    # 1. Capturar parámetros
    query = request.GET.get('q', '').strip()
    bodega_id = request.GET.get('bodega', '').strip()

    # 2. Aplicar filtro por texto (SKU o Nombre)
    if query:
        existencias = existencias.filter(
            Q(producto__sku__icontains=query) | Q(producto__nombre__icontains=query)
        )

    # 3. Aplicar filtro por Bodega
    if bodega_id:
        existencias = existencias.filter(bodega_id=bodega_id)

    # 4. Retorno para peticiones HTMX (Solo inyecta la tabla)
    if request.headers.get('HX-Request'):
        return render(request, 'inventario/partials/_tabla_existencias.html', {'existencias': existencias})

    # 5. Retorno para carga de página completa
    bodegas = Bodega.objects.all() # Traemos las bodegas reales para el filtro
    return render(request, 'inventario/existencias.html', {
        'existencias': existencias,
        'bodegas': bodegas,
        'query': query
    })


# ==============================================================================
# AUTORIZACIÓN DE ENTREGAS DE PRODUCCIÓN (ALMACÉN / KARDEX)
# ==============================================================================

@login_required
def entregas_pendientes_view(request):
    """
    Lista las entregas parciales de producción en espera de autorización de almacén.
    """
    entregas = EntregaParcialProduccion.objects.filter(
        estado=EntregaParcialProduccion.PENDIENTE
    ).select_related(
        'orden',
        'orden__producto_a_fabricar',
        'orden__producto_a_fabricar__unidad_medida',
        'orden__bodega_origen_insumos',
        'orden__bodega_destino_pt',
        'usuario_notifica'
    ).prefetch_related(
        'insumos_detalle__insumo',
        'insumos_detalle__insumo__unidad_medida'
    ).order_by('-fecha_notificacion')

    return render(request, 'inventario/partials/_tabla_pendientes_autorizacion.html', {
        'entregas': entregas,
        'total_pendientes': entregas.count()
    })


@login_required
def detalle_insumos_entrega_view(request, pk):
    """
    Modal/Offcanvas que muestra el desglose de materias primas que se descontarán
    al autorizar la entrega parcial de producción.
    """
    entrega = get_object_or_404(
        EntregaParcialProduccion.objects.select_related(
            'orden',
            'orden__producto_a_fabricar',
            'orden__producto_a_fabricar__unidad_medida',
            'orden__bodega_origen_insumos',
            'orden__bodega_destino_pt'
        ).prefetch_related(
            'insumos_detalle__insumo',
            'insumos_detalle__insumo__unidad_medida'
        ),
        pk=pk
    )

    return render(request, 'inventario/partials/_modal_insumos_entrega.html', {
        'entrega': entrega
    })


@login_required
def autorizar_entrega_view(request, pk):
    """
    Autoriza la entrada del producto terminado y el descuento de insumos en el Kardex.
    """
    if request.method == 'POST':
        try:
            notas = request.POST.get('notas_almacen', '').strip()
            entrega = autorizar_entrega_parcial(pk, request.user, notas_almacen=notas)

            messages.success(
                request,
                f"¡Entrega {entrega.folio_entrega} autorizada! Se ingresaron {entrega.cantidad_notificada} {entrega.orden.producto_a_fabricar.unidad_medida.codigo} al almacén y el Kardex fue actualizado."
            )
            response = HttpResponse()
            # Disparamos eventos HTMX para actualizar la tabla de pendientes y el Kardex
            response['HX-Trigger'] = 'entregaProcesada'
            return response
        except Exception as e:
            return HttpResponse(f"<div class='alert alert-danger py-2 small'>{str(e)}</div>", status=400)

    return HttpResponse(status=405)


@login_required
def rechazar_entrega_view(request, pk):
    """
    Rechaza la entrega parcial de producción sin afectar existencias.
    """
    if request.method == 'POST':
        try:
            motivo = request.headers.get('HX-Prompt') or request.POST.get('motivo_rechazo') or 'Rechazada por almacén'
            entrega = rechazar_entrega_parcial(pk, request.user, motivo=motivo.strip())

            messages.warning(
                request,
                f"La entrega {entrega.folio_entrega} ha sido rechazada y devuelta a Producción."
            )
            response = HttpResponse()
            response['HX-Trigger'] = 'entregaProcesada'
            return response
        except Exception as e:
            return HttpResponse(f"<div class='alert alert-danger py-2 small'>{str(e)}</div>", status=400)

    return HttpResponse(status=405)