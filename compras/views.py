# ERP/compras/services.py (o puedes ponerlo en views.py)

from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.contrib import messages

# Importa tus modelos de las distintas aplicaciones
from .models import OrdenCompra_Maestro, OrdenCompra_Detalle, Proveedor
from inventario.models import MovimientoInventario, Producto, Bodega
from general.models import TipoCambio, Moneda
from decimal import Decimal
from .forms import ProveedorForm

def procesar_recepcion_compra(orden_id, usuario):
    """
    Cambia el estado de la OC a 'RECIBIDA', obtiene el tipo de cambio
    y genera los movimientos de Kardex para cada producto.
    """
    with transaction.atomic():
        orden = OrdenCompra_Maestro.objects.select_for_update().get(id=orden_id)

        if orden.estado == 'RECIBIDA':
            raise ValueError("Esta Orden de Compra ya fue procesada anteriormente.")

        # 1. Resolver el Tipo de Cambio (Asumiendo que Moneda tiene un código como 'MXN', 'USD')
        tipo_cambio_val = Decimal('1.000000')
        if orden.moneda.codigo != 'MXN':
            # Buscar el tipo de cambio del día
            tc_hoy = TipoCambio.objects.filter(
                moneda_origen=orden.moneda,
                fecha=timezone.now().date()
            ).first()

            if tc_hoy:
                tipo_cambio_val = tc_hoy.valor
            else:
                # Aquí podrías lanzar un error exigiendo que capturen el TC del día
                raise ValueError(f"Falta registrar el Tipo de Cambio para {orden.moneda.codigo} del día de hoy.")

        orden.tipo_cambio_aplicado = tipo_cambio_val

        # 2. Generar Movimientos de Inventario (Kardex) por cada partida
        for detalle in orden.detalles.all():
            # Convertir el precio unitario original a MXN para la contabilidad
            costo_mxn = detalle.precio_unitario * tipo_cambio_val

            MovimientoInventario.objects.create(
                producto=detalle.producto,
                bodega_destino=orden.bodega_destino,
                tipo_movimiento=MovimientoInventario.ENTRADA,
                cantidad=detalle.cantidad,
                costo_unitario_original=detalle.precio_unitario,
                moneda_original=orden.moneda,
                tipo_cambio_aplicado=tipo_cambio_val,
                costo_unitario_mxn_capturado=costo_mxn,
                referencia_operacion=f"OC-{orden.folio}",
                usuario=usuario
            )

            # Actualizamos trazabilidad en la línea
            detalle.cantidad_recibida = detalle.cantidad
            detalle.save()

        # 3. Marcar OC como recibida
        orden.estado = 'RECIBIDA'
        orden.save()

        return True

def generar_folio_compra():
    """Genera un folio secuencial por año, ej: OC-2026-0001"""
    year = timezone.now().year
    prefijo = f"OC-{year}-"

    # Buscar la última orden registrada en este año
    ultima_orden = OrdenCompra_Maestro.objects.filter(
        folio__startswith=prefijo
    ).order_by('-folio').first()

    if ultima_orden:
        # Extraer la última parte numérica y sumar 1
        secuencia = int(ultima_orden.folio.split('-')[-1]) + 1
    else:
        secuencia = 1

    return f"{prefijo}{secuencia:04d}"

@login_required
def crear_orden_compra_view(request):
    if request.method == 'POST':
        # 1. Capturar datos del encabezado
        proveedor_id = request.POST.get('proveedor')
        bodega_id = request.POST.get('bodega_destino')
        moneda_id = request.POST.get('moneda')

        # 2. Capturar las listas dinámicas de las partidas
        productos = request.POST.getlist('producto[]')
        cantidades = request.POST.getlist('cantidad[]')
        precios = request.POST.getlist('precio_unitario[]')

        try:
            with transaction.atomic():
                # A. Crear la Orden de Compra Maestra con el nuevo folio
                orden = OrdenCompra_Maestro.objects.create(
                    proveedor_id=proveedor_id,
                    bodega_destino_id=bodega_id,
                    moneda_id=moneda_id,
                    folio=generar_folio_compra() 
                )

                subtotal_global = 0

                # B. Iterar sobre las listas simultáneamente
                for prod_id, cant, precio in zip(productos, cantidades, precios):
                    cantidad_decimal = float(cant)
                    precio_decimal = float(precio)

                    OrdenCompra_Detalle.objects.create(
                        orden=orden,
                        producto_id=prod_id,
                        cantidad=cantidad_decimal,
                        precio_unitario=precio_decimal
                    )
                    subtotal_global += (cantidad_decimal * precio_decimal)

                # C. Actualizar los totales
                orden.subtotal = subtotal_global
                orden.impuestos = subtotal_global * 0.16
                orden.total = orden.subtotal + orden.impuestos
                orden.save()

            # Retornar mensaje de éxito (HTMX lo inyectará en la pantalla)
            return HttpResponse(f"""
                <div class="alert alert-success border-0 shadow-sm p-4 text-center">
                    <i class="bi bi-check-circle-fill fs-1 text-success d-block mb-3"></i>
                    <h4 class="fw-bold">¡Orden de Compra Generada!</h4>
                    <p class="mb-0">El pedido <strong>{orden.folio}</strong> ha sido guardado correctamente.</p>
                    <a href="#" class="btn btn-primary mt-3" onClick="window.location.reload();">Crear Nueva Orden</a>
                </div>
            """)

        except Exception as e:
            return HttpResponse(f'<div class="alert alert-danger"><i class="bi bi-exclamation-triangle-fill me-2"></i> Error al procesar: {str(e)}</div>')

    # SI LA PETICIÓN ES GET: Preparamos los catálogos para el formulario
    context = {
        'proveedores': Proveedor.objects.filter(activo=True).order_by('razon_social'),
        'bodegas': Bodega.objects.all().order_by('nombre'),
        'monedas': Moneda.objects.all().order_by('codigo'),
        # Filtramos para no incluir Productos Terminados (PT)
        'productos': Producto.objects.exclude(tipo='PT').order_by('nombre')
    }

    return render(request, 'compras/orden_compra_form.html', context)

