import importlib
import sys
import unittest
from unittest.mock import patch

import pandas as pd


with patch('schwabdev.Client', side_effect=RuntimeError('disabled in tests')):
    if 'ezoptionsschwab' in sys.modules:
        del sys.modules['ezoptionsschwab']
    ezoptionsschwab = importlib.import_module('ezoptionsschwab')


class MockResponse:
    def __init__(self, payload, ok=True, status_code=200, reason='OK'):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.reason = reason

    def json(self):
        return self._payload


class MockPreviewClient:
    def __init__(self, positions_payload=None):
        self.preview_calls = []
        self.positions_payload = positions_payload or {
            'securitiesAccount': {
                'positions': [
                    {
                        'longQuantity': 3,
                        'shortQuantity': 0,
                        'instrument': {
                            'symbol': 'SPY   260501C00722000',
                            'assetType': 'OPTION',
                            'underlyingSymbol': 'SPY',
                        },
                    }
                ]
            }
        }

    def preview_order(self, account_hash, order):
        self.preview_calls.append((account_hash, order))
        return MockResponse({'status': 'ACCEPTED', 'accountNumber': '123456789'})

    def account_details(self, account_hash, fields=None):
        return MockResponse(self.positions_payload)


def seed_chain():
    ezoptionsschwab._options_cache.clear()
    ezoptionsschwab._options_cache['SPY'] = {
        'S': 721.85,
        'calls': pd.DataFrame([
            {
                'contractSymbol': 'SPY   260501C00722000',
                'strike': 722.0,
                'lastPrice': 0.57,
                'bid': 0.56,
                'ask': 0.57,
                'mark': 0.565,
                'volume': 1621,
                'openInterest': 255,
                'impliedVolatility': 0.21,
                'inTheMoney': False,
                'expiration': '2026-05-01',
                'quoteTimeInLong': 1777665600000,
                'tradeTimeInLong': 1777665600000,
                'delta': 0.48,
                'gamma': 0.01,
                'theta': -0.05,
                'vega': 0.02,
                'rho': 0.0,
            }
        ]),
        'puts': pd.DataFrame(),
    }


class TradePreviewEndpointTest(unittest.TestCase):
    def setUp(self):
        seed_chain()
        self.original_client = ezoptionsschwab.client
        ezoptionsschwab.client = MockPreviewClient()
        self.app = ezoptionsschwab.app.test_client()

    def tearDown(self):
        ezoptionsschwab.client = self.original_client
        ezoptionsschwab._options_cache.clear()

    def post_preview(self, **overrides):
        payload = {
            'account_hash': 'HASH123',
            'ticker': 'SPY',
            'contract_symbol': 'SPY   260501C00722000',
            'instruction': 'BUY_TO_OPEN',
            'quantity': 2,
            'limit_price': 0.57,
        }
        payload.update(overrides)
        return self.app.post('/trade/preview_order', json=payload)

    def test_rejects_missing_account(self):
        response = self.post_preview(account_hash='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Missing account hash', response.get_json()['error'])

    def test_rejects_missing_or_unknown_contract(self):
        response = self.post_preview(contract_symbol='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('cached trading chain', response.get_json()['error'])

        response = self.post_preview(contract_symbol='SPY   260501C00999000')
        self.assertEqual(response.status_code, 400)
        self.assertIn('cached trading chain', response.get_json()['error'])

    def test_rejects_invalid_quantity_price_and_action(self):
        for override in (
            {'quantity': 0},
            {'quantity': '1.5'},
            {'limit_price': 0},
            {'limit_price': 'bad'},
            {'instruction': 'SELL_SHORT'},
        ):
            response = self.post_preview(**override)
            self.assertEqual(response.status_code, 400, override)

    def test_builds_expected_schwab_preview_payload_for_buy_to_open(self):
        response = self.post_preview()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['order']['orderType'], 'LIMIT')
        self.assertEqual(data['order']['duration'], 'DAY')
        self.assertEqual(data['order']['session'], 'NORMAL')
        self.assertEqual(data['order']['orderStrategyType'], 'SINGLE')
        self.assertEqual(data['order']['price'], '0.57')
        leg = data['order']['orderLegCollection'][0]
        self.assertEqual(leg['instruction'], 'BUY_TO_OPEN')
        self.assertEqual(leg['quantity'], 2)
        self.assertEqual(leg['instrument']['assetType'], 'OPTION')
        self.assertEqual(leg['instrument']['symbol'], 'SPY   260501C00722000')
        self.assertEqual(ezoptionsschwab.client.preview_calls[0][0], 'HASH123')
        self.assertIn('preview_token', data)

    def test_sell_to_close_validates_available_selected_contract_position(self):
        response = self.post_preview(instruction='SELL_TO_CLOSE', quantity=3)
        self.assertEqual(response.status_code, 200)

        response = self.post_preview(instruction='SELL_TO_CLOSE', quantity=4)
        self.assertEqual(response.status_code, 400)
        self.assertIn('long position', response.get_json()['error'])

    def test_preview_response_does_not_expose_plain_account_numbers(self):
        response = self.post_preview()
        body = response.get_data(as_text=True)
        self.assertNotIn('123456789', body)
        self.assertIn('[redacted]', body)


if __name__ == '__main__':
    unittest.main()
