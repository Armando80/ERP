# ERP Decorlata - produccion/models.py
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.models import User
from django.utils import timezone
from inventario.models import Producto, Bodega


class ListaMaterialesBOM(models.Model):
    """
    Lista de Materiales (Bill of Materials - BOM).
    Define la fórmula o receta para fabricar una cantidad base de un producto
    (Producto Terminado, Hoja Litografiada, Plantilla o Hoja Cortada).
    """
    producto_terminado = models.OneToOneField(
        Producto,
        on_delete=models.CASCADE,
        related_name='receta_bom',
        verbose_name="Producto a Fabricar"
    )
    cantidad_base = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('1.0000'),
        validators=[MinValueValidator(Decimal('0.0001'))],
        verbose_name="Cantidad Base de Fabricación",
        help_text="Ej: 1 para 1 pza, 1000 para un millar de botes"
    )
    descripcion_proceso = models.TextField(
        blank=True,
        null=True,
        verbose_name="Instrucciones y Ruta de Manufactura"
    )
    activo = models.BooleanField(
        default=True,
        verbose_name="Receta Activa"
    )
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lista de Materiales (BOM)"
        verbose_name_plural = "Listas de Materiales (BOM)"
        ordering = ['producto_terminado__nombre']

    def __str__(self):
        return f"BOM: {self.producto_terminado.sku} - {self.producto_terminado.nombre} (Base: {self.cantidad_base})"

    def calcular_costo_estimado_total_mxn(self, visitados=None):
        """Calcula el costo total estimado de los insumos para la cantidad base."""
        total = Decimal('0.000000')
        for insumo in self.insumos.select_related('materia_prima', 'materia_prima__moneda_base_costo'):
            total += insumo.calcular_costo_estimado_linea_mxn(visitados=visitados)
        return round(total, 6)

    @property
    def costo_estimado_total_mxn(self):
        """Calcula el costo total estimado de los insumos para la cantidad base."""
        return self.calcular_costo_estimado_total_mxn()

    def calcular_costo_estimado_unitario_mxn(self, visitados=None):
        """Calcula el costo unitario estimado (por pieza) de fabricar este producto."""
        if self.cantidad_base and self.cantidad_base > Decimal('0'):
            return round(self.calcular_costo_estimado_total_mxn(visitados=visitados) / self.cantidad_base, 6)
        return Decimal('0.000000')

    @property
    def costo_estimado_unitario_mxn(self):
        """Calcula el costo unitario estimado (por pieza) de fabricar este producto."""
        return self.calcular_costo_estimado_unitario_mxn()


class InsumoBOM(models.Model):
    """
    Componente o materia prima individual requerida dentro de la receta (BOM).
    """
    bom = models.ForeignKey(
        ListaMaterialesBOM,
        on_delete=models.CASCADE,
        related_name='insumos',
        verbose_name="Receta BOM"
    )
    materia_prima = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='usado_como_insumo',
        verbose_name="Materia Prima / Insumo"
    )
    cantidad_requerida = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name="Cantidad Requerida"
    )
    porcentaje_merma = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="% Merma / Scrap Esperado",
        help_text="Porcentaje adicional contemplado por mermas del proceso"
    )

    class Meta:
        verbose_name = "Insumo de Receta"
        verbose_name_plural = "Insumos de Receta"
        unique_together = ('bom', 'materia_prima')

    def __str__(self):
        return f"{self.cantidad_requerida} x {self.materia_prima.sku} para {self.bom.producto_terminado.sku}"

    @property
    def cantidad_con_merma(self):
        """Retorna la cantidad efectiva requerida considerando el factor de merma."""
        factor = Decimal('1.00') + (self.porcentaje_merma / Decimal('100.00'))
        return round(self.cantidad_requerida * factor, 6)

    def obtener_costo_info(self, visitados=None):
        """Retorna el desglose del costo unitario en MXN y su procedencia."""
        from produccion.services import resolver_costo_unitario_producto_mxn
        return resolver_costo_unitario_producto_mxn(self.materia_prima, visitados=visitados)

    @property
    def costo_info(self):
        """Retorna el desglose del costo unitario en MXN y su procedencia (cacheado por instancia)."""
        if not hasattr(self, '_costo_info_cache'):
            self._costo_info_cache = self.obtener_costo_info()
        return self._costo_info_cache

    @property
    def costo_unitario_efectivo_mxn(self):
        """Costo unitario resuelto en pesos mexicanos (MXN)."""
        return self.costo_info['costo_mxn']

    def calcular_costo_estimado_linea_mxn(self, visitados=None):
        """Calcula el costo proyectado en MXN considerando la jerarquía de costeo."""
        costo_info = self.obtener_costo_info(visitados=visitados)
        return round(self.cantidad_con_merma * costo_info['costo_mxn'], 6)

    @property
    def costo_estimado_linea_mxn(self):
        """Calcula el costo proyectado de este insumo con base en su costo unitario efectivo en MXN."""
        return self.calcular_costo_estimado_linea_mxn()


