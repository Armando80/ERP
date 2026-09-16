# ERP Decorlata - produccion/views.py
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from inventario.models import Producto, Bodega, Stock
from .models import ListaMaterialesBOM, InsumoBOM, OrdenProduccion, OrdenProduccion_Insumo
from .forms import ListaMaterialesBOMForm, InsumoBOMForm, OrdenProduccionForm, FinalizarOrdenForm
from .services import (
    crear_orden_produccion,
    iniciar_orden_produccion,
    finalizar_orden_produccion,
    cancelar_orden_produccion
)


# ==============================================================================
# FÓRMULAS Y LISTAS DE MATERIALES (BOM)
# ==============================================================================

@login_required
def boms_catalogo_view(request):
    """Muestra el catálogo de recetas (BOM) con búsqueda en tiempo real vía HTMX."""
    query = request.GET.get('q', '').strip()
    boms = ListaMaterialesBOM.objects.select_related(
        'producto_terminado', 'producto_terminado__unidad_medida'
    ).prefetch_related(
        'insumos__materia_prima', 'insumos__materia_prima__unidad_medida'
    ).all()

    if query:
        boms = boms.filter(
            Q(producto_terminado__nombre__icontains=query) |
            Q(producto_terminado__sku__icontains=query)
        )

    if request.headers.get('HX-Request') and not request.headers.get('HX-Target') == 'main-content':
        return render(request, 'produccion/partials/_tabla_boms.html', {'boms': boms})

    return render(request, 'produccion/boms_catalogo.html', {'boms': boms, 'query': query})


@login_required
def crear_editar_bom_view(request, pk=None):
    """Crea o edita la cabecera de una receta (BOM) en un Offcanvas."""
    bom = get_object_or_404(ListaMaterialesBOM, pk=pk) if pk else None

    if request.method == 'POST':
        form = ListaMaterialesBOMForm(request.POST, instance=bom)
        if form.is_valid():
            nueva_bom = form.save()
            response = HttpResponse()
            response['HX-Trigger'] = 'bomGuardado'
            return response
    else:
        form = ListaMaterialesBOMForm(instance=bom)

    return render(request, 'produccion/partials/_bom_form.html', {'form': form, 'bom': bom})


@login_required
def detalle_bom_view(request, pk):
    """Vista detallada de una receta con administración dinámica de sus insumos."""
    bom = get_object_or_404(
        ListaMaterialesBOM.objects.select_related(
            'producto_terminado', 'producto_terminado__unidad_medida'
        ).prefetch_related(
            'insumos__materia_prima', 'insumos__materia_prima__unidad_medida'
        ),
        pk=pk
    )
    insumo_form = InsumoBOMForm()
    return render(request, 'produccion/bom_detalle.html', {
        'bom': bom,
        'insumo_form': insumo_form
    })


@login_required
def agregar_insumo_bom_view(request, bom_id):
    """Endpoint HTMX para añadir una materia prima a la receta."""
    bom = get_object_or_404(ListaMaterialesBOM, pk=bom_id)

    if request.method == 'POST':
        form = InsumoBOMForm(request.POST)
        if form.is_valid():
            insumo = form.save(commit=False)
            insumo.bom = bom
            insumo.save()
            # Devolvemos la tabla actualizada de insumos
            return render(request, 'produccion/partials/_tabla_insumos_bom.html', {'bom': bom})
        else:
            return HttpResponse(f"<div class='alert alert-danger py-1 small'>{form.errors.as_text()}</div>", status=400)

    return HttpResponse(status=405)


@login_required
def eliminar_insumo_bom_view(request, pk):
    """Endpoint HTMX para eliminar un insumo de la receta."""
    insumo = get_object_or_404(InsumoBOM, pk=pk)
    bom = insumo.bom
    if request.method == 'POST':
        insumo.delete()
        return render(request, 'produccion/partials/_tabla_insumos_bom.html', {'bom': bom})
    return HttpResponse(status=405)


# ==============================================================================
# ÓRDENES DE PRODUCCIÓN (OP)
# ==============================================================================

@login_required
def ordenes_catalogo_view(request):
    """Listado y seguimiento de Órdenes de Producción con filtrado dinámico HTMX."""
    query = request.GET.get('q', '').strip()
    estado_filtro = request.GET.get('estado', '').strip()

    ordenes = OrdenProduccion.objects.select_related(
        'producto_a_fabricar',
        'producto_a_fabricar__unidad_medida',
        'bodega_origen_insumos',
        'bodega_destino_pt',
        'usuario_creacion'
    ).all()

    if query:
        ordenes = ordenes.filter(
            Q(folio__icontains=query) |
            Q(producto_a_fabricar__nombre__icontains=query) |
            Q(producto_a_fabricar__sku__icontains=query) |
            Q(lote_fabricacion__icontains=query)
        )

    if estado_filtro:
        ordenes = ordenes.filter(estado=estado_filtro)

    if request.headers.get('HX-Request') and not request.headers.get('HX-Target') == 'main-content':
        return render(request, 'produccion/partials/_tabla_ordenes.html', {'ordenes': ordenes})

    return render(request, 'produccion/ordenes_catalogo.html', {
        'ordenes': ordenes,
        'query': query,
        'estado_filtro': estado_filtro
    })


