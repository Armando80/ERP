from django import forms
from .models import Proveedor

class ProveedorForm(forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ['rfc', 'razon_social', 'nombre_comercial', 'email', 'telefono', 'direccion', 'dias_credito', 'activo']
        widgets = {
            'rfc': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej. XAXX010101000'}),
            'razon_social': forms.TextInput(attrs={'class': 'form-control'}),
            'nombre_comercial': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'contacto@empresa.com'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control'}),
            'direccion': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'dias_credito': forms.NumberInput(attrs={'class': 'form-control'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }