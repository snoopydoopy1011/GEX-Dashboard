import importlib
import os
import sys
import tempfile
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
        self.order_calls = []
        self.cancel_calls = []
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

    def linked_accounts(self):
        return MockResponse([
            {
                'hashValue': 'HASH123',
                'accountNumber': '123456789',
                'displayName': 'Primary 123456789',
                'type': 'MARGIN',
            },
            {
                'hashValue': 'HASH456',
                'maskedAccountNumber': 'IRA ****6789',
                'displayName': 'IRA 987654321',
                'type': 'CASH',
            },
        ])

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

    def account_orders(self, account_hash, from_entered_time, to_entered_time, maxResults=None, status=None):
        self.order_calls.append((account_hash, from_entered_time, to_entered_time, maxResults, status))
        return MockResponse([
            {
                'orderId': 111,
                'status': 'WORKING',
                'enteredTime': '2026-05-01T14:30:00Z',
                'orderType': 'LIMIT',
                'price': 0.57,
                'accountNumber': '123456789',
                'orderLegCollection': [
                    {
                        'instruction': 'BUY_TO_OPEN',
                        'quantity': 2,
                        'instrument': {
                            'symbol': 'SPY   260501C00722000',
                            'assetType': 'OPTION',
                            'underlyingSymbol': 'SPY',
                        },
                    }
                ],
            },
            {
                'orderId': 333,
                'status': 'WORKING',
                'enteredTime': '2026-05-01T14:45:00Z',
                'orderType': 'LIMIT',
                'price': 0.41,
                'orderLegCollection': [
                    {
                        'instruction': 'BUY_TO_OPEN',
                        'quantity': 1,
                        'instrument': {
                            'symbol': 'SPY   260501C00723000',
                            'assetType': 'OPTION',
                            'underlyingSymbol': 'SPY',
                        },
                    }
                ],
            },
            {
                'orderId': 222,
                'status': 'FILLED',
                'enteredTime': '2026-05-01T13:30:00Z',
                'orderType': 'LIMIT',
                'price': 1.11,
                'orderLegCollection': [
                    {
                        'instruction': 'BUY_TO_OPEN',
                        'quantity': 1,
                        'instrument': {
                            'symbol': 'QQQ   260501C00450000',
                            'assetType': 'OPTION',
                            'underlyingSymbol': 'QQQ',
                        },
                    }
                ],
            },
        ])

    def cancel_order(self, account_hash, order_id):
        self.cancel_calls.append((account_hash, order_id))
        return MockResponse(None, ok=True, status_code=204, reason='No Content')

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
        self.original_db_path = ezoptionsschwab.DB_PATH
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db_path = tmp.name
        tmp.close()
        ezoptionsschwab.DB_PATH = self.temp_db_path
        ezoptionsschwab.init_db()
        ezoptionsschwab.client = MockPreviewClient()
        self.app = ezoptionsschwab.app.test_client()

    def tearDown(self):
        ezoptionsschwab.client = self.original_client
        ezoptionsschwab.DB_PATH = self.original_db_path
        try:
            os.unlink(self.temp_db_path)
        except OSError:
            pass
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

    def test_successful_preview_records_local_journal_event(self):
        response = self.post_preview()
        self.assertEqual(response.status_code, 200)
        journal = self.app.get('/trade/journal').get_json()
        self.assertEqual(len(journal['events']), 1)
        event = journal['events'][0]
        self.assertEqual(event['event_type'], 'previewed_order')
        self.assertEqual(event['ticker'], 'SPY')
        self.assertEqual(event['contract_symbol'], 'SPY   260501C00722000')
        self.assertEqual(event['journal_status'], 'planned')
        self.assertIn('bracket_plan', event['details'])

    def test_journal_event_annotations_are_editable(self):
        self.post_preview()
        event = self.app.get('/trade/journal').get_json()['events'][0]
        response = self.app.post('/trade/journal/update', json={
            'id': event['id'],
            'journal_status': 'review',
            'journal_tags': 'scalp, momentum',
            'journal_setup': 'VWAP reclaim',
            'journal_thesis': 'Price reclaimed VWAP with calls holding bid.',
            'journal_notes': 'Preview looked clean; waited for confirmation.',
            'journal_outcome': 'Closed flat after momentum faded.',
        })
        self.assertEqual(response.status_code, 200)
        updated = response.get_json()['event']
        self.assertEqual(updated['journal_status'], 'review')
        self.assertEqual(updated['journal_tags'], 'scalp, momentum')
        self.assertEqual(updated['journal_setup'], 'VWAP reclaim')
        self.assertIn('calls holding bid', updated['journal_thesis'])
        self.assertIn('waited for confirmation', updated['journal_notes'])
        self.assertIn('Closed flat', updated['journal_outcome'])
        self.assertTrue(updated['updated_at'])

        journal = self.app.get('/trade/journal').get_json()
        self.assertEqual(journal['events'][0]['journal_status'], 'review')

    def test_journal_update_rejects_missing_event(self):
        response = self.app.post('/trade/journal/update', json={'id': 999, 'journal_notes': 'No row'})
        self.assertEqual(response.status_code, 404)

    def test_manual_journal_event_can_be_created(self):
        response = self.app.post('/trade/journal/create', json={
            'ticker': 'SPY',
            'contract_symbol': 'SPY   260501C00722000',
            'instruction': 'BUY_TO_OPEN',
            'quantity': 1,
            'limit_price': '0.57',
            'journal_status': 'review',
            'journal_tags': 'manual, watchlist',
            'journal_setup': 'Pullback setup',
            'journal_notes': 'Watching the same contract without previewing.',
        })
        self.assertEqual(response.status_code, 200)
        event = response.get_json()['event']
        self.assertEqual(event['event_type'], 'manual_note')
        self.assertEqual(event['ticker'], 'SPY')
        self.assertEqual(event['journal_status'], 'review')
        self.assertEqual(event['journal_tags'], 'manual, watchlist')
        self.assertEqual(event['details']['source'], 'manual_journal_entry')

        journal = self.app.get('/trade/journal').get_json()
        self.assertEqual(journal['events'][0]['id'], event['id'])