@login_required
def historial_ordenes_view(request):
    """
    Muestra el listado de todas las órdenes de compra generadas.
    """
    ordenes = OrdenCompra_Maestro.objects.select_related('proveedor', 'moneda').all().order_by('-fecha_emision')

    return render(request, 'compras/historial_ordenes.html', {'ordenes': ordenes})

@login_required
def recibir_orden_view(request, pk):
    """Procesa la entrada al almacén y la afectación contable."""
    orden = get_object_or_404(OrdenCompra_Maestro, pk=pk)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                if orden.estado == 'RECIBIDA':
                    raise ValueError("Esta orden ya fue ingresada al almacén anteriormente.")

                # 1. Resolver el Tipo de Cambio
                tipo_cambio_val = Decimal('1.000000')
                if orden.moneda.codigo != 'MXN':
                    tc_hoy = TipoCambio.objects.filter(
                        moneda_origen=orden.moneda,
                        fecha=timezone.now().date()
                    ).first()

                    if tc_hoy:
                        tipo_cambio_val = tc_hoy.valor
                    else:
                        raise ValueError(f"No hay un Tipo de Cambio registrado hoy para {orden.moneda.codigo}.")

                orden.tipo_cambio_aplicado = tipo_cambio_val

                # 2. Generar Movimientos de Inventario (Kardex)
                for detalle in orden.detalles.all():
                    costo_mxn = detalle.precio_unitario * tipo_cambio_val

                    # Al crear este registro, la señal en inventario/signals.py
                    # hará la suma de stock y recalculará el costo promedio ponderado.
                    MovimientoInventario.objects.create(
                        producto=detalle.producto,
                        bodega_destino=orden.bodega_destino,
                        tipo_movimiento=MovimientoInventario.ENTRADA,
                        cantidad=detalle.cantidad,
                        costo_unitario_original=detalle.precio_unitario,
                        moneda_original=orden.moneda,
                        tipo_cambio_aplicado=tipo_cambio_val,
                        costo_unitario_mxn_capturado=costo_mxn,
                        referencia_operacion=f"{orden.folio}",
                        usuario=request.user
                    )

                    detalle.cantidad_recibida = detalle.cantidad
                    detalle.save()

                # 3. Marcar OC como recibida
                orden.estado = 'RECIBIDA'
                orden.save()

                messages.success(request, f"¡Éxito! La mercancía de la {orden.folio} ya está en el almacén y el inventario fue valorizado.")

        except Exception as e:
            messages.error(request, f"Error al procesar: {str(e)}")

    return redirect('compras:historial_ordenes')

@login_required
def proveedores_catalogo_view(request):
    """Muestra el catálogo de proveedores con búsqueda en tiempo real."""
    query = request.GET.get('q', '').strip()
    proveedores = Proveedor.objects.all().order_by('razon_social')

    if query:
        proveedores = proveedores.filter(
            Q(razon_social__icontains=query) |
            Q(rfc__icontains=query) |
            Q(nombre_comercial__icontains=query)
        )

    if request.headers.get('HX-Request'):
        return render(request, 'compras/partials/_tabla_proveedores.html', {'proveedores': proveedores})

    return render(request, 'compras/proveedores_catalogo.html', {'proveedores': proveedores, 'query': query})

@login_required
def guardar_proveedor_view(request, pk=None):
    """Maneja la creación y edición de proveedores en el Offcanvas."""
    proveedor = get_object_or_404(Proveedor, pk=pk) if pk else None

    if request.method == 'POST':
        form = ProveedorForm(request.POST, instance=proveedor)
        if form.is_valid():
            form.save()
            # Disparamos el evento para que HTMX recargue la tabla y cierre el panel
            response = HttpResponse()
            response['HX-Trigger'] = 'proveedorGuardado'
            return response
    else:
        form = ProveedorForm(instance=proveedor)

    return render(request, 'compras/partials/_proveedor_form.html', {'form': form, 'proveedor': proveedor})