@login_required
def crear_orden_produccion_view(request):
    """Formulario interactivo para registrar una nueva OP con cálculo previo de insumos."""
    if request.method == 'POST':
        form = OrdenProduccionForm(request.POST)
        if form.is_valid():
            try:
                orden = crear_orden_produccion(
                    producto_id=form.cleaned_data['producto_a_fabricar'].id,
                    cantidad=form.cleaned_data['cantidad_a_producir'],
                    bodega_origen_id=form.cleaned_data['bodega_origen_insumos'].id,
                    bodega_destino_id=form.cleaned_data['bodega_destino_pt'].id,
                    usuario=request.user,
                    fecha_compromiso=form.cleaned_data.get('fecha_compromiso'),
                    observaciones=form.cleaned_data.get('observaciones')
                )
                messages.success(request, f"¡Orden de Producción {orden.folio} creada exitosamente!")
                return redirect('produccion:detalle_orden', pk=orden.pk)
            except Exception as e:
                messages.error(request, f"Error al crear la orden: {str(e)}")
    else:
        form = OrdenProduccionForm()

    return render(request, 'produccion/orden_produccion_form.html', {'form': form})


@login_required
def previsualizar_bom_op_view(request):
    """
    Endpoint HTMX para previsualizar los insumos requeridos y comprobar el stock físico
    en la bodega de origen seleccionada antes de confirmar la OP.
    """
    producto_id = request.GET.get('producto_a_fabricar')
    cantidad = request.GET.get('cantidad_a_producir')
    bodega_origen_id = request.GET.get('bodega_origen_insumos')

    if not producto_id:
        return HttpResponse("<div class='text-muted small p-3 text-center'>Selecciona un producto para visualizar sus requerimientos de manufactura.</div>")

    try:
        producto = Producto.objects.select_related('receta_bom', 'unidad_medida').get(id=producto_id)
        bom = getattr(producto, 'receta_bom', None)
    except Producto.DoesNotExist:
        return HttpResponse("<div class='text-danger small p-3'>Producto no encontrado.</div>")

    if not bom or not bom.activo:
        return HttpResponse(
            "<div class='alert alert-warning small my-2'>"
            "<i class='bi bi-exclamation-triangle me-1'></i> Este producto no tiene una receta (BOM) activa. "
            "Crea primero una Lista de Materiales en el módulo correspondiente.</div>"
        )

    try:
        cant_dec = Decimal(str(cantidad)) if cantidad else Decimal('1.0000')
        if cant_dec <= Decimal('0'):
            cant_dec = Decimal('1.0000')
    except Exception:
        cant_dec = Decimal('1.0000')

    factor = cant_dec / bom.cantidad_base if bom.cantidad_base > Decimal('0') else Decimal('1.00')

    bodega_origen = Bodega.objects.filter(id=bodega_origen_id).first() if bodega_origen_id else None

    # Recorremos insumos y verificamos stock disponible
    insumos_info = []
    costo_estimado_total = Decimal('0.000000')
    todos_disponibles = True

    for insumo_receta in bom.insumos.select_related('materia_prima', 'materia_prima__unidad_medida'):
        cant_requerida = round(insumo_receta.cantidad_con_merma * factor, 6)
        costo_unit = insumo_receta.materia_prima.costo_promedio_mxn or Decimal('0.000000')
        costo_linea = round(cant_requerida * costo_unit, 6)
        costo_estimado_total += costo_linea

        stock_disp = Decimal('0.000000')
        if bodega_origen:
            stock_obj = Stock.objects.filter(producto=insumo_receta.materia_prima, bodega=bodega_origen).first()
            if stock_obj:
                stock_disp = stock_obj.cantidad_disponible

        suficiente = stock_disp >= cant_requerida if bodega_origen else True
        if not suficiente:
            todos_disponibles = False

        insumos_info.append({
            'insumo': insumo_receta.materia_prima,
            'cantidad_requerida': cant_requerida,
            'costo_unitario': costo_unit,
            'costo_linea': costo_linea,
            'stock_disponible': stock_disp,
            'suficiente': suficiente
        })

    costo_unitario_estimado = round(costo_estimado_total / cant_dec, 6) if cant_dec > Decimal('0') else Decimal('0.000000')

    return render(request, 'produccion/partials/_preview_bom_op.html', {
        'bom': bom,
        'producto': producto,
        'cantidad': cant_dec,
        'insumos_info': insumos_info,
        'costo_estimado_total': costo_estimado_total,
        'costo_unitario_estimado': costo_unitario_estimado,
        'bodega_origen': bodega_origen,
        'todos_disponibles': todos_disponibles
    })


