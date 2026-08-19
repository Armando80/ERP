# ERP/inventario/forms.py

from django import forms
from .models import Producto

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
                'class': 'form-select'
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