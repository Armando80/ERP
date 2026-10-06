import uuid
from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings

from general.models import Moneda
from inventario.models import Producto


class Cliente(models.Model):
    """
    Directorio de Clientes de ERP Decorlata.
    Cumple con especificaciones fiscales estrictas del SAT (CFDI 4.0) y requerimientos comerciales.
    """
    REGIMEN_CHOICES = [
        ('601', '601 - General de Ley Personas Morales'),
        ('603', '603 - Personas Morales con Fines no Lucrativos'),
        ('605', '605 - Sueldos y Salarios e Ingresos Asimilados a Salarios'),
        ('606', '606 - Arrendamiento'),
        ('612', '612 - Personas Físicas con Actividades Empresariales y Profesionales'),
        ('620', '620 - Sociedades Cooperativas de Producción que optan por diferir sus ingresos'),
        ('621', '621 - Incorporación Fiscal'),
        ('625', '625 - Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas'),
        ('626', '626 - Régimen Simplificado de Confianza (RESICO)'),
        ('616', '616 - Sin obligaciones fiscales'),
    ]

    USO_CFDI_CHOICES = [
        ('G01', 'G01 - Adquisición de mercancías'),
        ('G02', 'G02 - Devoluciones, descuentos o bonificaciones'),
        ('G03', 'G03 - Gastos en general'),
        ('I01', 'I01 - Construcciones'),
        ('I02', 'I02 - Mobiliario y equipo de oficina'),
        ('I03', 'I03 - Equipo de transporte'),
        ('I04', 'I04 - Equipo de cómputo y accesorios'),
        ('I08', 'I08 - Otra maquinaria y equipo'),
        ('S01', 'S01 - Sin efectos fiscales'),
        ('CP01', 'CP01 - Pagos'),
    ]

    # Identificación Fiscal Obligatoria CFDI 4.0
    rfc = models.CharField(
        max_length=13,
        unique=True,
        verbose_name="RFC del Cliente",
        help_text="12 caracteres para Personas Morales o 13 para Personas Físicas"
    )
    razon_social = models.CharField(
        max_length=200,
        verbose_name="Nombre o Razón Social (Exacto SAT)",
        help_text="Tal como aparece en la Constancia de Situación Fiscal (en mayúsculas)"
    )
    nombre_comercial = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name="Nombre Comercial / Identificador",
        help_text="Nombre de uso cotidiano o marca conocida"
    )
    regimen_fiscal = models.CharField(
        max_length=3,
        choices=REGIMEN_CHOICES,
        default='601',
        verbose_name="Régimen Fiscal (SAT)"
    )
    codigo_postal = models.CharField(
        max_length=5,
        verbose_name="Código Postal Fiscal",
        help_text="Código Postal del domicilio fiscal registrado en el SAT"
    )
    uso_cfdi = models.CharField(
        max_length=4,
        choices=USO_CFDI_CHOICES,
        default='G03',
        verbose_name="Uso de CFDI Habitual"
    )

    # Contacto y Operación Comercial
    correo = models.EmailField(
        verbose_name="Correo para Facturación / Envío",
        help_text="Destinatario principal de facturas XML/PDF y cotizaciones"
    )
    telefono = models.CharField(
        max_length=25,
        blank=True,
        null=True,
        verbose_name="Teléfono de Contacto"
    )
    direccion = models.TextField(
        blank=True,
        null=True,
        verbose_name="Dirección / Domicilio de Entrega"
    )

    # Condiciones de Crédito y Cobranza
    dias_credito = models.PositiveIntegerField(
        default=0,
        verbose_name="Días de Crédito",
        help_text="0 para operaciones de contado"
    )
    limite_credito = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Límite de Crédito (MXN)"
    )
    activo = models.BooleanField(
        default=True,
        verbose_name="Cliente Activo",
        help_text="Desmarcar para deshabilitar ventas a este cliente sin borrar historial"
    )
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name="Notas u Observaciones"
    )
    fecha_registro = models.DateTimeField(auto_now_add=True, null=True, blank=True, verbose_name="Fecha de Registro")

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['razon_social']

    def __str__(self):
        alias = f" ({self.nombre_comercial})" if self.nombre_comercial else ""
        return f"{self.rfc} - {self.razon_social}{alias}"

    def clean(self):
        super().clean()
        if self.rfc:
            self.rfc = self.rfc.upper().strip().replace(' ', '')
            if len(self.rfc) not in [12, 13]:
                raise ValidationError({'rfc': 'El RFC debe tener exactamente 12 caracteres (Moral) o 13 (Física).'})
        if self.razon_social:
            self.razon_social = self.razon_social.upper().strip()
        if self.codigo_postal:
            self.codigo_postal = self.codigo_postal.strip()
            if not self.codigo_postal.isdigit() or len(self.codigo_postal) != 5:
                raise ValidationError({'codigo_postal': 'El Código Postal debe ser un número de exactamente 5 dígitos.'})

