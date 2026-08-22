# ERP/compras/services.py (o puedes ponerlo en views.py)

from django.db import transaction
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone

# Importa tus modelos de las distintas aplicaciones
from .models import OrdenCompra_Maestro, OrdenCompra_Detalle, Proveedor
from inventario.models import MovimientoInventario, Producto, Bodega
from general.models import TipoCambio, Moneda
from decimal import Decimal

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