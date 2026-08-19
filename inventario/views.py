# ERP/inventario/views.py

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required, permission_required
from .models import Producto
from .forms import ProductoForm

@login_required
def catalogo_view(request):
    """
    Vista principal que renderiza la pantalla del catálogo de productos.
    """
    productos = Producto.objects.all().order_by('sku')
    
    # Si la petición viene de HTMX (ej. al escribir en la barra de búsqueda)
    if request.htmx:
        return render(request, 'inventario/partials/_tabla_productos.html', {'productos': productos})
        
    # Si es una carga normal de la página
    return render(request, 'inventario/catalogo.html', {'productos': productos})

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