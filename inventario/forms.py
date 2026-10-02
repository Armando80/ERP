# ERP/inventario/forms.py

from django import forms
from .models import Producto, MovimientoInventario

class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        # Excluimos explícitamente 'costo_promedio_mxn' porque ese valor
        # SOLO debe modificarse a través de las señales del Kardex, nunca a mano.
        fields = [
            'sku',
            'nombre',
            'tipo',
            'unidad_medida',
            'moneda_base_costo',
            'moneda_base_venta',
            'descripcion'
        ]

        # Widgets para inyectar las clases de Bootstrap 5 y placeholders
        widgets = {
            'sku': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: AE-001'
            }),
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Nombre completo del material o producto'
            }),
            'tipo': forms.Select(attrs={
                'class': 'form-select'
            }),
            'unidad_medida': forms.Select(attrs={
                'class': 'form-select'
            }),
            'moneda_base_costo': forms.Select(attrs={
                'class': 'form-select'
            }),
            'moneda_base_venta': forms.Select(attrs={
                'class': 'form-select border-secondary border-opacity-25'
            }),
            'descripcion': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Especificaciones técnicas u observaciones...'
            }),
        }

        labels = {
            'sku': 'SKU / Código',
            'nombre': 'Nombre del Producto',
            'tipo': 'Clasificación',
            'unidad_medida': 'Unidad de Medida',
            'moneda_base_costo': 'Moneda de Costo',
            'moneda_base_venta': 'Moneda de Venta',
        }

    # Validación personalizada: Forzar que el SKU siempre se guarde en MAYÚSCULAS
    def clean_sku(self):
        sku = self.cleaned_data.get('sku')
        if sku:
            return sku.upper().strip()
        return sku

from decimal import Decimal
from general.models import Moneda
from general.services import obtener_tipo_cambio_vigente

