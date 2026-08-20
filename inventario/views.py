# ERP/inventario/views.py

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q # <--- Importante para búsquedas avanzadas
from django.db import transaction
from .models import Producto, MovimientoInventario, Stock
from .forms import ProductoForm, MovimientoForm

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
    """Renderiza el historial general del Kardex. Retorna solo la tabla si es petición HTMX."""
    # Usamos select_related para hacer la consulta mucho más rápida y evitar saturar la base de datos
    movimientos = MovimientoInventario.objects.select_related(
        'producto', 'bodega_origen', 'bodega_destino', 'usuario'
    ).all().order_by('-fecha')[:100] # Limitamos a los últimos 100 para rendimiento visual

    if request.headers.get('HX-Request'):
        return render(request, 'inventario/partials/_tabla_movimientos.html', {'movimientos': movimientos})

    return render(request, 'inventario/movimientos.html', {'movimientos': movimientos})

@login_required
@permission_required('inventario.add_movimientoinventario', raise_exception=True)
def registrar_movimiento_view(request):
    """Guarda el registro de movimiento y altera matemáticamente el Stock."""
    if request.method == 'POST':
        form = MovimientoForm(request.POST)
        if form.is_valid():
            # Iniciar bloque atómico: O se guarda todo (Kardex + Stock), o no se guarda nada.
            with transaction.atomic():
                movimiento = form.save(commit=False)
                movimiento.usuario = request.user
                movimiento.save()

                # --- LÓGICA DE ACTUALIZACIÓN DE STOCK FÍSICO ---
                if movimiento.tipo_movimiento == MovimientoInventario.ENTRADA:
                    stock, created = Stock.objects.get_or_create(
                        producto=movimiento.producto,
                        bodega=movimiento.bodega_destino
                    )
                    stock.cantidad += movimiento.cantidad
                    stock.save()

                elif movimiento.tipo_movimiento == MovimientoInventario.SALIDA:
                    # En las salidas no usamos get_or_create porque ya validamos en el forms/models que sí exista
                    stock = Stock.objects.get(
                        producto=movimiento.producto,
                        bodega=movimiento.bodega_origen
                    )
                    stock.cantidad -= movimiento.cantidad
                    stock.save()

            # Avisamos al navegador (HTMX) que la operación fue un éxito
            response = HttpResponse()
            response['HX-Trigger'] = 'movimientoGuardado'
            return response

    else:
        form = MovimientoForm()

    return render(request, 'inventario/partials/_movimiento_form.html', {'form': form})