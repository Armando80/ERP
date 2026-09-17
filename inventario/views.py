# ERP/inventario/views.py

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from .models import Producto, MovimientoInventario, Stock, Bodega, SolicitudAnulacionMovimiento
from .forms import ProductoForm, MovimientoForm, SolicitarAnulacionForm
from .services import (
    es_usuario_administrador,
    solicitar_anulacion_movimiento,
    autorizar_anulacion_movimiento,
    rechazar_anulacion_movimiento
)
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
    # Consulta base optimizada con relaciones para trazabilidad y estado de anulación
    movimientos = MovimientoInventario.objects.select_related(
        'producto', 'bodega_origen', 'bodega_destino', 'usuario', 'movimiento_relacionado'
    ).prefetch_related('solicitudes_anulacion').all().order_by('-fecha')

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

    es_admin = es_usuario_administrador(request.user)

    # 3. Retornar solo la tabla si es petición HTMX
    if request.headers.get('HX-Request'):
        return render(request, 'inventario/partials/_tabla_movimientos.html', {
            'movimientos': movimientos,
            'es_admin': es_admin
        })

    # 4. Retornar página completa si es carga normal
    bodegas = Bodega.objects.all().order_by('nombre')
    total_pendientes = EntregaParcialProduccion.objects.filter(estado=EntregaParcialProduccion.PENDIENTE).count()
    total_solicitudes_anulacion = SolicitudAnulacionMovimiento.objects.filter(
        estado=SolicitudAnulacionMovimiento.PENDIENTE
    ).count()

    return render(request, 'inventario/movimientos.html', {
        'movimientos': movimientos,
        'bodegas': bodegas,
        'query': query,
        'total_pendientes': total_pendientes,
        'total_solicitudes_anulacion': total_solicitudes_anulacion,
        'es_admin': es_admin
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


# ==============================================================================
# ANULACIÓN DE MOVIMIENTOS EN KARDEX (AUTORIZACIÓN ADMINISTRATIVA)
# ==============================================================================

@login_required
def solicitar_anulacion_modal_view(request, pk):
    """
    Renderiza el modal con el formulario para solicitar la anulación de un movimiento del Kardex.
    """
    movimiento = get_object_or_404(
        MovimientoInventario.objects.select_related(
            'producto', 'producto__unidad_medida', 'bodega_origen', 'bodega_destino', 'usuario'
        ),
        pk=pk
    )

    form = SolicitarAnulacionForm()

    return render(request, 'inventario/partials/_modal_solicitar_anulacion.html', {
        'movimiento': movimiento,
        'form': form
    })


@login_required
def enviar_solicitud_anulacion_view(request, pk):
    """
    Procesa la solicitud de anulación de un movimiento del Kardex.
    """
    if request.method == 'POST':
        form = SolicitarAnulacionForm(request.POST)
        if form.is_valid():
            try:
                motivo = form.cleaned_data['motivo_solicitud']
                solicitud = solicitar_anulacion_movimiento(
                    movimiento_id=pk,
                    usuario_solicita=request.user,
                    motivo=motivo
                )
                messages.success(
                    request,
                    f"¡Solicitud {solicitud.folio} registrada exitosamente! "
                    "El movimiento queda en espera de revisión y autorización por un Administrador."
                )
                response = HttpResponse()
                response['HX-Trigger'] = 'anulacionSolicitada'
                return response
            except Exception as e:
                return HttpResponse(f"<div class='alert alert-danger py-2 small mb-0'>{str(e)}</div>", status=400)
        else:
            return HttpResponse("<div class='alert alert-danger py-2 small mb-0'>Por favor proporciona un motivo válido (mínimo 5 caracteres).</div>", status=400)

    return HttpResponse(status=405)


@login_required
def solicitudes_anulacion_view(request):
    """
    Bandeja de solicitudes de anulación pendientes para la pestaña de Administrador.
    Calcula el diagnóstico preventivo de existencias para cada solicitud.
    """
    solicitudes = SolicitudAnulacionMovimiento.objects.filter(
        estado=SolicitudAnulacionMovimiento.PENDIENTE
    ).select_related(
        'movimiento',
        'movimiento__producto',
        'movimiento__producto__unidad_medida',
        'movimiento__bodega_origen',
        'movimiento__bodega_destino',
        'usuario_solicita'
    ).order_by('-fecha_solicitud')

    solicitudes_data = []
    for s in solicitudes:
        mov = s.movimiento
        suficiente = True
        disponible = None
        bodega_nombre = ""

        if mov.tipo_movimiento == MovimientoInventario.ENTRADA:
            st = Stock.objects.filter(producto=mov.producto, bodega=mov.bodega_destino).first()
            disponible = st.cantidad_disponible if st else Decimal('0.000000')
            suficiente = disponible >= mov.cantidad
            bodega_nombre = mov.bodega_destino.nombre if mov.bodega_destino else "-"

        elif mov.tipo_movimiento == MovimientoInventario.TRANSFERENCIA:
            st = Stock.objects.filter(producto=mov.producto, bodega=mov.bodega_destino).first()
            disponible = st.cantidad_disponible if st else Decimal('0.000000')
            suficiente = disponible >= mov.cantidad
            bodega_nombre = mov.bodega_destino.nombre if mov.bodega_destino else "-"

        elif mov.tipo_movimiento == MovimientoInventario.SALIDA:
            bodega_nombre = mov.bodega_origen.nombre if mov.bodega_origen else "-"

        elif mov.tipo_movimiento == MovimientoInventario.AJUSTE:
            if mov.bodega_destino:
                st = Stock.objects.filter(producto=mov.producto, bodega=mov.bodega_destino).first()
                disponible = st.cantidad_disponible if st else Decimal('0.000000')
                suficiente = disponible >= mov.cantidad
                bodega_nombre = mov.bodega_destino.nombre
            else:
                bodega_nombre = mov.bodega_origen.nombre if mov.bodega_origen else "-"

        solicitudes_data.append({
            'solicitud': s,
            'suficiente': suficiente,
            'disponible': disponible,
            'bodega_nombre': bodega_nombre
        })

    es_admin = es_usuario_administrador(request.user)

    return render(request, 'inventario/partials/_tabla_solicitudes_anulacion.html', {
        'solicitudes_data': solicitudes_data,
        'total_pendientes': len(solicitudes_data),
        'es_admin': es_admin
    })


@login_required
def autorizar_anulacion_view(request, pk):
    """
    Autoriza formalmente la anulación de un movimiento.
    Exclusivo para usuarios con perfil de Administrador.
    Genera el contra-asiento en el Kardex y actualiza existencias de forma atómica.
    """
    if not es_usuario_administrador(request.user):
        return HttpResponseForbidden(
            "<div class='alert alert-danger py-2 small mb-0'>Acceso denegado: solo usuarios nivel Administrador pueden autorizar anulaciones en el Kardex.</div>"
        )

    if request.method == 'POST':
        try:
            notas = request.POST.get('notas_administrador', '').strip()
            solicitud, contraasiento = autorizar_anulacion_movimiento(
                solicitud_id=pk,
                usuario_admin=request.user,
                notas_admin=notas
            )
            messages.success(
                request,
                f"¡Anulación {solicitud.folio} APROBADA! Se generó el contra-asiento de reversa #{contraasiento.id} en el Kardex y se regularizó el stock."
            )
            response = HttpResponse()
            response['HX-Trigger'] = 'anulacionProcesada'
            return response
        except ValidationError as ve:
            msg = ve.messages[0] if hasattr(ve, 'messages') else str(ve)
            return HttpResponse(f"<div class='alert alert-danger py-2 small mb-0'><i class='bi bi-exclamation-octagon me-1'></i>{msg}</div>", status=400)
        except Exception as e:
            return HttpResponse(f"<div class='alert alert-danger py-2 small mb-0'><i class='bi bi-exclamation-triangle me-1'></i>{str(e)}</div>", status=400)

    return HttpResponse(status=405)


@login_required
def rechazar_anulacion_view(request, pk):
    """
    Rechaza formalmente la anulación de un movimiento.
    Exclusivo para usuarios con perfil de Administrador.
    El movimiento permanece activo sin alterar existencias.
    """
    if not es_usuario_administrador(request.user):
        return HttpResponseForbidden(
            "<div class='alert alert-danger py-2 small mb-0'>Acceso denegado: solo Administradores pueden rechazar solicitudes.</div>"
        )

    if request.method == 'POST':
        try:
            motivo = request.headers.get('HX-Prompt') or request.POST.get('motivo_rechazo') or 'Rechazada por administración'
            solicitud = rechazar_anulacion_movimiento(
                solicitud_id=pk,
                usuario_admin=request.user,
                motivo_rechazo=motivo.strip()
            )
            messages.info(
                request,
                f"La solicitud {solicitud.folio} ha sido RECHAZADA. El movimiento #{solicitud.movimiento_id} permanece activo."
            )
            response = HttpResponse()
            response['HX-Trigger'] = 'anulacionProcesada'
            return response
        except Exception as e:
            return HttpResponse(f"<div class='alert alert-danger py-2 small mb-0'>{str(e)}</div>", status=400)

    return HttpResponse(status=405)