class TradePlaceOrderEndpointTest(unittest.TestCase):
    def setUp(self):
        seed_chain()
        ezoptionsschwab._trade_preview_records.clear()
        self.original_client = ezoptionsschwab.client
        self.original_db_path = ezoptionsschwab.DB_PATH
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db_path = tmp.name
        tmp.close()
        ezoptionsschwab.DB_PATH = self.temp_db_path
        ezoptionsschwab.init_db()
        ezoptionsschwab.client = MockPreviewClient()
        self.app = ezoptionsschwab.app.test_client()

    def tearDown(self):
        ezoptionsschwab.client = self.original_client
        ezoptionsschwab.DB_PATH = self.original_db_path
        try:
            os.unlink(self.temp_db_path)
        except OSError:
            pass
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
        events = self.app.get('/trade/journal').get_json()['events']
        self.assertEqual(events[0]['event_type'], 'placed_order')
        self.assertEqual(events[0]['location'], 'https://api.schwabapi.com/trader/v1/accounts/HASH123/orders/987654321')

    def test_successful_placement_consumes_preview_token(self):
        preview = self.post_preview().get_json()
        payload = self.place_payload_from_preview(preview)
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(payload)
            replay = self.post_place(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(replay.status_code, 400)
        self.assertIn('successful preview', replay.get_json()['error'].lower())
        self.assertEqual(ezoptionsschwab.client.place_calls, [('HASH123', preview['order'])])

    def test_sell_to_close_rechecks_position_before_live_placement(self):
        preview = self.post_preview(instruction='SELL_TO_CLOSE', quantity=3).get_json()
        ezoptionsschwab.client.positions_payload = {
            'securitiesAccount': {
                'positions': [
                    {
                        'longQuantity': 1,
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
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview))
        self.assertEqual(response.status_code, 400)
        self.assertIn('long position', response.get_json()['error'])
        self.assertEqual(ezoptionsschwab.client.place_calls, [])

    def test_place_response_does_not_expose_plain_account_numbers(self):
        preview = self.post_preview().get_json()
        with patch.dict(os.environ, {'ENABLE_LIVE_TRADING': '1'}):
            response = self.post_place(self.place_payload_from_preview(preview))
        body = response.get_data(as_text=True)
        self.assertNotIn('123456789', body)


class TradeOrderManagementEndpointTest(unittest.TestCase):
    def setUp(self):
        seed_chain()
        self.original_client = ezoptionsschwab.client
        self.original_db_path = ezoptionsschwab.DB_PATH
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db_path = tmp.name
        tmp.close()
        ezoptionsschwab.DB_PATH = self.temp_db_path
        ezoptionsschwab.init_db()
        ezoptionsschwab.client = MockPreviewClient()
        self.app = ezoptionsschwab.app.test_client()

    def tearDown(self):
        ezoptionsschwab.client = self.original_client
        ezoptionsschwab.DB_PATH = self.original_db_path
        try:
            os.unlink(self.temp_db_path)
        except OSError:
            pass
        ezoptionsschwab._options_cache.clear()

    def post_orders(self, **overrides):
        payload = {
            'account_hash': 'HASH123',
            'ticker': 'SPY',
            'contract_symbol': 'SPY   260501C00722000',
        }
        payload.update(overrides)
        return self.app.post('/trade/orders', json=payload)

    def test_orders_reject_missing_account(self):
        response = self.post_orders(account_hash='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Missing account hash', response.get_json()['error'])

    def test_orders_handle_unavailable_schwab_client(self):
        ezoptionsschwab.client = None
        response = self.post_orders()
        self.assertEqual(response.status_code, 503)
        self.assertIn('not initialized', response.get_json()['error'])

    def test_order_response_does_not_expose_plain_account_numbers(self):
        response = self.post_orders()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn('123456789', body)

    def test_selected_contract_filtering_returns_matching_orders(self):
        response = self.post_orders()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data['orders']), 1)
        self.assertEqual(data['orders'][0]['order_id'], '111')
        self.assertEqual(data['orders'][0]['legs'][0]['symbol'], 'SPY   260501C00722000')
        self.assertEqual(ezoptionsschwab.client.order_calls[0][0], 'HASH123')

    def test_selected_contract_filtering_excludes_other_same_underlying_orders(self):
        response = self.post_orders(contract_symbol='SPY   260501C00723000')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data['orders']), 1)
        self.assertEqual(data['orders'][0]['order_id'], '333')
        self.assertEqual(data['orders'][0]['legs'][0]['symbol'], 'SPY   260501C00723000')

    def test_linked_accounts_do_not_expose_plain_account_numbers_in_labels(self):
        response = self.app.get('/trade/accounts')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        data = response.get_json()
        self.assertNotIn('123456789', body)
        self.assertNotIn('987654321', body)
        self.assertEqual(data['accounts'][0]['display_label'], 'Account *6789')
        self.assertEqual(data['accounts'][1]['display_label'], 'IRA ****6789')

    def test_cancel_requires_explicit_confirmation(self):
        response = self.app.post('/trade/cancel_order', json={
            'account_hash': 'HASH123',
            'order_id': '111',
            'confirmed': False,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('confirmation', response.get_json()['error'].lower())
        self.assertEqual(ezoptionsschwab.client.cancel_calls, [])

    def test_cancel_uses_selected_account_hash_and_order_id_only(self):
        response = self.app.post('/trade/cancel_order', json={
            'account_hash': 'HASH123',
            'order_id': '111',
            'ticker': 'SPY',
            'contract_symbol': 'SPY   260501C00722000',
            'order': {'orderLegCollection': []},
            'confirmed': True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['cancelled'])
        self.assertEqual(ezoptionsschwab.client.cancel_calls, [('HASH123', '111')])

    def test_successful_cancel_records_local_journal_event(self):
        response = self.app.post('/trade/cancel_order', json={
            'account_hash': 'HASH123',
            'order_id': '111',
            'ticker': 'SPY',
            'contract_symbol': 'SPY   260501C00722000',
            'confirmed': True,
        })
        self.assertEqual(response.status_code, 200)
        events = self.app.get('/trade/journal').get_json()['events']
        self.assertEqual(events[0]['event_type'], 'cancelled_order')
        self.assertEqual(events[0]['ticker'], 'SPY')
        self.assertEqual(events[0]['contract_symbol'], 'SPY   260501C00722000')
        self.assertEqual(events[0]['journal_status'], 'review')
        self.assertEqual(events[0]['details']['order_id'], '111')


if __name__ == '__main__':
    unittest.main()