class MovimientoForm(forms.ModelForm):
    moneda_original = forms.ModelChoiceField(
        queryset=Moneda.objects.all().order_by('codigo'),
        required=False,
        empty_label=None,
        widget=forms.Select(attrs={
            'class': 'form-select border-secondary border-opacity-25',
            'id': 'id_moneda_original'
        }),
        label="Moneda de Origen"
    )
    costo_unitario_original = forms.DecimalField(
        max_digits=18,
        decimal_places=6,
        required=False,
        min_value=Decimal('0.000000'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control border-secondary border-opacity-25',
            'placeholder': '0.00',
            'step': '0.0001',
            'id': 'id_costo_unitario_original'
        }),
        label="Costo Unitario Original"
    )
    tipo_cambio_aplicado = forms.DecimalField(
        max_digits=18,
        decimal_places=6,
        required=False,
        min_value=Decimal('0.000001'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control border-secondary border-opacity-25',
            'placeholder': '1.000000',
            'step': '0.0001',
            'id': 'id_tipo_cambio_aplicado'
        }),
        label="Tipo de Cambio Aplicado"
    )
    costo_unitario_mxn_capturado = forms.DecimalField(
        max_digits=18,
        decimal_places=6,
        required=False,
        min_value=Decimal('0.000000'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control bg-light border-secondary border-opacity-25 fw-bold text-success',
            'placeholder': '0.00',
            'step': '0.0001',
            'id': 'id_costo_unitario_mxn_capturado'
        }),
        label="Costo Unitario Resultante (MXN)"
    )

    class Meta:
        model = MovimientoInventario
        fields = [
            'tipo_movimiento',
            'producto',
            'bodega_origen',
            'bodega_destino',
            'cantidad',
            'moneda_original',
            'costo_unitario_original',
            'tipo_cambio_aplicado',
            'costo_unitario_mxn_capturado',
            'referencia_operacion',
            'observaciones'
        ]

        widgets = {
            'tipo_movimiento': forms.Select(attrs={
                'class': 'form-select border-secondary border-opacity-25',
                'id': 'id_tipo_movimiento'
            }),
            'producto': forms.Select(attrs={
                'class': 'form-select border-secondary border-opacity-25',
                'id': 'id_producto'
            }),
            'bodega_origen': forms.Select(attrs={
                'class': 'form-select border-secondary border-opacity-25',
                'id': 'id_bodega_origen'
            }),
            'bodega_destino': forms.Select(attrs={
                'class': 'form-select border-secondary border-opacity-25',
                'id': 'id_bodega_destino'
            }),
            'cantidad': forms.NumberInput(attrs={
                'class': 'form-control border-secondary border-opacity-25',
                'placeholder': 'Ej. 100',
                'min': '0.000001',
                'step': '0.01',
                'id': 'id_cantidad'
            }),
            'referencia_operacion': forms.TextInput(attrs={
                'class': 'form-control border-secondary border-opacity-25',
                'placeholder': 'Ej. OP-2026-004 o Factura #1234'
            }),
            'observaciones': forms.Textarea(attrs={
                'class': 'form-control border-secondary border-opacity-25',
                'rows': 2
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Moneda inicial por defecto: MXN
        if not self.is_bound:
            if not self.initial.get('moneda_original'):
                moneda_mxn = Moneda.objects.filter(codigo='MXN').first()
                if moneda_mxn:
                    self.initial['moneda_original'] = moneda_mxn
            if not self.initial.get('tipo_cambio_aplicado'):
                self.initial['tipo_cambio_aplicado'] = Decimal('1.000000')

    def clean(self):
        cleaned_data = super().clean()
        tipo_mov = cleaned_data.get('tipo_movimiento')
        moneda = cleaned_data.get('moneda_original')
        costo_orig = cleaned_data.get('costo_unitario_original')
        tc = cleaned_data.get('tipo_cambio_aplicado')
        costo_mxn = cleaned_data.get('costo_unitario_mxn_capturado')

        if tipo_mov == MovimientoInventario.ENTRADA:
            if not moneda:
                moneda = Moneda.objects.filter(codigo='MXN').first()
                cleaned_data['moneda_original'] = moneda

            cod_moneda = moneda.codigo if moneda else 'MXN'

            if costo_orig is not None:
                if costo_orig <= Decimal('0'):
                    self.add_error('costo_unitario_original', 'El costo unitario de una Entrada debe ser mayor a 0.')

                if cod_moneda == 'MXN':
                    tc = Decimal('1.000000')
                    costo_mxn = costo_orig
                else:
                    if not tc or tc <= Decimal('0'):
                        try:
                            tc = obtener_tipo_cambio_vigente(cod_moneda)
                        except Exception:
                            tc = Decimal('1.000000')
                    costo_mxn = round(costo_orig * tc, 6)

                cleaned_data['tipo_cambio_aplicado'] = tc
                cleaned_data['costo_unitario_mxn_capturado'] = costo_mxn

            elif costo_mxn is not None and costo_mxn > Decimal('0'):
                cleaned_data['costo_unitario_original'] = costo_mxn
                cleaned_data['tipo_cambio_aplicado'] = Decimal('1.000000')
                if not moneda:
                    cleaned_data['moneda_original'] = Moneda.objects.filter(codigo='MXN').first()
            else:
                self.add_error('costo_unitario_original', 'Debe capturar el costo unitario para la Entrada.')

        else:
            cleaned_data['costo_unitario_original'] = Decimal('0.000000')
            cleaned_data['tipo_cambio_aplicado'] = Decimal('1.000000')
            cleaned_data['costo_unitario_mxn_capturado'] = Decimal('0.000000')

        return cleaned_data


class SolicitarAnulacionForm(forms.Form):
    motivo_solicitud = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control border-secondary border-opacity-25',
            'rows': 3,
            'placeholder': 'Describa detalladamente el motivo operativo de la anulación (error en captura, mercancía devuelta, remisión duplicada, etc.)...'
        }),
        label="Motivo de la Anulación",
        min_length=5,
        required=True,
        help_text="Mínimo 5 caracteres. Este motivo quedará registrado permanentemente en la pista de auditoría."
    )