# ERP Decorlata - produccion/forms.py
from django import forms
from decimal import Decimal
from inventario.models import Producto, Bodega
from .models import ListaMaterialesBOM, InsumoBOM, OrdenProduccion


class ListaMaterialesBOMForm(forms.ModelForm):
    """Formulario para la cabecera de la Lista de Materiales (BOM)."""
    class Meta:
        model = ListaMaterialesBOM
        fields = ['producto_terminado', 'cantidad_base', 'descripcion_proceso', 'activo']
        widgets = {
            'producto_terminado': forms.Select(attrs={'class': 'form-select'}),
            'cantidad_base': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'descripcion_proceso': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Instrucciones técnicas de fabricación...'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'producto_terminado': 'Producto a Fabricar',
            'cantidad_base': 'Cantidad Base',
            'descripcion_proceso': 'Ruta / Instrucciones de Proceso',
            'activo': 'Receta Activa',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Productos que pueden fabricarse (PT, HL, HC, CC)
        tipos_fabricables = [
            Producto.PRODUCTO_TERMINADO,
            Producto.HOJA_LITOGRAFIADA,
            Producto.HOJA_CORTADA,
            Producto.PLANTILLA
        ]
        self.fields['producto_terminado'].queryset = Producto.objects.filter(
            tipo__in=tipos_fabricables
        ).order_by('nombre')


class InsumoBOMForm(forms.ModelForm):
    """Formulario para agregar o editar un insumo en la receta."""
    class Meta:
        model = InsumoBOM
        fields = ['materia_prima', 'cantidad_requerida', 'porcentaje_merma']
        widgets = {
            'materia_prima': forms.Select(attrs={'class': 'form-select'}),
            'cantidad_requerida': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001', 'min': '0.000001', 'placeholder': '0.0000'}),
            'porcentaje_merma': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1', 'min': '0.00', 'placeholder': '0.00'}),
        }
        labels = {
            'materia_prima': 'Insumo / Materia Prima',
            'cantidad_requerida': 'Cantidad Requerida (para cantidad base)',
            'porcentaje_merma': '% Merma / Desperdicio',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Insumos: MP, Componentes, Hojalata en Rollo, Hojas cortadas o litografiadas
        tipos_insumos = [
            Producto.MATERIA_PRIMA,
            Producto.COMPONENTE,
            Producto.HOJALATA_ROLLO,
            Producto.HOJA_CORTADA,
            Producto.HOJA_LITOGRAFIADA,
            Producto.PLANTILLA
        ]
        self.fields['materia_prima'].queryset = Producto.objects.filter(
            tipo__in=tipos_insumos
        ).order_by('nombre')


class OrdenProduccionForm(forms.ModelForm):
    """Formulario para crear una Orden de Producción."""
    class Meta:
        model = OrdenProduccion
        fields = [
            'producto_a_fabricar',
            'cantidad_a_producir',
            'bodega_origen_insumos',
            'bodega_destino_pt',
            'fecha_compromiso',
            'observaciones'
        ]
        widgets = {
            'producto_a_fabricar': forms.Select(attrs={
                'class': 'form-select',
                'hx-get': '/produccion/api/previsualizar-bom/',
                'hx-target': '#preview-insumos-op',
                'hx-trigger': 'change',
                'hx-include': '#id_cantidad_a_producir, #id_bodega_origen_insumos'
            }),
            'cantidad_a_producir': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0.01',
                'hx-get': '/produccion/api/previsualizar-bom/',
                'hx-target': '#preview-insumos-op',
                'hx-trigger': 'keyup delay:400ms, change',
                'hx-include': '#id_producto_a_fabricar, #id_bodega_origen_insumos'
            }),
            'bodega_origen_insumos': forms.Select(attrs={
                'class': 'form-select',
                'hx-get': '/produccion/api/previsualizar-bom/',
                'hx-target': '#preview-insumos-op',
                'hx-trigger': 'change',
                'hx-include': '#id_producto_a_fabricar, #id_cantidad_a_producir'
            }),
            'bodega_destino_pt': forms.Select(attrs={'class': 'form-select'}),
            'fecha_compromiso': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'observaciones': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Notas sobre especificaciones del cliente o proceso...'}),
        }
        labels = {
            'producto_a_fabricar': 'Producto a Fabricar',
            'cantidad_a_producir': 'Cantidad a Producir',
            'bodega_origen_insumos': 'Almacén de Materias Primas / Componentes',
            'bodega_destino_pt': 'Almacén Destino (Producto Fabricado)',
            'fecha_compromiso': 'Fecha Compromiso',
            'observaciones': 'Observaciones / Notas',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mostrar productos que tengan receta BOM definida
        self.fields['producto_a_fabricar'].queryset = Producto.objects.filter(
            receta_bom__isnull=False,
            receta_bom__activo=True
        ).order_by('nombre')
        self.fields['bodega_origen_insumos'].queryset = Bodega.objects.all().order_by('nombre')
        self.fields['bodega_destino_pt'].queryset = Bodega.objects.all().order_by('nombre')


class FinalizarOrdenForm(forms.Form):
    """Formulario para capturar el resultado de la producción y cerrar la OP."""
    cantidad_producida = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal('0.0001'),
        label="Cantidad Producida Real",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    lote_fabricacion = forms.CharField(
        max_length=50,
        required=False,
        label="Lote de Fabricación",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej. LOT-2026-001 (opcional, auto-generado si vacío)'})
    )
    observaciones = forms.CharField(
        required=False,
        label="Observaciones de Cierre",
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Incidencias, mermas o novedades en planta...'})
    )


class NotificarEntregaParcialForm(forms.Form):
    """Formulario para que producción notifique una entrega parcial de producto terminado."""
    cantidad_notificada = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal('0.0001'),
        label="Cantidad Terminada en este Lote / Turno",
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-control-lg fw-bold text-primary',
            'step': '0.01',
            'placeholder': '0.00'
        })
    )
    lote_fabricacion = forms.CharField(
        max_length=50,
        required=False,
        label="Lote de Fabricación",
        widget=forms.TextInput(attrs={
            'class': 'form-control font-monospace',
            'placeholder': 'Ej. LOT-2026-001 (opcional, auto-generado si vacío)'
        })
    )
    observaciones_produccion = forms.CharField(
        required=False,
        label="Observaciones del Turno / Producción",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Detalles del turno, operador de línea o especificaciones de calidad...'
        })
    )

