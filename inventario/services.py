# ERP Decorlata - inventario/services.py
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from .models import MovimientoInventario, Stock, Producto, SolicitudAnulacionMovimiento


def es_usuario_administrador(user):
    """
    Verifica si el usuario tiene privilegios de nivel Administrador:
    - Superusuario
    - Staff / Administrador de Django
    - Perteneciente al grupo 'Administrador' o 'Administradores'
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name__in=['Administrador', 'Administradores', 'Admin']).exists()


def generar_folio_anulacion():
    """Genera un folio secuencial anual para la anulación, ej: ANUL-2026-0001"""
    year = timezone.now().year
    prefijo = f"ANUL-{year}-"

    ultima_solicitud = SolicitudAnulacionMovimiento.objects.filter(
        folio__startswith=prefijo
    ).order_by('-folio').first()

    if ultima_solicitud:
        try:
            secuencia = int(ultima_solicitud.folio.split('-')[-1]) + 1
        except (ValueError, IndexError):
            secuencia = 1
    else:
        secuencia = 1

    return f"{prefijo}{secuencia:04d}"


def solicitar_anulacion_movimiento(movimiento_id, usuario_solicita, motivo):
    """
    Registra una solicitud formal de anulación de un movimiento en el Kardex.
    Deja la solicitud en estado PENDIENTE a la espera de autorización administrativa.
    El movimiento original NO se anula aún y el stock se mantiene intacto.
    """
    with transaction.atomic():
        mov = MovimientoInventario.objects.select_for_update().get(id=movimiento_id)

        if mov.es_anulado:
            raise ValueError(f"El movimiento #{mov.id} ya ha sido anulado previamente.")

        if mov.es_contraasiento:
            raise ValueError("No es posible solicitar la anulación de un movimiento que es contra-asiento de reversa.")

        if mov.tiene_solicitud_pendiente:
            sol_activa = mov.solicitud_pendiente
            raise ValueError(
                f"El movimiento #{mov.id} ya cuenta con una solicitud de anulación pendiente ({sol_activa.folio})."
            )

        motivo_limpio = (motivo or "").strip()
        if len(motivo_limpio) < 5:
            raise ValueError("El motivo de anulación es obligatorio y debe contener al menos 5 caracteres descriptivos.")

        folio = generar_folio_anulacion()

        solicitud = SolicitudAnulacionMovimiento.objects.create(
            folio=folio,
            movimiento=mov,
            estado=SolicitudAnulacionMovimiento.PENDIENTE,
            motivo_solicitud=motivo_limpio,
            usuario_solicita=usuario_solicita
        )

        return solicitud


def autorizar_anulacion_movimiento(solicitud_id, usuario_admin, notas_admin=None):
    """
    Autorización formal por parte de un Administrador:
    1. Valida permisos administrativos de usuario_admin.
    2. Valida que las existencias actuales absorban la reversa (evitando stock negativo).
    3. Genera un contra-asiento compensatorio en el Kardex (MovimientoInventario) con es_contraasiento=True.
    4. La señal de inventario actualiza automáticamente existencias y recalcula costos.
    5. Marca el movimiento original con es_anulado=True.
    6. Marca la solicitud como APROBADA y registra al usuario autorizador.
    """
    if not es_usuario_administrador(usuario_admin):
        raise PermissionDenied("Solo usuarios con perfil de Administrador tienen autorización para anular movimientos en el Kardex.")

    with transaction.atomic():
        solicitud = SolicitudAnulacionMovimiento.objects.select_for_update().get(id=solicitud_id)

        if solicitud.estado != SolicitudAnulacionMovimiento.PENDIENTE:
            raise ValueError(f"Esta solicitud ya fue procesada anteriormente con estado: {solicitud.get_estado_display()}")

        mov = MovimientoInventario.objects.select_for_update().get(id=solicitud.movimiento_id)

        if mov.es_anulado:
            raise ValueError(f"El movimiento #{mov.id} ya figura como anulado en el sistema.")

        producto = Producto.objects.select_for_update().get(id=mov.producto_id)

        # -------------------------------------------------------------
        # VALIDACIONES PREVENTIVAS DE STOCK FÍSICO
        # -------------------------------------------------------------
        if mov.tipo_movimiento == MovimientoInventario.ENTRADA:
            # Revertir una ENTRADA implica descontar de la bodega de destino
            bodega_a_descontar = mov.bodega_destino
            stock_obj = Stock.objects.select_for_update().filter(
                producto=producto,
                bodega=bodega_a_descontar
            ).first()
            disponible = stock_obj.cantidad_disponible if stock_obj else Decimal('0.000000')

            if mov.cantidad > disponible:
                raise ValidationError(
                    f"No es posible anular la entrada #{mov.id}: el stock actual en {bodega_a_descontar.nombre} "
                    f"es de {disponible} {producto.unidad_medida.codigo}, insuficiente para revertir {mov.cantidad}."
                )

        elif mov.tipo_movimiento == MovimientoInventario.TRANSFERENCIA:
            # Revertir una TRANSFERENCIA implica retirar de la bodega destino y devolver al origen
            bodega_a_descontar = mov.bodega_destino
            stock_obj = Stock.objects.select_for_update().filter(
                producto=producto,
                bodega=bodega_a_descontar
            ).first()
            disponible = stock_obj.cantidad_disponible if stock_obj else Decimal('0.000000')

            if mov.cantidad > disponible:
                raise ValidationError(
                    f"No es posible anular la transferencia #{mov.id}: el stock actual en la bodega receptora "
                    f"({bodega_a_descontar.nombre}) es de {disponible} {producto.unidad_medida.codigo}, insuficiente para devolver {mov.cantidad}."
                )

        # -------------------------------------------------------------
        # DETERMINAR CONFIGURACIÓN DEL CONTRA-ASIENTO
        # -------------------------------------------------------------
        bodega_origen_contra = None
        bodega_destino_contra = None
        tipo_contra = None

        if mov.tipo_movimiento == MovimientoInventario.ENTRADA:
            # Entrada se reversa con SALIDA desde la bodega receptora
            tipo_contra = MovimientoInventario.SALIDA
            bodega_origen_contra = mov.bodega_destino
            bodega_destino_contra = None

        elif mov.tipo_movimiento == MovimientoInventario.SALIDA:
            # Salida se reversa con ENTRADA hacia la bodega despachadora
            tipo_contra = MovimientoInventario.ENTRADA
            bodega_origen_contra = None
            bodega_destino_contra = mov.bodega_origen

        elif mov.tipo_movimiento == MovimientoInventario.TRANSFERENCIA:
            # Transferencia se reversa con TRANSFERENCIA INVERSA (destino -> origen)
            tipo_contra = MovimientoInventario.TRANSFERENCIA
            bodega_origen_contra = mov.bodega_destino
            bodega_destino_contra = mov.bodega_origen

        elif mov.tipo_movimiento == MovimientoInventario.AJUSTE:
            if mov.bodega_destino:
                tipo_contra = MovimientoInventario.SALIDA
                bodega_origen_contra = mov.bodega_destino
                bodega_destino_contra = None
            else:
                tipo_contra = MovimientoInventario.ENTRADA
                bodega_origen_contra = None
                bodega_destino_contra = mov.bodega_origen

        # -------------------------------------------------------------
        # GENERAR CONTRA-ASIENTO EN KARDEX
        # -------------------------------------------------------------
        contraasiento = MovimientoInventario.objects.create(
            producto=mov.producto,
            bodega_origen=bodega_origen_contra,
            bodega_destino=bodega_destino_contra,
            tipo_movimiento=tipo_contra,
            cantidad=mov.cantidad,
            lote=mov.lote,
            fecha_caducidad=mov.fecha_caducidad,
            moneda_original=mov.moneda_original,
            costo_unitario_original=mov.costo_unitario_original,
            tipo_cambio_aplicado=mov.tipo_cambio_aplicado,
            costo_unitario_mxn_capturado=mov.costo_unitario_mxn_capturado,
            referencia_operacion=f"ANUL-{solicitud.folio}",
            usuario=usuario_admin,
            observaciones=(
                f"Contra-asiento por anulación autorizada ({solicitud.folio}). "
                f"Movimiento revertido: #{mov.id}. Motivo: {solicitud.motivo_solicitud}"
            ),
            es_contraasiento=True,
            movimiento_relacionado=mov
        )

        # -------------------------------------------------------------
        # ACTUALIZAR ESTADO DEL MOVIMIENTO ORIGINAL
        # -------------------------------------------------------------
        mov.es_anulado = True
        mov.movimiento_relacionado = contraasiento
        mov.save()

        # -------------------------------------------------------------
        # ACTUALIZAR SOLICITUD
        # -------------------------------------------------------------
        solicitud.estado = SolicitudAnulacionMovimiento.APROBADA
        solicitud.usuario_autoriza = usuario_admin
        solicitud.fecha_resolucion = timezone.now()
        solicitud.notas_administrador = notas_admin
        solicitud.movimiento_contraasiento = contraasiento
        solicitud.save()

        # -------------------------------------------------------------
        # IMPACTO EN ÓRDENES DE PRODUCCIÓN (SI PROCEDE)
        # -------------------------------------------------------------
        if mov.tipo_movimiento == MovimientoInventario.ENTRADA and mov.referencia_operacion:
            try:
                from produccion.models import EntregaParcialProduccion, OrdenProduccion
                entrega = EntregaParcialProduccion.objects.filter(
                    folio_entrega=mov.referencia_operacion,
                    estado=EntregaParcialProduccion.AUTORIZADA
                ).first()

                if entrega:
                    orden = entrega.orden
                    orden.cantidad_producida = max(
                        Decimal('0.0000'),
                        orden.cantidad_producida - entrega.cantidad_notificada
                    )
                    if orden.estado == OrdenProduccion.TERMINADA:
                        orden.estado = OrdenProduccion.EN_PROCESO
                        orden.fecha_finalizacion = None
                        orden.usuario_finalizacion = None
                    orden.save()
            except Exception:
                pass

        return solicitud, contraasiento


def rechazar_anulacion_movimiento(solicitud_id, usuario_admin, motivo_rechazo):
    """
    Rechaza formalmente una solicitud de anulación por parte de un Administrador.
    El movimiento original permanece activo y el Kardex no sufre alteración alguna.
    """
    if not es_usuario_administrador(usuario_admin):
        raise PermissionDenied("Solo usuarios con perfil de Administrador pueden resolver solicitudes de anulación.")

    with transaction.atomic():
        solicitud = SolicitudAnulacionMovimiento.objects.select_for_update().get(id=solicitud_id)

        if solicitud.estado != SolicitudAnulacionMovimiento.PENDIENTE:
            raise ValueError(f"Esta solicitud ya fue procesada anteriormente con estado: {solicitud.get_estado_display()}")

        motivo_limpio = (motivo_rechazo or "").strip()
        if not motivo_limpio:
            motivo_limpio = "Rechazada por decisión administrativa"

        solicitud.estado = SolicitudAnulacionMovimiento.RECHAZADA
        solicitud.usuario_autoriza = usuario_admin
        solicitud.fecha_resolucion = timezone.now()
        solicitud.notas_administrador = motivo_limpio
        solicitud.save()

        return solicitud