@login_required
def detalle_orden_produccion_view(request, pk):
    """Ficha técnica de la OP con insumos programados vs consumidos y trazabilidad."""
    orden = get_object_or_404(
        OrdenProduccion.objects.select_related(
            'producto_a_fabricar',
            'producto_a_fabricar__unidad_medida',
            'bodega_origen_insumos',
            'bodega_destino_pt',
            'usuario_creacion',
            'usuario_finalizacion',
            'bom'
        ).prefetch_related(
            'insumos_detalle__insumo',
            'insumos_detalle__insumo__unidad_medida'
        ),
        pk=pk
    )
    finalizar_form = FinalizarOrdenForm(initial={'cantidad_producida': orden.cantidad_a_producir})

    return render(request, 'produccion/orden_produccion_detalle.html', {
        'orden': orden,
        'finalizar_form': finalizar_form
    })


@login_required
def iniciar_orden_view(request, pk):
    """Transiciona la OP de PLANEADA a EN PROCESO y reserva insumos."""
    if request.method == 'POST':
        try:
            orden = iniciar_orden_produccion(pk, request.user)
            messages.success(request, f"¡La orden {orden.folio} está ahora EN PROCESO de manufactura!")
        except Exception as e:
            messages.error(request, f"Error al iniciar orden: {str(e)}")

    return redirect('produccion:detalle_orden', pk=pk)


@login_required
def finalizar_orden_view(request, pk):
    """Cierra la OP, captura insumos consumidos y alimenta el Kardex."""
    orden = get_object_or_404(OrdenProduccion, pk=pk)

    if request.method == 'POST':
        form = FinalizarOrdenForm(request.POST)
        if form.is_valid():
            try:
                cantidad_real = form.cleaned_data['cantidad_producida']
                lote = form.cleaned_data.get('lote_fabricacion') or f"LOT-{orden.folio}"

                # Opcionalmente capturar consumos específicos si vinieron en el formulario
                consumos_dict = {}
                for key, val in request.POST.items():
                    if key.startswith('consumo_'):
                        detalle_id = key.replace('consumo_', '')
                        consumos_dict[detalle_id] = val

                finalizar_orden_produccion(
                    orden_id=orden.id,
                    cantidad_real=cantidad_real,
                    lote=lote,
                    consumos_dict=consumos_dict,
                    usuario=request.user
                )
                messages.success(request, f"¡Producción de {orden.folio} completada! Stock de {orden.producto_a_fabricar.sku} incrementado y Kardex actualizado.")
            except Exception as e:
                messages.error(request, f"Error al finalizar orden: {str(e)}")
        else:
            messages.error(request, "Datos inválidos para finalizar la producción.")

    return redirect('produccion:detalle_orden', pk=pk)


@login_required
def cancelar_orden_view(request, pk):
    """Cancela la OP y libera reservas de stock."""
    if request.method == 'POST':
        try:
            orden = cancelar_orden_produccion(pk, request.user)
            messages.warning(request, f"La orden {orden.folio} ha sido CANCELADA.")
        except Exception as e:
            messages.error(request, f"Error al cancelar la orden: {str(e)}")

    return redirect('produccion:detalle_orden', pk=pk)


# ==============================================================================
# HOJA DE VIAJERO / ORDEN DE TRABAJO (PDF)
# ==============================================================================

@login_required
def descargar_hoja_viajero_pdf_view(request, pk):
    """Genera la Hoja de Producción en PDF con WeasyPrint para los operarios de planta."""
    orden = get_object_or_404(
        OrdenProduccion.objects.select_related(
            'producto_a_fabricar',
            'producto_a_fabricar__unidad_medida',
            'bodega_origen_insumos',
            'bodega_destino_pt',
            'usuario_creacion',
            'bom'
        ).prefetch_related(
            'insumos_detalle__insumo',
            'insumos_detalle__insumo__unidad_medida'
        ),
        pk=pk
    )

    context = {
        'orden': orden,
        'detalles': orden.insumos_detalle.all(),
        'fecha_impresion': timezone.now()
    }

    html_string = render_to_string('produccion/hoja_viajero_pdf.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf_generado = html.write_pdf()

    response = HttpResponse(pdf_generado, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="OP_{orden.folio}.pdf"'
    return response
