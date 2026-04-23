import importlib
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import pytz


with patch('schwabdev.Client', side_effect=RuntimeError('disabled in tests')):
    if 'ezoptionsschwab' in sys.modules:
        del sys.modules['ezoptionsschwab']
    ezoptionsschwab = importlib.import_module('ezoptionsschwab')


ET = pytz.timezone('US/Eastern')


def make_candle(date_text, time_text, open_price, high_price, low_price, close_price):
    dt = ET.localize(datetime.strptime(f'{date_text} {time_text}', '%Y-%m-%d %H:%M'))
    return {
        'datetime': int(dt.timestamp() * 1000),
        'open': open_price,
        'high': high_price,
        'low': low_price,
        'close': close_price,
    }


def build_session_fixture(include_anchor_after_hours=False, include_future_session=False):
    candles = [
        make_candle('2026-04-22', '09:30', 100.0, 101.0, 99.0, 100.0),
        make_candle('2026-04-22', '10:00', 100.0, 111.0, 108.0, 110.0),
        make_candle('2026-04-22', '15:59', 108.0, 109.0, 106.0, 108.0),
        make_candle('2026-04-22', '16:00', 109.0, 114.0, 107.0, 112.0),
        make_candle('2026-04-22', '19:59', 111.0, 113.0, 105.0, 106.0),
        make_candle('2026-04-23', '08:30', 120.0, 121.0, 119.0, 120.0),
        make_candle('2026-04-23', '09:00', 120.0, 125.0, 118.0, 124.0),
        make_candle('2026-04-23', '09:25', 124.0, 124.5, 123.0, 124.0),
        make_candle('2026-04-23', '09:30', 126.0, 127.0, 125.0, 126.5),
        make_candle('2026-04-23', '09:35', 126.5, 129.0, 126.0, 128.0),
        make_candle('2026-04-23', '09:44', 128.0, 128.5, 124.0, 124.5),
        make_candle('2026-04-23', '10:00', 124.5, 131.0, 123.0, 130.0),
        make_candle('2026-04-23', '10:29', 130.0, 130.5, 122.0, 123.0),
        make_candle('2026-04-23', '11:15', 123.0, 133.0, 121.0, 132.0),
    ]
    if include_anchor_after_hours:
        candles.extend([
            make_candle('2026-04-23', '16:05', 134.0, 136.0, 134.0, 135.0),
            make_candle('2026-04-23', '19:30', 135.0, 137.0, 133.0, 134.0),
        ])
    if include_future_session:
        candles.extend([
            make_candle('2026-04-24', '08:30', 140.0, 141.0, 139.0, 140.0),
            make_candle('2026-04-24', '09:30', 141.0, 142.0, 140.0, 141.0),
        ])
    return candles


class ComputeSessionLevelsTest(unittest.TestCase):
    def test_rth_anchor_levels_and_previous_after_hours_fallback(self):
        levels = ezoptionsschwab.compute_session_levels(build_session_fixture(), config={
            'near_open_minutes': 60,
            'opening_range_minutes': 15,
            'ib_start': '09:30',
            'ib_end': '10:30',
        })

        self.assertEqual(levels['meta']['anchor_date'], '2026-04-23')
        self.assertEqual(levels['today_high']['price'], 133.0)
        self.assertEqual(levels['today_low']['price'], 121.0)
        self.assertEqual(levels['today_open']['price'], 126.0)
        self.assertEqual(levels['yesterday_high']['price'], 111.0)
        self.assertEqual(levels['yesterday_low']['price'], 99.0)
        self.assertEqual(levels['yesterday_open']['price'], 100.0)
        self.assertEqual(levels['yesterday_close']['price'], 108.0)
        self.assertEqual(levels['premarket_high']['price'], 125.0)
        self.assertEqual(levels['premarket_low']['price'], 118.0)
        self.assertEqual(levels['near_open_high']['price'], 125.0)
        self.assertEqual(levels['near_open_low']['price'], 118.0)
        self.assertEqual(levels['opening_range_high']['price'], 129.0)
        self.assertEqual(levels['opening_range_low']['price'], 124.0)
        self.assertEqual(levels['opening_range_mid']['price'], 126.5)
        self.assertEqual(levels['ib_high']['price'], 131.0)
        self.assertEqual(levels['ib_low']['price'], 122.0)
        self.assertEqual(levels['ib_mid']['price'], 126.5)
        self.assertEqual(levels['ib_high_x2']['price'], 140.0)
        self.assertEqual(levels['ib_low_x2']['price'], 113.0)
        self.assertEqual(levels['ib_high_x3']['price'], 149.0)
        self.assertEqual(levels['ib_low_x3']['price'], 104.0)
        self.assertEqual(levels['after_hours_high']['price'], 114.0)
        self.assertEqual(levels['after_hours_low']['price'], 105.0)

    def test_same_day_after_hours_wins_when_anchor_session_has_after_hours(self):
        levels = ezoptionsschwab.compute_session_levels(
            build_session_fixture(include_anchor_after_hours=True)
        )

        self.assertEqual(levels['after_hours_high']['price'], 137.0)
        self.assertEqual(levels['after_hours_low']['price'], 133.0)

    def test_explicit_anchor_date_uses_anchor_session_for_after_hours(self):
        levels = ezoptionsschwab.compute_session_levels(
            build_session_fixture(include_anchor_after_hours=True, include_future_session=True),
            anchor_date='2026-04-23',
        )

        self.assertEqual(levels['meta']['anchor_date'], '2026-04-23')
        self.assertEqual(levels['today_high']['price'], 133.0)
        self.assertEqual(levels['after_hours_high']['price'], 137.0)
        self.assertEqual(levels['after_hours_low']['price'], 133.0)


if __name__ == '__main__':
    unittest.main()