class OrdenProduccion(models.Model):
    """
    Orden de Producción (OP). Controla la fabricación de un lote de producto,
    el seguimiento de estados y la inyección final al Kardex de inventario.
    """
    PLANEADA = 'PLA'
    EN_PROCESO = 'PRO'
    TERMINADA = 'TER'
    CANCELADA = 'CAN'

    ESTADOS_PRODUCCION = [
        (PLANEADA, 'Planeada / En Espera'),
        (EN_PROCESO, 'En Proceso de Fabricación'),
        (TERMINADA, 'Terminada (Stock Afectado)'),
        (CANCELADA, 'Cancelada'),
    ]

    folio = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        verbose_name="No. Orden de Producción"
    )
    producto_a_fabricar = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='ordenes_fabricacion',
        verbose_name="Producto a Fabricar"
    )
    bom = models.ForeignKey(
        ListaMaterialesBOM,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ordenes_produccion',
        verbose_name="Fórmula / BOM Utilizada"
    )
    cantidad_a_producir = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        verbose_name="Cantidad Solicitada"
    )
    cantidad_producida = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        verbose_name="Cantidad Producida Real"
    )

    # Ubicaciones de Almacén
    bodega_origen_insumos = models.ForeignKey(
        Bodega,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_origen',
        verbose_name="Bodega de Insumos / Materia Prima",
        help_text="Almacén del cual se tomarán las materias primas y componentes"
    )
    bodega_destino_pt = models.ForeignKey(
        Bodega,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_destino',
        verbose_name="Bodega Destino (Producto Fabricado)",
        help_text="Almacén al cual ingresará el lote terminado"
    )

    # Vinculación Comercial con Pedidos de Venta
    pedido_venta = models.ForeignKey(
        'ventas.PedidoVenta_Maestro',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_produccion',
        verbose_name="Pedido de Venta Relacionado",
        help_text="Pedido comercial en firme que originó esta orden de producción"
    )
    pedido_detalle = models.ForeignKey(
        'ventas.PedidoVenta_Detalle',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_produccion',
        verbose_name="Partida de Pedido Específica"
    )

    # Tiempos
    fecha_inicio = models.DateTimeField(
        default=timezone.now,
        verbose_name="Fecha de Creación"
    )
    fecha_compromiso = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha Compromiso de Entrega"
    )
    fecha_finalizacion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de Finalización"
    )

    estado = models.CharField(
        max_length=3,
        choices=ESTADOS_PRODUCCION,
        default=PLANEADA,
        verbose_name="Estado de Producción"
    )

    # Costos Industriales Acumulados
    costo_total_insumos_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Total Insumos (MXN)"
    )
    costo_unitario_final_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Unitario Resultante (MXN)"
    )

    # Trazabilidad Industrial
    lote_fabricacion = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Lote de Fabricación"
    )
    usuario_creacion = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_creadas',
        verbose_name="Registrado por"
    )
    usuario_finalizacion = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ops_finalizadas',
        verbose_name="Finalizado por"
    )
    observaciones = models.TextField(
        blank=True,
        null=True,
        verbose_name="Observaciones del Proceso"
    )

    class Meta:
        verbose_name = "Orden de Producción"
        verbose_name_plural = "Órdenes de Producción"
        ordering = ['-fecha_inicio']

    def __str__(self):
        return f"OP {self.folio} - {self.producto_a_fabricar.sku} ({self.get_estado_display()})"

    @property
    def cantidad_pendiente(self):
        """Retorna la cantidad restante por producir."""
        return max(Decimal('0.0000'), self.cantidad_a_producir - self.cantidad_producida)

    @property
    def porcentaje_avance(self):
        """Calcula el porcentaje de avance respecto a la meta solicitada."""
        if self.cantidad_a_producir and self.cantidad_a_producir > Decimal('0'):
            return min(Decimal('100.0'), round((self.cantidad_producida / self.cantidad_a_producir) * Decimal('100.0'), 1))
        return Decimal('0.0')

    @property
    def entregas_pendientes_count(self):
        """Número de entregas enviadas que esperan autorización de almacén."""
        return self.entregas_parciales.filter(estado=EntregaParcialProduccion.PENDIENTE).count()

    @property
    def etapas_completadas_count(self):
        """Número de etapas de ensamble marcadas como completadas."""
        return self.etapas_ensamble.filter(estado='COMPLETADA').count()

    @property
    def etapas_total_count(self):
        """Número total de etapas configuradas en la línea de ensamble."""
        return self.etapas_ensamble.count()

    @property
    def porcentaje_avance_ensamble(self):
        """Calcula el avance ponderado en la línea de ensamble (0.0% a 100.0%)."""
        total = self.etapas_total_count
        if not total:
            return self.porcentaje_avance
        suma = sum((e.porcentaje_avance_etapa for e in self.etapas_ensamble.all()), Decimal('0.0'))
        return round(suma / Decimal(str(total)), 1)



class OrdenProduccion_Insumo(models.Model):
    """
    Desglose congelado de insumos requeridos y consumidos para una Orden de Producción específica.
    Garantiza inmutabilidad histórica frente a futuros cambios en la receta general (BOM).
    """
    orden = models.ForeignKey(
        OrdenProduccion,
        on_delete=models.CASCADE,
        related_name='insumos_detalle',
        verbose_name="Orden de Producción"
    )
    insumo = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='usos_en_ordenes',
        verbose_name="Insumo / Materia Prima"
    )
    cantidad_estimada = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        verbose_name="Cantidad Proyectada"
    )
    cantidad_consumida = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Cantidad Real Consumida"
    )
    costo_unitario_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Unitario Capturado (MXN)"
    )
    costo_total_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Total Línea (MXN)"
    )

    class Meta:
        verbose_name = "Insumo de Orden de Producción"
        verbose_name_plural = "Insumos de Orden de Producción"
        unique_together = ('orden', 'insumo')

    def __str__(self):
        return f"{self.insumo.sku} para {self.orden.folio}"


class EntregaParcialProduccion(models.Model):
    """
    Notificación parcial de producto terminado enviado al almacén.
    Permanece congelada hasta que el usuario de almacén la autoriza en el Kardex.
    """
    PENDIENTE = 'PENDIENTE'
    AUTORIZADA = 'AUTORIZADA'
    RECHAZADA = 'RECHAZADA'

    ESTADOS_ENTREGA = [
        (PENDIENTE, 'Pendiente de Autorización de Almacén'),
        (AUTORIZADA, 'Autorizada (Ingresada al Kardex)'),
        (RECHAZADA, 'Rechazada por Almacén'),
    ]

    orden = models.ForeignKey(
        OrdenProduccion,
        on_delete=models.CASCADE,
        related_name='entregas_parciales',
        verbose_name="Orden de Producción"
    )
    folio_entrega = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        verbose_name="Folio de Entrega"
    )
    cantidad_notificada = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        verbose_name="Cantidad Notificada (PT)"
    )
    lote_fabricacion = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Lote Fabricado"
    )
    fecha_notificacion = models.DateTimeField(
        default=timezone.now,
        verbose_name="Fecha de Notificación"
    )
    usuario_notifica = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='entregas_notificadas',
        verbose_name="Notificado por",
        null=True,
        blank=True
    )
    estado = models.CharField(
        max_length=15,
        choices=ESTADOS_ENTREGA,
        default=PENDIENTE,
        verbose_name="Estado de Autorización"
    )

    # Resolución de Almacén
    fecha_autorizacion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de Resolución"
    )
    usuario_autoriza = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='entregas_resueltas',
        verbose_name="Resuelto por (Almacén)",
        null=True,
        blank=True
    )
    notas_almacen = models.TextField(
        blank=True,
        null=True,
        verbose_name="Observaciones / Motivo de Almacén"
    )
    observaciones_produccion = models.TextField(
        blank=True,
        null=True,
        verbose_name="Notas del Turno / Producción"
    )

    # Costos Industriales Asignados a este Lote Parcial
    costo_total_insumos_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Total Insumos (MXN)"
    )
    costo_unitario_final_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Unitario Resultante (MXN)"
    )

    class Meta:
        verbose_name = "Entrega Parcial de Producción"
        verbose_name_plural = "Entregas Parciales de Producción"
        ordering = ['-fecha_notificacion']

    def __str__(self):
        return f"{self.folio_entrega} - {self.cantidad_notificada} {self.orden.producto_a_fabricar.sku} ({self.get_estado_display()})"


class EntregaParcial_Insumo(models.Model):
    """
    Desglose de materias primas e insumos proporcionales (o reales) consumidos
    específicamente para esta entrega parcial.
    """
    entrega = models.ForeignKey(
        EntregaParcialProduccion,
        on_delete=models.CASCADE,
        related_name='insumos_detalle',
        verbose_name="Entrega Parcial"
    )
    insumo = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name='consumos_en_entregas',
        verbose_name="Insumo / Materia Prima"
    )
    cantidad_estimada = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        verbose_name="Cantidad Estimada"
    )
    cantidad_consumida = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Cantidad Consumida"
    )
    costo_unitario_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Unitario Capturado (MXN)"
    )
    costo_total_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Costo Total Línea (MXN)"
    )

    class Meta:
        verbose_name = "Insumo de Entrega Parcial"
        verbose_name_plural = "Insumos de Entregas Parciales"
        unique_together = ('entrega', 'insumo')

    def __str__(self):
        return f"{self.insumo.sku}: {self.cantidad_consumida} para {self.entrega.folio_entrega}"


# ==============================================================================
# CONTROL DE AVANCE EN LÍNEAS DE ENSAMBLE Y ESTACIONES DE MANUFACTURA
# ==============================================================================

class EtapaProduccionOP(models.Model):
    """
    Control de Avance en Líneas de Ensamble y Estaciones de Fabricación.
    Rastrea el progreso secuencial de una orden de producción a través de las
    estaciones industriales clave de la fábrica de Decorlata.
    """
    CORTE_HOJA = 'CORTE_HOJA'
    LITOGRAFIA = 'LITOGRAFIA'
    CORTE_CUERPO = 'CORTE_CUERPO'
    SOLDADURA = 'SOLDADURA'
    ENSAMBLE = 'ENSAMBLE'
    PRUEBA_EMPAQUE = 'PRUEBA_EMPAQUE'

    ETAPAS_CHOICES = [
        (CORTE_HOJA, '1. Cizallado Primario de Hojalata'),
        (LITOGRAFIA, '2. Litografía y Barnizado'),
        (CORTE_CUERPO, '3. Corte de Plantillas (Cuerpos)'),
        (SOLDADURA, '4. Formado y Soldadura Eléctrica'),
        (ENSAMBLE, '5. Línea de Ensamble y Engargolado'),
        (PRUEBA_EMPAQUE, '6. Prueba de Hermeticidad y Paletizado'),
    ]

    ESTADO_PENDIENTE = 'PENDIENTE'
    ESTADO_EN_PROCESO = 'EN_PROCESO'
    ESTADO_COMPLETADA = 'COMPLETADA'

    ESTADOS_CHOICES = [
        (ESTADO_PENDIENTE, 'Pendiente'),
        (ESTADO_EN_PROCESO, 'En Proceso'),
        (ESTADO_COMPLETADA, 'Completada'),
    ]

    orden = models.ForeignKey(
        OrdenProduccion,
        on_delete=models.CASCADE,
        related_name='etapas_ensamble',
        verbose_name="Orden de Producción"
    )
    codigo_etapa = models.CharField(
        max_length=25,
        choices=ETAPAS_CHOICES,
        verbose_name="Estación / Etapa Industrial"
    )
    nombre_etapa = models.CharField(
        max_length=120,
        verbose_name="Nombre de la Estación"
    )
    secuencia = models.PositiveIntegerField(
        default=1,
        verbose_name="Secuencia en Línea"
    )
    estado = models.CharField(
        max_length=15,
        choices=ESTADOS_CHOICES,
        default=ESTADO_PENDIENTE,
        verbose_name="Estado de la Estación"
    )
    cantidad_entrada = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        verbose_name="Cantidad Recibida en Estación"
    )
    cantidad_buena = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        verbose_name="Piezas Conformes / Procesadas"
    )
    cantidad_scrap = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        verbose_name="Merma / Scrap (Piezas Defectuosas)"
    )
    operador = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        verbose_name="Operador / Líder de Línea"
    )
    fecha_inicio = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Inicio en Estación"
    )
    fecha_fin = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fin en Estación"
    )
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name="Observaciones de Calidad / Proceso"
    )

    class Meta:
        verbose_name = "Etapa de Línea de Ensamble"
        verbose_name_plural = "Etapas de Líneas de Ensamble"
        ordering = ['secuencia', 'id']
        unique_together = ('orden', 'codigo_etapa')

    def __str__(self):
        return f"{self.orden.folio} - {self.nombre_etapa} ({self.get_estado_display()})"

    @property
    def porcentaje_avance_etapa(self):
        """Calcula el porcentaje de piezas procesadas respecto al objetivo de la orden."""
        meta = self.orden.cantidad_a_producir
        if meta and meta > Decimal('0'):
            return min(Decimal('100.0'), round((self.cantidad_buena / meta) * Decimal('100.0'), 1))
        return Decimal('0.0')



