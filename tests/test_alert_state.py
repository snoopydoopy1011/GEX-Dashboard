import importlib
import sys
import unittest
from unittest.mock import patch


with patch('schwabdev.Client', side_effect=RuntimeError('disabled in tests')):
    if 'ezoptionsschwab' in sys.modules:
        del sys.modules['ezoptionsschwab']
    ezoptionsschwab = importlib.import_module('ezoptionsschwab')


class AlertRegimeStateTest(unittest.TestCase):
    def setUp(self):
        ezoptionsschwab._LAST_REGIME_STATE.clear()

    def tearDown(self):
        ezoptionsschwab._LAST_REGIME_STATE.clear()

    def test_regime_change_alert_state_is_scoped_and_transition_only(self):
        self.assertFalse(ezoptionsschwab._regime_changed('spy', 'scope-a', 'Long Gamma'))
        self.assertFalse(ezoptionsschwab._regime_changed('SPY', 'scope-a', 'Long Gamma'))

        self.assertTrue(ezoptionsschwab._regime_changed('SPY', 'scope-a', 'Short Gamma'))
        self.assertFalse(ezoptionsschwab._regime_changed('SPY', 'scope-a', 'Short Gamma'))

        self.assertFalse(ezoptionsschwab._regime_changed('SPY', 'scope-b', 'Short Gamma'))
        self.assertTrue(ezoptionsschwab._regime_changed('SPY', 'scope-b', 'Long Gamma'))

    def test_regime_change_requires_ticker_and_regime(self):
        self.assertFalse(ezoptionsschwab._regime_changed(None, 'scope-a', 'Long Gamma'))
        self.assertFalse(ezoptionsschwab._regime_changed('SPY', 'scope-a', None))
        self.assertEqual(ezoptionsschwab._LAST_REGIME_STATE, {})

    def test_iv_surge_liquidity_gate_filters_thin_or_wide_contracts(self):
        self.assertTrue(ezoptionsschwab._iv_surge_liquidity_ok({
            'volume': 60,
            'bid': 0.46,
            'ask': 0.50,
            'mark': 0.48,
        }))
        self.assertFalse(ezoptionsschwab._iv_surge_liquidity_ok({
            'volume': 10,
            'bid': 0.46,
            'ask': 0.50,
            'mark': 0.48,
        }))
        self.assertFalse(ezoptionsschwab._iv_surge_liquidity_ok({
            'volume': 30,
            'bid': 0.05,
            'ask': 0.07,
            'mark': 0.06,
        }))
        self.assertFalse(ezoptionsschwab._iv_surge_liquidity_ok({
            'volume': 80,
            'bid': 0.20,
            'ask': 0.50,
            'mark': 0.35,
        }))


if __name__ == '__main__':
    unittest.main()