from django.core.validators import MinValueValidator
from inventario.models import Bodega

class PedidoVenta_Maestro(models.Model):
    """
    Maestro de Cotizaciones y Pedidos de Venta.
    Controla el ciclo comercial completo, valuación multidivisa y preparación fiscal.
    """
    COTIZACION = 'COT'
    PEDIDO = 'PED'
    TIPO_DOC_CHOICES = [
        (COTIZACION, 'Cotización'),
        (PEDIDO, 'Pedido de Venta'),
    ]

    ESTADO_BORRADOR = 'BORRADOR'
    ESTADO_COTIZADO = 'COTIZADO'
    ESTADO_CONFIRMADO = 'CONFIRMADO'
    ESTADO_SURTIDO = 'SURTIDO'
    ESTADO_FACTURADO = 'FACTURADO'
    ESTADO_CANCELADO = 'CANCELADO'

    BORRADOR = ESTADO_BORRADOR
    COTIZADO = ESTADO_COTIZADO
    CONFIRMADO = ESTADO_CONFIRMADO
    SURTIDO = ESTADO_SURTIDO
    FACTURADO = ESTADO_FACTURADO
    CANCELADO = ESTADO_CANCELADO

    ESTADO_CHOICES = [
        (ESTADO_BORRADOR, 'Borrador'),
        (ESTADO_COTIZADO, 'Cotización Emitida'),
        (ESTADO_CONFIRMADO, 'Confirmado (Pedido en Firme)'),
        (ESTADO_SURTIDO, 'Surtido / Remisionado'),
        (ESTADO_FACTURADO, 'Facturado (CFDI Emitido)'),
        (ESTADO_CANCELADO, 'Cancelado'),
    ]

    METODO_PAGO_CHOICES = [
        ('PUE', 'PUE - Pago en una sola exhibición'),
        ('PPD', 'PPD - Pago en parcialidades o diferido'),
    ]

    FORMA_PAGO_CHOICES = [
        ('01', '01 - Efectivo'),
        ('02', '02 - Cheque nominativo'),
        ('03', '03 - Transferencia electrónica de fondos'),
        ('99', '99 - Por definir'),
    ]

    folio = models.CharField(max_length=20, unique=True, editable=False, verbose_name="Folio Comercial")
    tipo_documento = models.CharField(max_length=3, choices=TIPO_DOC_CHOICES, default=PEDIDO, verbose_name="Tipo de Documento")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='pedidos', verbose_name="Cliente")
    bodega_despacho = models.ForeignKey(Bodega, on_delete=models.PROTECT, related_name='pedidos_venta', verbose_name="Almacén de Despacho")

    fecha_emision = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Emisión")
    fecha_compromiso = models.DateField(blank=True, null=True, verbose_name="Fecha Compromiso de Entrega")

    # Arquitectura Multidivisa
    moneda = models.ForeignKey(Moneda, on_delete=models.PROTECT, verbose_name="Moneda de Venta")
    tipo_cambio_aplicado = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1.000000'),
        verbose_name="Tipo de Cambio Oficial Aplicado",
        help_text="Tipo de cambio oficial vigente en MXN"
    )

    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default=ESTADO_BORRADOR, verbose_name="Estado Operativo")
    condiciones_pago = models.CharField(max_length=100, blank=True, null=True, verbose_name="Condiciones de Pago")
    referencia_cliente = models.CharField(max_length=100, blank=True, null=True, verbose_name="Orden de Compra del Cliente / Ref")
    observaciones = models.TextField(blank=True, null=True, verbose_name="Observaciones / Instrucciones de Entrega")

    # Impuestos y Totales (en la moneda pactada)
    tasa_iva = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('16.00'), verbose_name="Tasa de IVA (%)")
    subtotal = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'), verbose_name="Subtotal")
    impuestos = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'), verbose_name="IVA (16%)")
    total = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'), verbose_name="Total")
    total_mxn = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0.0000'), verbose_name="Total Valuado en MXN")

    # Preparación Fiscal SAT (CFDI 4.0)
    uso_cfdi = models.CharField(max_length=4, default='G03', verbose_name="Uso de CFDI")
    metodo_pago = models.CharField(max_length=3, choices=METODO_PAGO_CHOICES, default='PUE', verbose_name="Método de Pago")
    forma_pago = models.CharField(max_length=3, choices=FORMA_PAGO_CHOICES, default='03', verbose_name="Forma de Pago")
    cfdi_uuid = models.UUIDField(blank=True, null=True, verbose_name="UUID Fiscal SAT")
    xml_file = models.FileField(upload_to='cfdi_xmls/', blank=True, null=True, verbose_name="Archivo XML SAT")

    # Control de Almacén y Surtido (Fase 3)
    stock_reservado = models.BooleanField(
        default=False,
        verbose_name="Stock Reservado en Bodega",
        help_text="Indica si el stock de este pedido fue apartado físicamente en el almacén"
    )
    fecha_surtido = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Fecha y Hora de Surtido / Despacho"
    )
    usuario_surtio = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='pedidos_surtidos',
        verbose_name="Usuario que Despachó el Pedido"
    )

    class Meta:
        verbose_name = "Pedido de Venta"
        verbose_name_plural = "Pedidos de Venta"
        ordering = ['-fecha_emision']

    def __str__(self):
        return f"{self.folio} - {self.cliente.razon_social} ({self.total} {self.moneda.codigo})"

    def recalcular_totales(self):
        """Calcula de forma atómica subtotal, IVA, total y total_mxn sumando sus partidas."""
        sub = sum((d.subtotal_linea for d in self.detalles.all()), Decimal('0.0000'))
        iva = round(sub * (self.tasa_iva / Decimal('100.0')), 4)
        tot = sub + iva
        tc = self.tipo_cambio_aplicado or Decimal('1.000000')
        tot_mxn = round(tot * tc, 4)

        self.subtotal = sub
        self.impuestos = iva
        self.total = tot
        self.total_mxn = tot_mxn
        self.save(update_fields=['subtotal', 'impuestos', 'total', 'total_mxn'])


