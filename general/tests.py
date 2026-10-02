import json
import datetime
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.management import call_command

from general.models import Moneda, TipoCambio
from general.services import (
    BanxicoClient,
    BanxicoAPIError,
    TipoCambioNoDisponibleError,
    sincronizar_tipos_cambio_banxico,
    obtener_tipo_cambio_vigente,
)


class BanxicoServiceTests(TestCase):
    """Pruebas unitarias para el servicio de integración con Banxico y resolución multidivisa."""

    def setUp(self):
        self.moneda_mxn, _ = Moneda.objects.get_or_create(codigo='MXN', defaults={'nombre': 'Peso Mexicano', 'simbolo': '$'})
        self.moneda_usd, _ = Moneda.objects.get_or_create(codigo='USD', defaults={'nombre': 'Dólar Americano', 'simbolo': '$'})
        self.moneda_eur, _ = Moneda.objects.get_or_create(codigo='EUR', defaults={'nombre': 'Euro Zona', 'simbolo': '€'})

        self.user = User.objects.create_user(username='admin_test', password='password123', is_staff=True)
        self.client = Client()

    def test_parsear_respuesta_banxico_valida_y_con_ne(self):
        """Valida que el cliente parsea correctamente números, fechas y descarta días inhábiles ('N/E')."""
        payload_simulado = {
            "bmx": {
                "series": [
                    {
                        "idSerie": "SF43718",
                        "titulo": "Tipo de cambio FIX",
                        "datos": [
                            {"fecha": "27/09/2026", "dato": "N/E"},        # Domingo inhábil
                            {"fecha": "28/09/2026", "dato": "19.4520"},     # Lunes hábil
                            {"fecha": "29/09/2026", "dato": "19.5100"}      # Martes hábil
                        ]
                    },
                    {
                        "idSerie": "SF46410",
                        "titulo": "Cotización Euro",
                        "datos": [
                            {"fecha": "29/09/2026", "dato": "21.6500"}
                        ]
                    }
                ]
            }
        }

        b_client = BanxicoClient(token="mock_token_123")
        resultado = b_client._parsear_respuesta(json.dumps(payload_simulado))

        # USD
        self.assertIn("SF43718", resultado)
        datos_usd = resultado["SF43718"]["datos"]
        self.assertEqual(len(datos_usd), 2)  # El registro N/E debe ser descartado
        self.assertEqual(datos_usd[0]["fecha"], datetime.date(2026, 9, 28))
        self.assertEqual(datos_usd[0]["valor"], Decimal("19.4520"))
        self.assertEqual(datos_usd[1]["fecha"], datetime.date(2026, 9, 29))
        self.assertEqual(datos_usd[1]["valor"], Decimal("19.5100"))

        # EUR
        self.assertIn("SF46410", resultado)
        datos_eur = resultado["SF46410"]["datos"]
        self.assertEqual(len(datos_eur), 1)
        self.assertEqual(datos_eur[0]["valor"], Decimal("21.6500"))

    def test_sincronizar_tipos_cambio_guarda_en_bd(self):
        """Prueba que la sincronización crea o actualiza los registros en TipoCambio."""
        mock_client = MagicMock()
        mock_client.consultar_series.return_value = {
            "SF43718": {
                "titulo": "FIX USD",
                "datos": [{"fecha": datetime.date(2026, 9, 29), "valor": Decimal("19.3500")}]
            },
            "SF46410": {
                "titulo": "EUR",
                "datos": [{"fecha": datetime.date(2026, 9, 29), "valor": Decimal("21.4000")}]
            }
        }

        res = sincronizar_tipos_cambio_banxico(cliente=mock_client)
        self.assertTrue(res['exito'])
        self.assertEqual(res['total'], 2)

        tc_usd = TipoCambio.objects.get(moneda_origen=self.moneda_usd, fecha=datetime.date(2026, 9, 29))
        self.assertEqual(tc_usd.valor_en_mxn, Decimal("19.3500"))
        self.assertEqual(tc_usd.fuente, "BANXICO_FIX")

        tc_eur = TipoCambio.objects.get(moneda_origen=self.moneda_eur, fecha=datetime.date(2026, 9, 29))
        self.assertEqual(tc_eur.valor_en_mxn, Decimal("21.4000"))

    def test_obtener_tipo_cambio_vigente_mxn_siempre_uno(self):
        """MXN frente a MXN siempre debe ser exactamente 1.000000."""
        rate = obtener_tipo_cambio_vigente('MXN')
        self.assertEqual(rate, Decimal('1.000000'))
        rate_obj = obtener_tipo_cambio_vigente(self.moneda_mxn)
        self.assertEqual(rate_obj, Decimal('1.000000'))

    def test_obtener_tipo_cambio_vigente_fallback_legal_fin_de_semana(self):
        """
        Si se consulta una fecha de fin de semana o festivo donde no hay FIX publicado,
        debe aplicar el del último día hábil anterior (Art. 20 CFF).
        """
        # Viernes 25 de septiembre
        fecha_viernes = datetime.date(2026, 9, 25)
        TipoCambio.objects.create(
            moneda_origen=self.moneda_usd,
            fecha=fecha_viernes,
            valor_en_mxn=Decimal('19.4000'),
            fuente='BANXICO_FIX'
        )

        # Consultar el Domingo 27 de septiembre (donde no hay tipo de cambio propio)
        fecha_domingo = datetime.date(2026, 9, 27)
        rate_domingo = obtener_tipo_cambio_vigente(self.moneda_usd, fecha=fecha_domingo)
        self.assertEqual(rate_domingo, Decimal('19.4000'))

    def test_obtener_tipo_cambio_vigente_error_si_no_existe_registro(self):
        """Si no hay ningún registro en BD ni se puede sincronizar, lanza TipoCambioNoDisponibleError."""
        TipoCambio.objects.all().delete()
        with patch('general.services.sincronizar_tipos_cambio_banxico', side_effect=BanxicoAPIError("Sin token")):
            with self.assertRaises(TipoCambioNoDisponibleError):
                obtener_tipo_cambio_vigente('USD', fecha=datetime.date(2020, 1, 1))

    def test_vista_htmx_sincronizar_tipo_cambio(self):
        """Verifica que el endpoint HTMX sincroniza y devuelve el fragmento HTML de tarjetas."""
        self.client.force_login(self.user)

        with patch('general.views.sincronizar_tipos_cambio_banxico') as mock_sync:
            mock_sync.return_value = {'total': 2}
            url = reverse('general:sincronizar_tipo_cambio')

            response = self.client.post(url, HTTP_HX_REQUEST='true')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'DÓLAR AMERICANO (FIX)')
            self.assertContains(response, 'EURO ZONA')
            self.assertContains(response, 'MONEDA LOCAL')

    def test_comando_actualizar_tipocambio_simular(self):
        """Prueba que el comando CLI ejecuta correctamente con la bandera --simular."""
        call_command('actualizar_tipocambio', simular=True)
        # Verificar que se crearon los tipos de cambio de contingencia
        self.assertTrue(TipoCambio.objects.filter(moneda_origen__codigo='USD').exists())
        self.assertTrue(TipoCambio.objects.filter(moneda_origen__codigo='EUR').exists())
