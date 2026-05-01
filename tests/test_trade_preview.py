import importlib
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd


with patch('schwabdev.Client', side_effect=RuntimeError('disabled in tests')):
    if 'ezoptionsschwab' in sys.modules:
        del sys.modules['ezoptionsschwab']
    ezoptionsschwab = importlib.import_module('ezoptionsschwab')


class MockResponse:
    def __init__(self, payload, ok=True, status_code=200, reason='OK', headers=None):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.reason = reason
        self.headers = headers or {}

    def json(self):
        return self._payload


class MockPreviewClient:
    def __init__(self, positions_payload=None):
        self.preview_calls = []
        self.place_calls = []
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

    def place_order(self, account_hash, order):
        self.place_calls.append((account_hash, order))
        return MockResponse(
            None,
            ok=True,
            status_code=201,
            reason='Created',
            headers={'Location': 'https://api.schwabapi.com/trader/v1/accounts/HASH123/orders/987654321'},
        )

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
        ezoptionsschwab._trade_preview_records.clear()
        self.original_client = ezoptionsschwab.client
        ezoptionsschwab.client = MockPreviewClient()
        self.app = ezoptionsschwab.app.test_client()

    def tearDown(self):
        ezoptionsschwab.client = self.original_client
        ezoptionsschwab._options_cache.clear()
        ezoptionsschwab._trade_preview_records.clear()

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


class TradePlaceOrderEndpointTest(unittest.TestCase):
    def setUp(self):
        seed_chain()
        ezoptionsschwab._trade_preview_records.clear()
        self.original_client = ezoptionsschwab.client
        ezoptionsschwab.client = MockPreviewClient()
        self.app = ezoptionsschwab.app.test_client()

    def tearDown(self):
        ezoptionsschwab.client = self.original_client
        ezoptionsschwab._options_cache.clear()
        ezoptionsschwab._trade_preview_records.clear()

    def preview_payload(self, **overrides):
        payload = {
            'account_hash': 'HASH123',
            'ticker': 'SPY',
            'contract_symbol': 'SPY   260501C00722000',
            'instruction': 'BUY_TO_OPEN',
            'quantity': 2,
            'limit_price': 0.57,
        }
        payload.update(overrides)
        return payload

    def post_preview(self, **overrides):
        return self.app.post('/trade/preview_order', json=self.preview_payload(**overrides))

    def place_payload_from_preview(self, preview_data, **overrides):
        leg = preview_data['order']['orderLegCollection'][0]
        payload = {
            'account_hash': 'HASH123',
            'ticker': 'SPY',
            'contract_symbol': leg['instrument']['symbol'],
            'instruction': leg['instruction'],
            'quantity': leg['quantity'],
            'limit_price': preview_data['order']['price'],
            'preview_token': preview_data['preview_token'],
            'order': preview_data['order'],
            'confirmed': True,
        }
        payload.update(overrides)
        return payload

    def post_place(self, payload):
        return self.app.post('/trade/place_order', json=payload)

    def test_feature_flag_off_rejects_placement(self):
        preview = self.post_preview().get_json()
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '0'}):
            response = self.post_place(self.place_payload_from_preview(preview))
        self.assertEqual(response.status_code, 403)
        self.assertIn('disabled', response.get_json()['error'])
        self.assertEqual(ezoptionsschwab.client.place_calls, [])

    def test_missing_preview_token_rejects_placement(self):
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place({
                'account_hash': 'HASH123',
                'ticker': 'SPY',
                'contract_symbol': 'SPY   260501C00722000',
                'instruction': 'BUY_TO_OPEN',
                'quantity': 2,
                'limit_price': 0.57,
                'confirmed': True,
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn('preview token', response.get_json()['error'].lower())

    def test_stale_preview_rejects_placement(self):
        preview = self.post_preview().get_json()
        token = preview['preview_token']
        ezoptionsschwab._trade_preview_records[token]['created_at'] -= ezoptionsschwab.TRADE_PREVIEW_TTL_SECONDS + 1
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview))
        self.assertEqual(response.status_code, 400)
        self.assertIn('stale', response.get_json()['error'].lower())

    def test_changed_order_fields_reject_placement(self):
        preview = self.post_preview().get_json()
        changed_cases = (
            {'account_hash': 'OTHERHASH'},
            {'contract_symbol': 'SPY   260501C00999000'},
            {'instruction': 'SELL_TO_CLOSE'},
            {'quantity': 3},
            {'limit_price': '0.58'},
        )
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            for override in changed_cases:
                response = self.post_place(self.place_payload_from_preview(preview, **override))
                self.assertEqual(response.status_code, 400, override)
        self.assertEqual(ezoptionsschwab.client.place_calls, [])

    def test_changed_order_json_rejects_placement(self):
        preview = self.post_preview().get_json()
        changed_order = dict(preview['order'])
        changed_order['price'] = '0.58'
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview, order=changed_order))
        self.assertEqual(response.status_code, 400)
        self.assertIn('order json', response.get_json()['error'].lower())

    def test_missing_explicit_confirmation_rejects_placement(self):
        preview = self.post_preview().get_json()
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview, confirmed=False))
        self.assertEqual(response.status_code, 400)
        self.assertIn('confirmation', response.get_json()['error'].lower())
        self.assertEqual(ezoptionsschwab.client.place_calls, [])

    def test_valid_flagged_request_places_exact_previewed_order_and_returns_location(self):
        preview = self.post_preview().get_json()
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview))
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['placed'])
        self.assertEqual(data['schwab_status'], 201)
        self.assertIn('/orders/987654321', data['location'])
        self.assertEqual(ezoptionsschwab.client.place_calls, [('HASH123', preview['order'])])

    def test_place_response_does_not_expose_plain_account_numbers(self):
        preview = self.post_preview().get_json()
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview))
        body = response.get_data(as_text=True)
        self.assertNotIn('123456789', body)


if __name__ == '__main__':
    unittest.main()