class PedidoVenta_Detalle(models.Model):
    """Partidas individuales de productos dentro del pedido de venta."""
    pedido = models.ForeignKey(PedidoVenta_Maestro, related_name='detalles', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, verbose_name="Producto Vendido")
    cantidad = models.DecimalField(
        max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Cantidad"
    )
    precio_unitario = models.DecimalField(
        max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name="Precio Unitario"
    )
    subtotal_linea = models.DecimalField(
        max_digits=18, decimal_places=6,
        editable=False,
        verbose_name="Subtotal Partida"
    )
    cantidad_surtida = models.DecimalField(
        max_digits=18, decimal_places=6,
        default=Decimal('0.000000'),
        verbose_name="Cantidad Surtida / Entregada"
    )
    notas_linea = models.CharField(max_length=200, blank=True, null=True, verbose_name="Notas de Partida")

    class Meta:
        verbose_name = "Detalle de Pedido de Venta"
        verbose_name_plural = "Detalles de Pedido de Venta"

    def save(self, *args, **kwargs):
        self.subtotal_linea = round(self.cantidad * self.precio_unitario, 6)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cantidad} x {self.producto.sku} ({self.pedido.folio})"


# Compatibilidad y alias
Venta = PedidoVenta_Maestro
DetalleVenta = PedidoVenta_Detalle


