from django import forms
from .models import Cliente

class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            'rfc',
            'razon_social',
            'nombre_comercial',
            'regimen_fiscal',
            'codigo_postal',
            'uso_cfdi',
            'correo',
            'telefono',
            'direccion',
            'dias_credito',
            'limite_credito',
            'activo',
            'notas'
        ]
        widgets = {
            'rfc': forms.TextInput(attrs={
                'class': 'form-control font-monospace text-uppercase',
                'placeholder': 'Ej. XAXX010101000',
                'maxlength': '13'
            }),
            'razon_social': forms.TextInput(attrs={
                'class': 'form-control text-uppercase',
                'placeholder': 'Razón Social tal como consta en CSF del SAT'
            }),
            'nombre_comercial': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Nombre Comercial o Marca'
            }),
            'regimen_fiscal': forms.Select(attrs={
                'class': 'form-select'
            }),
            'codigo_postal': forms.TextInput(attrs={
                'class': 'form-control font-monospace',
                'placeholder': 'Ej. 06000',
                'maxlength': '5'
            }),
            'uso_cfdi': forms.Select(attrs={
                'class': 'form-select'
            }),
            'correo': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'facturacion@empresa.com'
            }),
            'telefono': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej. (55) 1234-5678'
            }),
            'direccion': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Calle, número exterior/interior, colonia, municipio, estado...'
            }),
            'dias_credito': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'placeholder': '0'
            }),
            'limite_credito': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'placeholder': '0.00'
            }),
            'activo': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'notas': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Condiciones de entrega, contactos secundarios u observaciones...'
            }),
        }

    def clean_rfc(self):
        rfc = self.cleaned_data.get('rfc')
        if rfc:
            return rfc.upper().strip().replace(' ', '')
        return rfc

    def clean_razon_social(self):
        razon = self.cleaned_data.get('razon_social')
        if razon:
            return razon.upper().strip()
        return razon


from .models import PedidoVenta_Maestro, PedidoVenta_Detalle

class PedidoVenta_MaestroForm(forms.ModelForm):
    class Meta:
        model = PedidoVenta_Maestro
        fields = [
            'tipo_documento',
            'cliente',
            'bodega_despacho',
            'moneda',
            'fecha_compromiso',
            'condiciones_pago',
            'referencia_cliente',
            'observaciones',
            'tasa_iva',
            'uso_cfdi',
            'metodo_pago',
            'forma_pago'
        ]
        widgets = {
            'tipo_documento': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25', 'id': 'id_tipo_documento'}),
            'cliente': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25', 'id': 'id_cliente'}),
            'bodega_despacho': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25', 'id': 'id_bodega_despacho'}),
            'moneda': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25', 'id': 'id_moneda'}),
            'fecha_compromiso': forms.DateInput(attrs={'class': 'form-control border-secondary border-opacity-25', 'type': 'date'}),
            'condiciones_pago': forms.TextInput(attrs={'class': 'form-control border-secondary border-opacity-25', 'placeholder': 'Ej. Contado o 30 días de crédito'}),
            'referencia_cliente': forms.TextInput(attrs={'class': 'form-control border-secondary border-opacity-25', 'placeholder': 'Ej. PO-2026-990'}),
            'observaciones': forms.Textarea(attrs={'class': 'form-control border-secondary border-opacity-25', 'rows': 2, 'placeholder': 'Instrucciones especiales de embarque o empaque...'}),
            'tasa_iva': forms.NumberInput(attrs={'class': 'form-control border-secondary border-opacity-25', 'step': '0.01', 'id': 'id_tasa_iva'}),
            'uso_cfdi': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25'}),
            'metodo_pago': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25'}),
            'forma_pago': forms.Select(attrs={'class': 'form-select border-secondary border-opacity-25'}),
        }