# ==============================================================================
# FASE 5: FACTURACIÓN ELECTRÓNICA SAT (CFDI 4.0) Y COMPLEMENTO DE PAGOS (REP 2.0)
# ==============================================================================

class Factura_Maestro(models.Model):
    """
    Maestro de Facturación Electrónica SAT (CFDI 4.0).
    Soporta Comprobantes de Ingreso ('I') emitidos a partir de pedidos de venta surtidos,
    así como Recibos Electrónicos de Pago con Complemento de Pagos 2.0 ('P') y Notas de Crédito ('E').
    """
    TIPO_INGRESO = 'I'
    TIPO_PAGO = 'P'
    TIPO_EGRESO = 'E'
    TIPO_COMPROBANTE_CHOICES = [
        (TIPO_INGRESO, 'I - Ingreso (Factura)'),
        (TIPO_PAGO, 'P - Pago (Complemento de Recepción de Pagos)'),
        (TIPO_EGRESO, 'E - Egreso (Nota de Crédito)'),
    ]

    ESTADO_BORRADOR = 'BORRADOR'
    ESTADO_TIMBRADA = 'TIMBRADA'
    ESTADO_CANCELADA = 'CANCELADA'
    ESTADO_CHOICES = [
        (ESTADO_BORRADOR, 'Borrador'),
        (ESTADO_TIMBRADA, 'Timbrada / Vigente'),
        (ESTADO_CANCELADA, 'Cancelada'),
    ]

    METODO_PAGO_CHOICES = [
        ('PUE', 'PUE - Pago en una sola exhibición'),
        ('PPD', 'PPD - Pago en parcialidades o diferido'),
    ]

    FORMA_PAGO_CHOICES = [
        ('01', '01 - Efectivo'),
        ('02', '02 - Cheque nominativo'),
        ('03', '03 - Transferencia electrónica de fondos'),
        ('04', '04 - Tarjeta de crédito'),
        ('28', '28 - Tarjeta de débito'),
        ('99', '99 - Por definir'),
    ]

    pedido = models.ForeignKey(
        PedidoVenta_Maestro,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='facturas',
        verbose_name="Pedido de Venta Origen"
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name='facturas',
        verbose_name="Cliente Receptor"
    )

    tipo_comprobante = models.CharField(
        max_length=1,
        choices=TIPO_COMPROBANTE_CHOICES,
        default=TIPO_INGRESO,
        verbose_name="Tipo de Comprobante"
    )
    serie = models.CharField(max_length=10, default='F', verbose_name="Serie")
    folio = models.CharField(max_length=20, verbose_name="Folio Fiscal")

    uuid = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        verbose_name="Folio Fiscal SAT (UUID)"
    )
    fecha_emision = models.DateTimeField(default=timezone.now, verbose_name="Fecha de Emisión")
    fecha_timbrado = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de Certificación SAT")

    estado = models.CharField(
        max_length=15,
        choices=ESTADO_CHOICES,
        default=ESTADO_TIMBRADA,
        verbose_name="Estado Fiscal"
    )

    # Moneda y Tipo de Cambio
    moneda = models.ForeignKey(Moneda, on_delete=models.PROTECT, verbose_name="Moneda")
    tipo_cambio = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('1.000000'),
        verbose_name="Tipo de Cambio"
    )

    # Régimen de Pago y Fiscal SAT
    metodo_pago = models.CharField(
        max_length=3,
        choices=METODO_PAGO_CHOICES,
        default='PPD',
        verbose_name="Método de Pago"
    )
    forma_pago = models.CharField(
        max_length=3,
        choices=FORMA_PAGO_CHOICES,
        default='99',
        verbose_name="Forma de Pago"
    )
    uso_cfdi = models.CharField(max_length=4, default='G01', verbose_name="Uso de CFDI")
    condiciones_pago = models.CharField(max_length=150, blank=True, null=True, verbose_name="Condiciones de Pago")
    orden_compra_cliente = models.CharField(max_length=100, blank=True, null=True, verbose_name="Orden de Compra / Ref")

    # Importes Totales
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Subtotal")
    descuento = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Descuento")
    impuestos_trasladados = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="IVA Trasladado (16%)"
    )
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Total Comprobante")
    total_mxn = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Total en MXN")

    # Control de Cobranza y Saldo Insoluto para Facturas PPD
    saldo_insoluto = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo Insoluto Restante"
    )
    pagada = models.BooleanField(
        default=False,
        verbose_name="Factura Liquidada",
        help_text="Indica si la factura está totalmente cobrada (saldo insoluto = 0)"
    )

    # Datos Técnicos del Timbre Fiscal Digital (TFD 1.1)
    no_certificado_emisor = models.CharField(
        max_length=30,
        default='00001000000502019816',
        verbose_name="Serie Certificado Sello Digital (Emisor)"
    )
    no_certificado_sat = models.CharField(
        max_length=30,
        default='00001000000504204441',
        verbose_name="Serie Certificado Sello Digital SAT"
    )
    rfc_prov_certif = models.CharField(
        max_length=15,
        default='TSP080724QW6',
        verbose_name="RFC Proveedor Certificación (PAC)"
    )
    sello_cfd = models.TextField(blank=True, null=True, verbose_name="Sello Digital del Contribuyente (CFDI)")
    sello_sat = models.TextField(blank=True, null=True, verbose_name="Sello Digital del SAT")
    cadena_original = models.TextField(blank=True, null=True, verbose_name="Cadena Original de Certificación SAT")

    # Archivos
    xml_firmado = models.FileField(upload_to='cfdi/xml/', blank=True, null=True, verbose_name="Archivo XML Sellado")
    notas = models.TextField(blank=True, null=True, verbose_name="Notas del Comprobante")

    class Meta:
        verbose_name = "Factura / CFDI"
        verbose_name_plural = "Facturas y CFDI 4.0"
        ordering = ['-fecha_emision', '-id']

    def __str__(self):
        return f"{self.serie}-{self.folio} [{self.tipo_comprobante}] - {self.cliente.razon_social} (${self.total})"

    @property
    def folio_completo(self):
        return f"{self.serie}-{self.folio}"

    @property
    def es_ppd(self):
        return self.metodo_pago == 'PPD' and self.tipo_comprobante == self.TIPO_INGRESO

    @property
    def es_pago(self):
        return self.tipo_comprobante == self.TIPO_PAGO

    def recalcular_totales(self):
        """Calcula subtotal, impuestos y total a partir de sus conceptos."""
        if self.tipo_comprobante == self.TIPO_PAGO:
            # En comprobantes de pago el total en el CFDI es 0
            self.subtotal = Decimal('0.00')
            self.impuestos_trasladados = Decimal('0.00')
            self.total = Decimal('0.00')
            self.total_mxn = Decimal('0.00')
        else:
            detalles = list(self.detalles.all())
            sub = sum((d.importe for d in detalles), Decimal('0.00'))
            iva = sum((d.importe_iva for d in detalles), Decimal('0.00'))
            tot = sub + iva
            tc = self.tipo_cambio or Decimal('1.000000')
            self.subtotal = round(sub, 2)
            self.impuestos_trasladados = round(iva, 2)
            self.total = round(tot, 2)
            self.total_mxn = round(tot * tc, 2)
            if self.saldo_insoluto == Decimal('0.00') and not self.pagada:
                self.saldo_insoluto = self.total
        self.save(update_fields=['subtotal', 'impuestos_trasladados', 'total', 'total_mxn', 'saldo_insoluto'])


class Factura_Detalle(models.Model):
    """
    Conceptos o Partidas del CFDI 4.0.
    Cumple con claves SAT de producto, claves de unidad y desglose fiscal.
    """
    factura = models.ForeignKey(Factura_Maestro, related_name='detalles', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Producto")

    clave_prod_serv = models.CharField(
        max_length=10, default='24121800', verbose_name="Clave Prod/Serv SAT",
        help_text="Clave oficial SAT (ej. 24121800 para recipientes o empaques de hojalata)"
    )
    no_identificacion = models.CharField(max_length=50, blank=True, null=True, verbose_name="No. Identificación / SKU")
    clave_unidad = models.CharField(
        max_length=10, default='H87', verbose_name="Clave Unidad SAT",
        help_text="Clave oficial SAT (ej. H87 = Pieza, KGM = Kilogramo)"
    )
    unidad = models.CharField(max_length=50, default='Pieza', verbose_name="Nombre de Unidad")
    descripcion = models.TextField(verbose_name="Descripción del Concepto")

    cantidad = models.DecimalField(
        max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name="Cantidad"
    )
    valor_unitario = models.DecimalField(
        max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name="Valor Unitario"
    )
    importe = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Importe")

    # Impuestos del Concepto
    objeto_imp = models.CharField(max_length=3, default='02', verbose_name="Objeto de Impuesto")
    base_iva = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Base de IVA")
    tasa_iva = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal('0.160000'), verbose_name="Tasa de IVA")
    importe_iva = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Importe IVA")

    class Meta:
        verbose_name = "Concepto de Factura"
        verbose_name_plural = "Conceptos de Factura"

    def save(self, *args, **kwargs):
        self.importe = round(self.cantidad * self.valor_unitario, 2)
        if self.objeto_imp == '02':
            self.base_iva = self.importe
            self.importe_iva = round(self.base_iva * self.tasa_iva, 2)
        else:
            self.base_iva = Decimal('0.00')
            self.importe_iva = Decimal('0.00')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cantidad} {self.clave_unidad} - {self.descripcion[:40]}"


class ComplementoPago_Detalle(models.Model):
    """
    Documentos Relacionados en el Recibo Electrónico de Pago (Complemento de Pagos 2.0).
    Acredita el pago parcial o total contra una factura emitida previamente en PPD.
    """
    pago_cfdi = models.ForeignKey(
        Factura_Maestro,
        related_name='doctos_relacionados',
        on_delete=models.CASCADE,
        verbose_name="Comprobante de Pago (Tipo P)"
    )
    factura_origen = models.ForeignKey(
        Factura_Maestro,
        related_name='pagos_recibidos',
        on_delete=models.PROTECT,
        verbose_name="Factura Liquidada / Amortizada"
    )
    num_parcialidad = models.PositiveIntegerField(default=1, verbose_name="Número de Parcialidad")
    imp_saldo_ant = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Importe Saldo Anterior")
    imp_pagado = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Importe Pagado")
    imp_saldo_insoluto = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Importe Saldo Insoluto")
    equivalencia_dr = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1.000000'), verbose_name="Equivalencia DR"
    )

    # Desglose de IVA efectivamente cobrado en el abono
    base_iva = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Base IVA del Pago")
    importe_iva = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Importe IVA del Pago")

    class Meta:
        verbose_name = "Documento Relacionado de Pago"
        verbose_name_plural = "Documentos Relacionados de Pagos"

    def save(self, *args, **kwargs):
        # Desglosar base e IVA del abono (1.16)
        if self.imp_pagado > 0:
            self.base_iva = round(self.imp_pagado / Decimal('1.16'), 2)
            self.importe_iva = round(self.imp_pagado - self.base_iva, 2)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Pago a {self.factura_origen.folio_completo}: ${self.imp_pagado} (Parcialidad {self.num_parcialidad})"


