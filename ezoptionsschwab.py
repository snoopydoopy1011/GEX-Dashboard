from flask import Flask, render_template_string, jsonify, request, Response, stream_with_context
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from bisect import bisect_left
from datetime import datetime, timedelta
import math
import time
import collections
import os

# python.org Python on macOS ships without a default CA bundle unless
# "Install Certificates.command" has been run, so ssl.create_default_context()
# returns an empty trust store and the Schwab streaming websocket fails with
# "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain".
# Pointing SSL_CERT_FILE at certifi's bundle makes the app self-healing.
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

import schwabdev
from dotenv import load_dotenv
import pytz
import sqlite3
from contextlib import closing
from scipy.stats import norm
import warnings
import json
import threading
import queue


# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'options_data.db')
MAX_RETAINED_SESSION_DATES = 2
_retention_lock = threading.Lock()


def sqlite_connect(db_path=DB_PATH, timeout=10, retries=2, retry_delay=0.25):
    last_error = None
    for attempt in range(retries + 1):
        try:
            return sqlite3.connect(db_path, timeout=timeout)
        except sqlite3.OperationalError as e:
            last_error = e
            if 'disk i/o error' not in str(e).lower() or attempt >= retries:
                raise
            time.sleep(retry_delay * (attempt + 1))
    raise last_error

# Global error handlers for Flask
@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith('/api/') or request.path.startswith('/update') or request.path.startswith('/expirations'):
        return jsonify({'error': 'API endpoint not found'}), 404
    return "404 - Not Found", 404

@app.errorhandler(500)
def internal_error(error):
    # Expose the error message for API-like endpoints so the frontend can show details
    msg = getattr(error, 'description', None) or str(error)
    if request.path.startswith('/api/') or request.path.startswith('/update') or request.path.startswith('/expirations'):
        return jsonify({'error': msg}), 500
    return "500 - Internal Server Error", 500

# Initialize SQLite database
def init_db():
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS interval_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    price REAL NOT NULL,
                    strike REAL NOT NULL,
                    net_gamma REAL NOT NULL,
                    net_delta REAL NOT NULL,
                    net_vanna REAL NOT NULL,
                    net_charm REAL,
                    net_volume REAL,
                    net_speed REAL,
                    net_vomma REAL,
                    net_color REAL,
                    abs_gex_total REAL,
                    date TEXT NOT NULL
                )
            ''')
            # Try to add net_charm column if it doesn't exist (for existing databases)
            try:
                cursor.execute('ALTER TABLE interval_data ADD COLUMN net_charm REAL')
            except sqlite3.OperationalError:
                pass 
            try:
                cursor.execute('ALTER TABLE interval_data ADD COLUMN net_volume REAL')
            except sqlite3.OperationalError:
                pass
            # Add abs_gex_total column if it's missing
            try:
                cursor.execute('ALTER TABLE interval_data ADD COLUMN abs_gex_total REAL')
            except sqlite3.OperationalError:
                pass 
            try:
                cursor.execute('ALTER TABLE interval_data ADD COLUMN net_speed REAL')
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute('ALTER TABLE interval_data ADD COLUMN net_vomma REAL')
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute('ALTER TABLE interval_data ADD COLUMN net_color REAL')
            except sqlite3.OperationalError:
                pass

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_interval_data_ticker_date_ts
                ON interval_data (ticker, date, timestamp, strike)
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS interval_session_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    price REAL NOT NULL,
                    expected_move REAL,
                    expected_move_upper REAL,
                    expected_move_lower REAL,
                    date TEXT NOT NULL
                )
            ''')
            
            # Add centroid data table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS centroid_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    price REAL NOT NULL,
                    call_centroid REAL NOT NULL,
                    put_centroid REAL NOT NULL,
                    call_volume INTEGER NOT NULL,
                    put_volume INTEGER NOT NULL,
                    date TEXT NOT NULL
                )
            ''')
            conn.commit()

def is_market_hours():
    """Return True if the current time is within regular market hours (9:30 AM - 4:00 PM ET, Mon-Fri)."""
    est = pytz.timezone('US/Eastern')
    now = datetime.now(est)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


INTERVAL_LEVEL_DISPLAY_NAMES = {
    'GEX': 'GEX',
    'AbsGEX': 'Abs GEX',
    'DEX': 'DEX',
    'VEX': 'Vanna',
    'Charm': 'Charm',
    'Volume': 'Volume',
    'Speed': 'Speed',
    'Vomma': 'Vomma',
    'Color': 'Color',
    'Expected Move': 'Expected Move',
}

INTERVAL_LEVEL_VALUE_KEYS = {
    'GEX': 'net_gamma',
    'AbsGEX': 'abs_gex_total',
    'DEX': 'net_delta',
    'VEX': 'net_vanna',
    'Charm': 'net_charm',
    'Volume': 'net_volume',
    'Speed': 'net_speed',
    'Vomma': 'net_vomma',
    'Color': 'net_color',
}


def normalize_level_type(level_type):
    if level_type in ('Vanna', 'VEX'):
        return 'VEX'
    return level_type


def _normalize_expiry_list(values):
    if not values:
        return []
    normalized = []
    for value in values:
        expiry_iso = _normalize_expiry_iso(value)
        if expiry_iso:
            normalized.append(expiry_iso)
    return sorted(set(normalized))


def _build_stats_scope_id(strike_range, selected_expiries=None, scope_label='all'):
    normalized_expiries = _normalize_expiry_list(selected_expiries)
    expiries_token = ','.join(normalized_expiries) if normalized_expiries else '*'
    try:
        range_token = f"{float(strike_range):.6f}"
    except Exception:
        range_token = 'nan'
    return f"{scope_label}|range:{range_token}|exp:{expiries_token}"


def _select_expected_move_contracts(calls, puts, spot_price, selected_expiries=None):
    """Pick one deterministic ATM straddle from a shared expiry and strike."""
    if calls is None or puts is None or calls.empty or puts.empty or not spot_price:
        return None

    call_scope = calls.copy()
    put_scope = puts.copy()
    call_scope['_expiry_iso'] = _expiration_series_iso(call_scope)
    put_scope['_expiry_iso'] = _expiration_series_iso(put_scope)

    selected_expiry_set = _normalize_expiry_list(selected_expiries)
    call_expiries = {expiry for expiry in call_scope['_expiry_iso'].dropna().tolist() if expiry}
    put_expiries = {expiry for expiry in put_scope['_expiry_iso'].dropna().tolist() if expiry}
    shared_expiries = sorted(call_expiries & put_expiries)

    if selected_expiry_set:
        expiry_candidates = [expiry for expiry in selected_expiry_set if expiry in shared_expiries]
    else:
        expiry_candidates = list(shared_expiries)

    chosen_expiry = None
    if expiry_candidates:
        today_iso = datetime.now(pytz.timezone('US/Eastern')).date().isoformat()
        future_candidates = [expiry for expiry in expiry_candidates if expiry >= today_iso]
        chosen_expiry = min(future_candidates) if future_candidates else min(expiry_candidates)
        call_scope = call_scope[call_scope['_expiry_iso'] == chosen_expiry]
        put_scope = put_scope[put_scope['_expiry_iso'] == chosen_expiry]

    call_strikes = set(pd.to_numeric(call_scope['strike'], errors='coerce').dropna().tolist())
    put_strikes = set(pd.to_numeric(put_scope['strike'], errors='coerce').dropna().tolist())
    common_strikes = sorted(call_strikes & put_strikes)
    if not common_strikes:
        return None

    def _get_mid(df, strike):
        strike_series = pd.to_numeric(df['strike'], errors='coerce')
        row = df.loc[strike_series == strike]
        if row is None or row.empty:
            return None
        bid = row['bid'].values[0]
        ask = row['ask'].values[0]
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        if bid > 0:
            return bid
        if ask > 0:
            return ask
        return None

    atm_strike = min(common_strikes, key=lambda strike: (abs(strike - spot_price), strike))
    call_mid = _get_mid(call_scope, atm_strike)
    put_mid = _get_mid(put_scope, atm_strike)
    if call_mid is None and put_mid is None:
        return None

    return {
        'expiry': chosen_expiry,
        'atm_strike': float(atm_strike),
        'call_mid': float(call_mid) if call_mid is not None else None,
        'put_mid': float(put_mid) if put_mid is not None else None,
    }


def calculate_expected_move_snapshot(calls, puts, spot_price, selected_expiries=None):
    """Return the current deterministic ATM straddle-based expected move snapshot."""
    contract = _select_expected_move_contracts(
        calls, puts, spot_price, selected_expiries=selected_expiries
    )
    if not contract:
        return None

    call_mid = contract['call_mid']
    put_mid = contract['put_mid']
    expected_move = (call_mid or 0) + (put_mid or 0)
    if expected_move <= 0:
        return None

    return {
        'expiry': contract['expiry'],
        'atm_strike': contract['atm_strike'],
        'move': float(expected_move),
        'upper': float(spot_price + expected_move),
        'lower': float(spot_price - expected_move),
    }

# Function to store centroid data
def store_centroid_data(ticker, price, calls, puts):
    """Store call and put centroid data for 5-minute intervals during market hours only"""
    # Get current time in Eastern Time
    est = pytz.timezone('US/Eastern')
    current_time_est = datetime.now(est)
    
    # Check if we're in market hours (9:30 AM - 4:00 PM ET, Monday-Friday)
    if current_time_est.weekday() >= 5:  # Weekend
        return
    
    market_open = current_time_est.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = current_time_est.replace(hour=16, minute=0, second=0, microsecond=0)
    
    if not (market_open <= current_time_est <= market_close):
        return  # Outside market hours
    
    current_time = int(current_time_est.timestamp())
    current_date = current_time_est.strftime('%Y-%m-%d')

    # Keep the DB bounded to the two most recent session dates.
    clear_old_data()
    
    # Round to nearest 5-minute interval (300 seconds)
    interval_timestamp = (current_time // 300) * 300
    
    # Delete existing data for this 5-minute interval to update with most recent data
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                DELETE FROM centroid_data 
                WHERE ticker = ? AND timestamp = ? AND date = ?
            ''', (ticker, interval_timestamp, current_date))
            conn.commit()
    
    # Calculate centroids (volume-weighted average strike prices)
    call_centroid = 0
    put_centroid = 0
    call_volume = 0
    put_volume = 0
    
    if not calls.empty:
        # Filter out zero volume options
        calls_with_volume = calls[calls['volume'] > 0]
        if not calls_with_volume.empty:
            call_volume = int(calls_with_volume['volume'].sum())
            # Calculate weighted average strike price
            weighted_strikes = calls_with_volume['strike'] * calls_with_volume['volume']
            call_centroid = weighted_strikes.sum() / call_volume
    
    if not puts.empty:
        # Filter out zero volume options
        puts_with_volume = puts[puts['volume'] > 0]
        if not puts_with_volume.empty:
            put_volume = int(puts_with_volume['volume'].sum())
            # Calculate weighted average strike price
            weighted_strikes = puts_with_volume['strike'] * puts_with_volume['volume']
            put_centroid = weighted_strikes.sum() / put_volume
    
    # Only store if we have volume data
    if call_volume > 0 or put_volume > 0:
        with closing(sqlite_connect()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO centroid_data (ticker, timestamp, price, call_centroid, put_centroid, call_volume, put_volume, date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, interval_timestamp, price, call_centroid, put_centroid, call_volume, put_volume, current_date))
                conn.commit()

# Function to get centroid data
def get_centroid_data(ticker, date=None):
    """Get centroid data for current trading session only (market hours)"""
    if date is None:
        # Get current date in Eastern Time
        est = pytz.timezone('US/Eastern')
        current_date_est = datetime.now(est).strftime('%Y-%m-%d')
        date = current_date_est
    
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                SELECT timestamp, price, call_centroid, put_centroid, call_volume, put_volume
                FROM centroid_data
                WHERE ticker = ? AND date = ?
                ORDER BY timestamp
            ''', (ticker, date))
            
            # Filter data to only include market hours (9:30 AM - 4:00 PM ET)
            all_data = cursor.fetchall()
            filtered_data = []
            
            for row in all_data:
                timestamp = row[0]
                # Convert timestamp to Eastern Time
                dt_est = datetime.fromtimestamp(timestamp, pytz.timezone('US/Eastern'))
                
                # Check if within market hours
                market_open = dt_est.replace(hour=9, minute=30, second=0, microsecond=0)
                market_close = dt_est.replace(hour=16, minute=0, second=0, microsecond=0)
                
                if market_open <= dt_est <= market_close and dt_est.weekday() < 5:
                    filtered_data.append(row)
            
            return filtered_data

def _load_centroid_session_rows(ticker):
    """Return centroid rows for today or the most recent session."""
    est = pytz.timezone('US/Eastern')
    current_time_est = datetime.now(est)
    showing_last_session = False
    centroid_data = get_centroid_data(ticker)

    if not centroid_data:
        last_date = get_last_session_date(ticker, 'centroid_data')
        if last_date:
            centroid_data = get_centroid_data(ticker, last_date)
            showing_last_session = True

    return centroid_data, showing_last_session, current_time_est

def build_centroid_panel_payload(ticker, limit=24):
    """Compact centroid payload for the right-rail sparkline card."""
    centroid_data, showing_last_session, current_time_est = _load_centroid_session_rows(ticker)

    if not centroid_data:
        if current_time_est.weekday() >= 5:
            status = 'Market closed for the weekend.'
        elif current_time_est.hour < 9 or (current_time_est.hour == 9 and current_time_est.minute < 30):
            status = 'Centroid data starts at the open.'
        elif current_time_est.hour >= 16:
            status = 'Session closed. Waiting for the next open.'
        else:
            status = 'Centroid data will appear after the next refresh.'
        return {
            'points': [],
            'showing_last_session': False,
            'status': status,
        }

    rows = centroid_data[-max(8, int(limit or 24)):]
    points = []
    first_price = None
    first_call = None
    first_put = None
    first_time = None
    latest_price = None
    latest_call = None
    latest_put = None
    latest_time = None

    for timestamp, price, call_centroid, put_centroid, call_volume, put_volume in rows:
        dt_est = datetime.fromtimestamp(timestamp, pytz.timezone('US/Eastern'))
        time_label = dt_est.strftime('%H:%M')
        price_value = float(price) if price is not None else None
        call_value = float(call_centroid) if call_centroid and call_centroid > 0 else None
        put_value = float(put_centroid) if put_centroid and put_centroid > 0 else None
        latest_time = time_label
        latest_price = price_value if price_value is not None else latest_price
        if call_value is not None:
            latest_call = call_value
        if put_value is not None:
            latest_put = put_value
        if first_time is None:
            first_time = time_label
        if first_price is None and price_value is not None:
            first_price = price_value
        if first_call is None and call_value is not None:
            first_call = call_value
        if first_put is None and put_value is not None:
            first_put = put_value
        points.append({
            'time': time_label,
            'price': price_value,
            'call': call_value,
            'put': put_value,
            'call_volume': int(call_volume or 0),
            'put_volume': int(put_volume or 0),
        })

    first_spread = (first_call - first_put) if first_call is not None and first_put is not None else None
    spread = (latest_call - latest_put) if latest_call is not None and latest_put is not None else None
    call_vs_price = (latest_call - latest_price) if latest_call is not None and latest_price is not None else None
    put_vs_price = (latest_put - latest_price) if latest_put is not None and latest_price is not None else None
    call_drift = (latest_call - first_call) if latest_call is not None and first_call is not None else None
    put_drift = (latest_put - first_put) if latest_put is not None and first_put is not None else None
    price_drift = (latest_price - first_price) if latest_price is not None and first_price is not None else None
    spread_drift = (spread - first_spread) if spread is not None and first_spread is not None else None

    return {
        'points': points,
        'showing_last_session': showing_last_session,
        'status': 'Last session' if showing_last_session else 'Current session',
        'first_time': first_time,
        'first_price': first_price,
        'first_call': first_call,
        'first_put': first_put,
        'latest_time': latest_time,
        'latest_price': latest_price,
        'latest_call': latest_call,
        'latest_put': latest_put,
        'spread': spread,
        'call_drift': call_drift,
        'put_drift': put_drift,
        'price_drift': price_drift,
        'spread_drift': spread_drift,
        'call_vs_price': call_vs_price,
        'put_vs_price': put_vs_price,
    }

# Function to store interval data
def store_interval_data(ticker, price, strike_range, calls, puts):
    if not is_market_hours():
        return
    est = pytz.timezone('US/Eastern')
    current_time_est = datetime.now(est)
    current_time = int(current_time_est.timestamp())
    current_date = current_time_est.strftime('%Y-%m-%d')

    # Keep the DB bounded to the two most recent session dates.
    clear_old_data()
    
    # Store interval overlays at 1-minute resolution so they can be aggregated
    # to whatever candle timeframe the chart is using.
    interval_timestamp = (current_time // 60) * 60
    
    # Delete existing data for this 1-minute interval to update with most recent data
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                DELETE FROM interval_data 
                WHERE ticker = ? AND timestamp = ? AND date = ?
            ''', (ticker, interval_timestamp, current_date))
            cursor.execute('''
                DELETE FROM interval_session_data
                WHERE ticker = ? AND timestamp = ? AND date = ?
            ''', (ticker, interval_timestamp, current_date))
            conn.commit()
    
    # Calculate strike range boundaries
    min_strike = price * (1 - strike_range)
    max_strike = price * (1 + strike_range)
    
    # Filter options within strike range
    range_calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)]
    range_puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)]
    
    # Calculate per-strike exposures used by the historical intraday overlays.
    exposure_by_strike = {}
    for _, row in range_calls.iterrows():
        strike = row['strike']
        gamma = row['GEX']
        delta = row['DEX']
        vanna = row['VEX']
        charm = row['Charm']
        speed = row['Speed']
        vomma = row['Vomma']
        color = row['Color']
        cur = exposure_by_strike.get(strike, {
            'gamma': 0,
            'delta': 0,
            'vanna': 0,
            'charm': 0,
            'volume': 0,
            'speed': 0,
            'vomma': 0,
            'color': 0,
            'call_gamma': 0,
            'put_gamma': 0,
        })
        cur['gamma'] = cur.get('gamma',0) + gamma
        cur['delta'] = cur.get('delta',0) + delta
        cur['vanna'] = cur.get('vanna',0) + vanna
        cur['charm'] = cur.get('charm',0) + charm
        cur['volume'] = cur.get('volume',0) + row['volume']
        cur['speed'] = cur.get('speed',0) + speed
        cur['vomma'] = cur.get('vomma',0) + vomma
        cur['color'] = cur.get('color',0) + color
        cur['call_gamma'] = cur.get('call_gamma',0) + gamma
        exposure_by_strike[strike] = cur
        
    for _, row in range_puts.iterrows():
        strike = row['strike']
        gamma = row['GEX']
        delta = row['DEX']
        vanna = row['VEX']
        charm = row['Charm']
        speed = row['Speed']
        vomma = row['Vomma']
        color = row['Color']
        cur = exposure_by_strike.get(strike, {
            'gamma': 0,
            'delta': 0,
            'vanna': 0,
            'charm': 0,
            'volume': 0,
            'speed': 0,
            'vomma': 0,
            'color': 0,
            'call_gamma': 0,
            'put_gamma': 0,
        })
        cur['gamma'] = cur.get('gamma',0) - gamma
        cur['delta'] = cur.get('delta',0) + delta
        cur['vanna'] = cur.get('vanna',0) + vanna
        cur['charm'] = cur.get('charm',0) + charm
        cur['volume'] = cur.get('volume',0) - row['volume']
        cur['speed'] = cur.get('speed',0) + speed
        cur['vomma'] = cur.get('vomma',0) + vomma
        cur['color'] = cur.get('color',0) + color
        cur['put_gamma'] = cur.get('put_gamma',0) + gamma
        exposure_by_strike[strike] = cur

    expected_move_snapshot = calculate_expected_move_snapshot(calls, puts, price)
    
    # Store data for each strike
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            for strike, exposure in exposure_by_strike.items():
                abs_gex_total = abs(exposure.get('call_gamma',0)) + abs(exposure.get('put_gamma',0))
                cursor.execute('''
                    INSERT INTO interval_data (
                        ticker, timestamp, price, strike, net_gamma, net_delta, net_vanna,
                        net_charm, net_volume, net_speed, net_vomma, net_color, abs_gex_total, date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker,
                    interval_timestamp,
                    price,
                    strike,
                    exposure['gamma'],
                    exposure['delta'],
                    exposure['vanna'],
                    exposure['charm'],
                    exposure['volume'],
                    exposure['speed'],
                    exposure['vomma'],
                    exposure['color'],
                    abs_gex_total,
                    current_date,
                ))

            if expected_move_snapshot:
                cursor.execute('''
                    INSERT INTO interval_session_data (
                        ticker, timestamp, price, expected_move, expected_move_upper, expected_move_lower, date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker,
                    interval_timestamp,
                    price,
                    expected_move_snapshot['move'],
                    expected_move_snapshot['upper'],
                    expected_move_snapshot['lower'],
                    current_date,
                ))
            conn.commit()

# Function to get interval data
def get_interval_data(ticker, date=None):
    if date is None:
        date = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
    
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                  SELECT timestamp, price, strike, net_gamma, net_delta, net_vanna, net_charm,
                      abs_gex_total, net_volume, net_speed, net_vomma, net_color
                FROM interval_data
                WHERE ticker = ? AND date = ?
                ORDER BY timestamp, strike
            ''', (ticker, date))
            all_data = cursor.fetchall()

    est = pytz.timezone('US/Eastern')
    filtered = []
    for row in all_data:
        dt = datetime.fromtimestamp(row[0], est)
        market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = dt.replace(hour=16, minute=0, second=0, microsecond=0)
        if dt.weekday() < 5 and market_open <= dt <= market_close:
            filtered.append(row)
    return filtered


def get_interval_session_data(ticker, date=None):
    if date is None:
        date = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')

    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                SELECT timestamp, price, expected_move, expected_move_upper, expected_move_lower
                FROM interval_session_data
                WHERE ticker = ? AND date = ?
                ORDER BY timestamp
            ''', (ticker, date))
            all_data = cursor.fetchall()

    est = pytz.timezone('US/Eastern')
    filtered = []
    for row in all_data:
        dt = datetime.fromtimestamp(row[0], est)
        market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = dt.replace(hour=16, minute=0, second=0, microsecond=0)
        if dt.weekday() < 5 and market_open <= dt <= market_close:
            filtered.append(row)
    return filtered

def get_last_session_date(ticker, table='interval_data'):
    """Return the most recent date that has data for ticker, or None."""
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute(f'SELECT MAX(date) FROM {table} WHERE ticker = ?', (ticker,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

# Function to clear old data
def clear_old_data():
    """Keep only the most recent session dates in each SQLite history table."""
    tables = ('interval_data', 'centroid_data', 'interval_session_data')
    deleted_rows = {}

    with _retention_lock:
        with closing(sqlite_connect()) as conn:
            with closing(conn.cursor()) as cursor:
                for table_name in tables:
                    cursor.execute(f'''
                        SELECT DISTINCT date
                        FROM {table_name}
                        WHERE date IS NOT NULL
                        ORDER BY date DESC
                    ''')
                    all_dates = [row[0] for row in cursor.fetchall() if row[0]]
                    stale_dates = all_dates[MAX_RETAINED_SESSION_DATES:]
                    if not stale_dates:
                        continue

                    placeholders = ','.join('?' for _ in stale_dates)
                    cursor.execute(
                        f'DELETE FROM {table_name} WHERE date IN ({placeholders})',
                        stale_dates,
                    )
                    deleted_rows[table_name] = cursor.rowcount

                conn.commit()

    if deleted_rows:
        print(
            'Pruned database history to the latest '
            f'{MAX_RETAINED_SESSION_DATES} session dates: {deleted_rows}'
        )

# Function to clear centroid data for new session
def clear_centroid_session_data(ticker):
    """Clear centroid data at the start of a new trading session"""
    est = pytz.timezone('US/Eastern')
    today = datetime.now(est).strftime('%Y-%m-%d')
    
    with closing(sqlite_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                DELETE FROM centroid_data
                WHERE ticker = ? AND date = ?
            ''', (ticker, today))
            conn.commit()
            print(f"Cleared centroid data for new session: {ticker} on {today}")

# Initialize database
init_db()

# Prune retained history on startup as well as on active writes.
clear_old_data()

# Clear old data at the start of the day
est = pytz.timezone('US/Eastern')
current_time_est = datetime.now(est)

# Clear centroid data at market open (9:30 AM ET) for a fresh session
if current_time_est.hour == 9 and current_time_est.minute == 30 and current_time_est.weekday() < 5:
    # Note: This will clear centroid data for all tickers at market open
    # Individual ticker clearing happens in the update route when first accessed
    pass

# Global variables for streaming
current_chain = {'calls': [], 'puts': []}
last_update_time = 0
UPDATE_INTERVAL = 1  # seconds
current_ticker = None
current_expiry = None

# Cache for last fetched options data per ticker — used by /update_price
# so the price chart can refresh independently without re-fetching the full chain.
_options_cache = {}  # ticker -> {'calls': DataFrame, 'puts': DataFrame, 'S': float}

# Initialize Schwab client
try:
    client = schwabdev.Client(
        os.getenv('SCHWAB_APP_KEY'),
        os.getenv('SCHWAB_APP_SECRET'),
        os.getenv('SCHWAB_CALLBACK_URL')
    )
except Exception as e:
    print(f"Error initializing Schwab client: {e}")
    client = None

# ── Real-time Price Streamer ─────────────────────────────────────────────────
class PriceStreamer:
    """Manages a single schwabdev streaming websocket for real-time price data.
    Feeds per-ticker queues that are consumed by the /price_stream SSE endpoint.
    """
    def __init__(self):
        self._stream = None
        self._lock = threading.Lock()
        self._queues = {}       # ticker (upper) -> list[queue.Queue]
        self._subscribed = set()  # tickers with active stream subscriptions
        self._started = False

    def _handler(self, message):
        """Parse raw schwabdev stream message and push candle/quote data to queues."""
        try:
            data = json.loads(message) if isinstance(message, str) else message
            # Schwab wraps every data message in {"data": [...]}; unwrap it so
            # we can iterate over individual service messages directly.
            if isinstance(data, dict):
                if 'data' in data:
                    data = data['data']
                else:
                    return  # login/response/notify envelope – nothing to forward
            if not isinstance(data, list):
                data = [data]
            for msg in data:
                service = msg.get('service', '')
                if service == 'CHART_EQUITY':
                    for item in msg.get('content', []):
                        ticker = item.get('key', '').upper()
                        chart_time_ms = item.get('7')
                        if not ticker or chart_time_ms is None:
                            continue
                        payload = json.dumps({
                            'type': 'candle',
                            'time': int(chart_time_ms) // 1000,
                            'open':   item.get('1'),
                            'high':   item.get('2'),
                            'low':    item.get('3'),
                            'close':  item.get('4'),
                            'volume': item.get('5'),
                        })
                        self._push(ticker, payload)
                elif service == 'CHART_FUTURES':
                    for item in msg.get('content', []):
                        ticker = item.get('key', '').upper()
                        chart_time_ms = item.get('3')
                        if not ticker or chart_time_ms is None:
                            continue
                        payload = json.dumps({
                            'type': 'candle',
                            'time': int(chart_time_ms) // 1000,
                            'open':   item.get('4'),
                            'high':   item.get('5'),
                            'low':    item.get('6'),
                            'close':  item.get('7'),
                            'volume': item.get('8'),
                        })
                        self._push(ticker, payload)
                elif service == 'LEVELONE_EQUITIES':
                    for item in msg.get('content', []):
                        ticker = item.get('key', '').upper()
                        last = item.get('3')  # field 3 = last price
                        if not ticker or last is None:
                            continue
                        payload = json.dumps({'type': 'quote', 'last': float(last)})
                        self._push(ticker, payload)
                elif service == 'LEVELONE_FUTURES':
                    for item in msg.get('content', []):
                        ticker = item.get('key', '').upper()
                        last = item.get('3')  # field 3 = last price
                        if not ticker or last is None:
                            continue
                        payload = json.dumps({'type': 'quote', 'last': float(last)})
                        self._push(ticker, payload)
        except Exception as e:
            print(f"[PriceStreamer] handler error: {e}")

    def _push(self, ticker, payload):
        with self._lock:
            qs = list(self._queues.get(ticker, []))
        for q in qs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass  # drop stale data rather than block

    def _ensure_started(self):
        with self._lock:
            if self._started or client is None:
                return
            try:
                self._stream = schwabdev.Stream(client)
                self._stream.start(self._handler)
                self._started = True
                print("[PriceStreamer] Stream started.")
            except Exception as e:
                print(f"[PriceStreamer] Failed to start stream: {e}")

    def subscribe(self, ticker, q):
        """Register a client SSE queue and ensure ticker is subscribed on the stream."""
        self._ensure_started()
        needs_sub = False
        with self._lock:
            if ticker not in self._queues:
                self._queues[ticker] = []
            self._queues[ticker].append(q)
            if ticker not in self._subscribed:
                self._subscribed.add(ticker)
                needs_sub = True
        if needs_sub and self._started and self._stream:
            try:
                is_future = ticker.startswith('/')
                if is_future:
                    self._stream.send(self._stream.chart_futures(ticker, "0,1,2,3,4,5,6,7,8"))
                    self._stream.send(self._stream.level_one_futures(ticker, "0,1,2,3"))
                else:
                    self._stream.send(self._stream.chart_equity(ticker, "0,1,2,3,4,5,6,7,8"))
                    self._stream.send(self._stream.level_one_equities(ticker, "0,1,2,3"))
                print(f"[PriceStreamer] Subscribed to {ticker}")
            except Exception as e:
                print(f"[PriceStreamer] Subscribe error for {ticker}: {e}")

    def unsubscribe_queue(self, ticker, q):
        """Remove a specific client queue (called on SSE disconnect)."""
        with self._lock:
            if ticker in self._queues:
                try:
                    self._queues[ticker].remove(q)
                except ValueError:
                    pass

    def stop(self):
        with self._lock:
            if self._stream and self._started:
                try:
                    self._stream.stop()
                except Exception:
                    pass
                self._stream = None
                self._started = False


price_streamer = PriceStreamer()

# Helper Functions
def format_ticker(ticker):
    if not ticker:
        return ""
    ticker = ticker.upper()
    if ticker.startswith('/'):
        return ticker
    elif ticker in ['SPX', '$SPX']:
        return '$SPX'  # Return $SPX for API calls
    elif ticker in ['NDX', '$NDX']:
        return '$NDX'  # Return $NDX for API calls
    elif ticker in ['VIX', '$VIX']:
        return '$VIX'  # Return $VIX for API calls
    return ticker

def format_display_ticker(ticker):
    """Helper function to format tickers for display and data filtering"""
    if not ticker:
        return []
    ticker = ticker.upper()
    if ticker.startswith('/'):
        return [ticker]
    elif ticker in ['$SPX', 'SPX']:
        # For SPX, return SPXW for options symbols and $SPX for underlying
        return ['SPXW', '$SPX']
    elif ticker in ['$NDX', 'NDX']:
        # For NDX, return NDXP for options symbols and $NDX for underlying
        return ['NDXP', '$NDX']
    elif ticker in ['$VIX', 'VIX']:
        # For VIX, return VIX for options symbols and $VIX for underlying
        return ['VIX', '$VIX']
    elif ticker == 'MARKET2':
        return ['SPY']
    return [ticker]

def format_large_number(num):
    """Format large numbers with suffixes (K, M, B, T)"""
    if num is None:
        return "0"
    
    abs_num = abs(num)
    if abs_num >= 1e12:
        return f"{num/1e12:.2f}T"
    elif abs_num >= 1e9:
        return f"{num/1e9:.2f}B"
    elif abs_num >= 1e6:
        return f"{num/1e6:.2f}M"
    elif abs_num >= 1e3:
        return f"{num/1e3:.2f}K"
    else:
        return f"{num:,.0f}"

def get_strike_interval(strikes):
    """Determine the most common strike interval from a list of strikes"""
    if len(strikes) < 2:
        return 1.0
    
    sorted_strikes = sorted(set(strikes))
    intervals = []
    for i in range(1, len(sorted_strikes)):
        diff = sorted_strikes[i] - sorted_strikes[i-1]
        if diff > 0:
            intervals.append(diff)
    
    if not intervals:
        return 1.0
    
    # Return the most common interval
    from collections import Counter
    interval_counts = Counter([round(i, 2) for i in intervals])
    return interval_counts.most_common(1)[0][0]

def round_to_strike(value, strike_interval):
    """Round a value to the nearest strike interval"""
    return round(value / strike_interval) * strike_interval

def aggregate_by_strike(df, value_columns, strike_interval):
    """Aggregate dataframe by rounded strike prices"""
    if df.empty:
        return df
    
    df = df.copy()
    df['rounded_strike'] = df['strike'].apply(lambda x: round_to_strike(x, strike_interval))
    
    # Build aggregation dict for value columns
    agg_dict = {}
    for col in value_columns:
        if col in df.columns:
            agg_dict[col] = 'sum'
    
    if not agg_dict:
        return df
    
    # Group by rounded strike and aggregate
    aggregated = df.groupby('rounded_strike', as_index=False).agg(agg_dict)
    aggregated = aggregated.rename(columns={'rounded_strike': 'strike'})
    
    return aggregated

def calculate_time_to_expiration(expiry_date):
    """
    Calculate time to expiration in years using Eastern Time.
    expiry_date: datetime.date object or string 'YYYY-MM-DD'
    Returns: time in years (float)
    """
    try:
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        
        if isinstance(expiry_date, str):
            expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        elif isinstance(expiry_date, datetime):
            expiry_date = expiry_date.date()
            
        # Set expiration to 4:00 PM ET on the expiration date
        expiry_dt = datetime.combine(expiry_date, datetime.min.time()) + timedelta(hours=16)
        expiry_dt = et_tz.localize(expiry_dt)
        
        # Calculate time difference in years
        diff = expiry_dt - now_et
        t = diff.total_seconds() / (365 * 24 * 3600)
        
        return t
             
    except Exception as e:
        print(f"Error calculating time to expiration: {e}")
        return 0

def fetch_options_for_date(ticker, date, exposure_metric="Open Interest", delta_adjusted: bool = False, calculate_in_notional: bool = True, S=None):
    if client is None:
        raise Exception("Schwab API client not initialized. Check your environment variables.")
    
    if ticker == "MARKET" or ticker == "MARKET2":
        # Step 1: Initialize Base
        base_ticker = "$SPX" if ticker == "MARKET" else "SPY"
        base_price = S if S else get_current_price(base_ticker)
        
        if not base_price:
             return pd.DataFrame(), pd.DataFrame()

        # Fetch Base chain to build strike grid
        base_calls_raw, base_puts_raw = fetch_options_for_date(base_ticker, date, exposure_metric, delta_adjusted, calculate_in_notional)
        
        if base_calls_raw.empty and base_puts_raw.empty:
            return pd.DataFrame(), pd.DataFrame()

        # Step 2: Components to combine
        # Calculate bucket size from the base chain's actual strike spacing
        # (e.g. SPX → typically $5, SPY → $1). Avoids hardcoding.
        base_all_strikes = []
        if not base_calls_raw.empty: base_all_strikes.extend(base_calls_raw['strike'].tolist())
        if not base_puts_raw.empty: base_all_strikes.extend(base_puts_raw['strike'].tolist())
        bucket_size = get_strike_interval(base_all_strikes) if base_all_strikes else 5.0

        if ticker == "MARKET":
            component_tickers = ["$SPX", "$NDX", "QQQ", "SPY"]
        else:
            component_tickers = ["SPY"]
        
        calls_list = []
        puts_list = []

        # Columns that get per-Greek normalization
        exposure_cols = ['GEX', 'DEX', 'VEX', 'Charm', 'Speed', 'Vomma', 'Color']
        activity_cols = ['openInterest', 'volume']

        # First pass: collect data and compute per-Greek total absolute exposure
        # for each component.  This lets us normalize each Greek independently
        # so that e.g. 5 000 OI on IWM is proportionally as loud as 500 000 on SPX.
        component_data = []
        for comp_tick in component_tickers:
            if comp_tick == base_ticker:
                c, p = base_calls_raw.copy(), base_puts_raw.copy()
                comp_price = base_price
            else:
                comp_price = get_current_price(comp_tick)
                if not comp_price: continue
                c, p = fetch_options_for_date(comp_tick, date, exposure_metric, delta_adjusted, calculate_in_notional)
                c, p = c.copy() if not c.empty else c, p.copy() if not p.empty else p
            
            if c.empty and p.empty: continue
            
            # Use total open interest as the stable sizing anchor.
            # OI only updates overnight, so normalization factors stay constant
            # between live updates — preventing Greek exposure jumps caused by
            # the per-Greek totals (GEX, DEX…) swinging with every price tick.
            comp_oi = 0
            if not c.empty and 'openInterest' in c.columns:
                comp_oi += c['openInterest'].sum()
            if not p.empty and 'openInterest' in p.columns:
                comp_oi += p['openInterest'].sum()
            comp_oi = max(comp_oi, 1)  # avoid /0
            # Same anchor value for every column so the ratio base_oi/comp_oi
            # is applied uniformly across all Greeks and activity columns.
            totals = {col: comp_oi for col in exposure_cols + activity_cols}

            component_data.append({
                'ticker': comp_tick,
                'price': comp_price,
                'calls': c,
                'puts': p,
                'totals': totals          # dict keyed by column name
            })
        
        if not component_data:
            return pd.DataFrame(), pd.DataFrame()
        
        # OI-based reference anchor: base_oi / comp_oi is the single scale
        # factor applied to all columns for every non-base component.
        # Because OI only changes overnight, this ratio stays constant between
        # live price-update cycles — eliminating the intraday Greek-jump problem
        # that arose when per-Greek totals (GEX ∝ S², DEX ∝ S) swung with price.
        base_cd = next((cd for cd in component_data if cd['ticker'] == base_ticker), component_data[0])
        base_totals = base_cd['totals']  # {col: base_oi} for all cols

        # Second pass: OI-anchored normalization, then moneyness strike mapping.
        # Base component (SPX) is untouched (factor = 1.0).
        # Non-base: scale so their total OI matches base OI, then apply to Greeks.
        for cd in component_data:
            comp_tick = cd['ticker']
            comp_price = cd['price']
            c = cd['calls']
            p = cd['puts']
            totals = cd['totals']
            
            is_base = (comp_tick == base_ticker)

            # Build per-column norm factors anchored to base component.
            # Base component: factor = 1.0 (unchanged).
            # Non-base: factor = base_total / component_total (scale up to match SPX magnitude).
            col_norm = {}
            for col in exposure_cols + activity_cols:
                if is_base:
                    col_norm[col] = 1.0
                else:
                    col_norm[col] = base_totals[col] / totals[col]
            
            # Process Calls
            if not c.empty:
                c = c.copy()
                
                # Normalize each column independently (Greeks + OI/Volume)
                # Base component is untouched (factor=1.0), others scaled to match base
                for col in exposure_cols + activity_cols:
                    if col in c.columns and not is_base:
                        c[col] = c[col] * col_norm[col]
                
                if is_base:
                    # Base component: strikes are already native SPX strikes.
                    # No moneyness mapping needed — just snap to nearest bucket
                    # to avoid floating-point ghost rows.
                    c['strike'] = (c['strike'] / bucket_size).round() * bucket_size
                    calls_list.append(c)
                else:
                    # Map strikes to base-equivalent via moneyness with linear
                    # interpolation between the two nearest buckets.  This prevents
                    # "bucket-hopping" where a small price change snaps 100% of a
                    # strike's exposure from one bucket to an adjacent one.
                    # Total exposure is conserved: weight_lo + weight_hi = 1.0.
                    weight_cols = exposure_cols + activity_cols
                    exact = (c['strike'] / comp_price) * base_price
                    # Round to avoid floating-point boundary jitter
                    exact = exact.round(6)
                    bucket_lo = np.floor(exact / bucket_size) * bucket_size
                    bucket_hi = bucket_lo + bucket_size
                    weight_hi = (exact - bucket_lo) / bucket_size
                    weight_lo = 1.0 - weight_hi
                    
                    c_lo = c.copy()
                    c_hi = c.copy()
                    c_lo['strike'] = bucket_lo
                    c_hi['strike'] = bucket_hi
                    for col in weight_cols:
                        if col in c_lo.columns:
                            c_lo[col] = c_lo[col] * weight_lo
                            c_hi[col] = c_hi[col] * weight_hi
                    
                    calls_list.append(c_lo)
                    calls_list.append(c_hi)

            # Process Puts
            if not p.empty:
                p = p.copy()
                
                # Normalize each column independently (Greeks + OI/Volume)
                # Base component is untouched (factor=1.0), others scaled to match base
                for col in exposure_cols + activity_cols:
                    if col in p.columns and not is_base:
                        p[col] = p[col] * col_norm[col]
                
                if is_base:
                    # Base component: strikes are already native SPX strikes.
                    p['strike'] = (p['strike'] / bucket_size).round() * bucket_size
                    puts_list.append(p)
                else:
                    # Map strikes to base-equivalent via moneyness with linear interpolation
                    exact = (p['strike'] / comp_price) * base_price
                    # Round to avoid floating-point boundary jitter
                    exact = exact.round(6)
                    bucket_lo = np.floor(exact / bucket_size) * bucket_size
                    bucket_hi = bucket_lo + bucket_size
                    weight_hi = (exact - bucket_lo) / bucket_size
                    weight_lo = 1.0 - weight_hi
                    
                    p_lo = p.copy()
                    p_hi = p.copy()
                    p_lo['strike'] = bucket_lo
                    p_hi['strike'] = bucket_hi
                    for col in weight_cols:
                        if col in p_lo.columns:
                            p_lo[col] = p_lo[col] * weight_lo
                            p_hi[col] = p_hi[col] * weight_hi
                    
                    puts_list.append(p_lo)
                    puts_list.append(p_hi)

        # Step 3: Combine and Aggregate by Strike
        combined_calls = pd.concat(calls_list, ignore_index=True) if calls_list else pd.DataFrame()
        combined_puts = pd.concat(puts_list, ignore_index=True) if puts_list else pd.DataFrame()

        def aggregate_market_data(df):
            if df.empty: return df
            sum_cols = ['openInterest', 'volume', 'GEX', 'DEX', 'VEX', 'Charm', 'Speed', 'Vomma', 'Color']
            avg_cols = ['lastPrice', 'bid', 'ask', 'impliedVolatility', 'delta', 'gamma', 'vega', 'theta', 'rho']
            
            agg_dict = {col: 'sum' for col in sum_cols if col in df.columns}
            agg_dict.update({col: 'mean' for col in avg_cols if col in df.columns})
            
            for col in df.columns:
                if col not in agg_dict and col != 'strike':
                    agg_dict[col] = 'first'
                    
            return df.groupby('strike', as_index=False).agg(agg_dict)

        combined_calls = aggregate_market_data(combined_calls)
        combined_puts = aggregate_market_data(combined_puts)
        
        return combined_calls, combined_puts

    try:
        expiry = datetime.strptime(date, '%Y-%m-%d').date()
        chain_response = client.option_chains(
            symbol=ticker,
            fromDate=expiry.strftime('%Y-%m-%d'),
            toDate=expiry.strftime('%Y-%m-%d'),
            contractType='ALL'
        )
        
        if not chain_response.ok:
            try:
                error_data = chain_response.json()
                error_msg = error_data.get('error', 'Unknown API error')
                if 'error_description' in error_data:
                    error_msg += f": {error_data['error_description']}"
                raise Exception(f"Schwab API Error: {error_msg}")
            except:
                raise Exception(f"Schwab API Error: {chain_response.status_code} {chain_response.reason}")
        
        chain = chain_response.json()
        S = float(chain.get('underlyingPrice', 0))
        if S == 0:
            S = get_current_price(ticker)
        if S is None:
            return pd.DataFrame(), pd.DataFrame()
        
        # Calculate time to expiration in years
        t = calculate_time_to_expiration(expiry)
        t = max(t, 1e-5)  # Minimum 1 minute
        r = 0.02  # risk-free rate (2% as default to match Yahoo script)
        
        calls_data = []
        puts_data = []
        display_tickers = format_display_ticker(ticker)
        
        for exp_date, strikes in chain.get('callExpDateMap', {}).items():
            for strike, options in strikes.items():
                for option in options:
                    if any(option['symbol'].startswith(t) for t in display_tickers):
                        K = float(option['strikePrice'])
                        raw_vol = float(option.get('volatility', -999.0))
                        vol = (raw_vol / 100) if raw_vol > 0 else 0.20
                        
                        # Calculate Greeks
                        if t > 0 and vol > 0 and K > 0:
                            delta, gamma, vega, vanna = calculate_greeks('c', S, K, t, vol, r, 0)
                            theta = calculate_theta('c', S, K, t, vol, r, 0)
                            rho = calculate_rho('c', S, K, t, vol, r, 0)
                        else:
                            delta = gamma = theta = vega = rho = 0
                        
                        option_data = {
                            'contractSymbol': option['symbol'],
                            'strike': K,
                            'lastPrice': float(option['last']),
                            'bid': float(option['bid']),
                            'ask': float(option['ask']),
                            'mark': float(option.get('mark', 0) or 0),
                            'volume': int(option['totalVolume']),
                            'openInterest': int(option['openInterest']),
                            'impliedVolatility': vol,
                            'inTheMoney': option['inTheMoney'],
                            'expiration': datetime.strptime(exp_date.split(':')[0], '%Y-%m-%d').date(),
                            'quoteTimeInLong': _coerce_epoch_ms(option.get('quoteTimeInLong')),
                            'tradeTimeInLong': _coerce_epoch_ms(option.get('tradeTimeInLong')),
                            'delta': delta,
                            'gamma': gamma,
                            'theta': theta,
                            'vega': vega,
                            'rho': rho
                        }
                        option_data['side'] = infer_side(option_data['lastPrice'], option_data['bid'], option_data['ask'])
                        calls_data.append(option_data)
        
        for exp_date, strikes in chain.get('putExpDateMap', {}).items():
            for strike, options in strikes.items():
                for option in options:
                    if any(option['symbol'].startswith(t) for t in display_tickers):
                        K = float(option['strikePrice'])
                        raw_vol = float(option.get('volatility', -999.0))
                        vol = (raw_vol / 100) if raw_vol > 0 else 0.20
                        
                        # Calculate Greeks
                        if t > 0 and vol > 0 and K > 0:
                            delta, gamma, vega, vanna = calculate_greeks('p', S, K, t, vol, r, 0)
                            theta = calculate_theta('p', S, K, t, vol, r, 0)
                            rho = calculate_rho('p', S, K, t, vol, r, 0)
                        else:
                            delta = gamma = theta = vega = rho = 0
                        
                        option_data = {
                            'contractSymbol': option['symbol'],
                            'strike': K,
                            'lastPrice': float(option['last']),
                            'bid': float(option['bid']),
                            'ask': float(option['ask']),
                            'mark': float(option.get('mark', 0) or 0),
                            'volume': int(option['totalVolume']),
                            'openInterest': int(option['openInterest']),
                            'impliedVolatility': vol,
                            'inTheMoney': option['inTheMoney'],
                            'expiration': datetime.strptime(exp_date.split(':')[0], '%Y-%m-%d').date(),
                            'quoteTimeInLong': _coerce_epoch_ms(option.get('quoteTimeInLong')),
                            'tradeTimeInLong': _coerce_epoch_ms(option.get('tradeTimeInLong')),
                            'delta': delta,
                            'gamma': gamma,
                            'theta': theta,
                            'vega': vega,
                            'rho': rho
                        }
                        option_data['side'] = infer_side(option_data['lastPrice'], option_data['bid'], option_data['ask'])
                        puts_data.append(option_data)
        
        # Calculate exposures with selected metric
        for option_data in calls_data:
            weight = 0
            if exposure_metric == 'Volume':
                weight = option_data['volume']
            elif exposure_metric == 'Max OI vs Volume':
                # Use the greater of OI and volume as the weight
                oi = option_data['openInterest']
                vol = option_data['volume']
                weight = max(oi, vol)
            elif exposure_metric == 'OI + Volume':
                # Use the sum of OI and volume as the weight
                weight = option_data['openInterest'] + option_data['volume']
            else: # Open Interest
                weight = option_data['openInterest']

            option_data['_weight'] = weight
            exposures = calculate_greek_exposures(option_data, S, weight, delta_adjusted=delta_adjusted, calculate_in_notional=calculate_in_notional)
            option_data.update(exposures)

        for option_data in puts_data:
            weight = 0
            if exposure_metric == 'Volume':
                weight = option_data['volume']
            elif exposure_metric == 'Max OI vs Volume':
                # Use the greater of OI and volume as the weight
                oi = option_data['openInterest']
                vol = option_data['volume']
                weight = max(oi, vol)
            elif exposure_metric == 'OI + Volume':
                # Use the sum of OI and volume as the weight
                weight = option_data['openInterest'] + option_data['volume']
            else: # Open Interest
                weight = option_data['openInterest']

            option_data['_weight'] = weight
            exposures = calculate_greek_exposures(option_data, S, weight, delta_adjusted=delta_adjusted, calculate_in_notional=calculate_in_notional)
            option_data.update(exposures)

        calls = pd.DataFrame(calls_data)
        puts = pd.DataFrame(puts_data)
        return calls, puts
        
    except Exception as e:
        msg = f"Error fetching options chain: {e}"
        print(msg)
        # Propagate so callers (API routes) can return the error to clients
        raise Exception(msg)

def calculate_greeks(flag, S, K, t, sigma, r=0.02, q=0):
    """Calculate delta, gamma, vega, vanna."""
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        
        # Delta
        if flag == 'c':
            delta = np.exp(-q * t) * norm.cdf(d1)
        else:
            delta = np.exp(-q * t) * (norm.cdf(d1) - 1)
        
        # Gamma
        gamma = np.exp(-q * t) * norm.pdf(d1) / (S * sigma * np.sqrt(t))
        
        # Vega
        vega = S * np.exp(-q * t) * norm.pdf(d1) * np.sqrt(t)
        
        # Vanna
        vanna = -np.exp(-q * t) * norm.pdf(d1) * d2 / sigma
        
        return delta, gamma, vega, vanna
    except Exception as e:
        return 0, 0, 0, 0

def calculate_theta(flag, S, K, t, sigma, r=0.02, q=0):
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        
        term1 = -S * np.exp(-q * t) * norm.pdf(d1) * sigma / (2 * np.sqrt(t))
        
        if flag == 'c':
            theta = term1 - r * K * np.exp(-r * t) * norm.cdf(d2) + q * S * np.exp(-q * t) * norm.cdf(d1)
        else:
            theta = term1 + r * K * np.exp(-r * t) * norm.cdf(-d2) - q * S * np.exp(-q * t) * norm.cdf(-d1)
        return theta
    except:
        return 0

def calculate_rho(flag, S, K, t, sigma, r=0.02, q=0):
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        
        if flag == 'c':
            rho = K * t * np.exp(-r * t) * norm.cdf(d2)
        else:
            rho = -K * t * np.exp(-r * t) * norm.cdf(-d2)
        return rho
    except:
        return 0

def calculate_charm(flag, S, K, t, sigma, r=0.02, q=0):
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        norm_d1 = norm.pdf(d1)
        
        if flag == 'c':
            charm = -np.exp(-q * t) * (norm_d1 * (2*(r-q)*t - d2*sigma*np.sqrt(t)) / (2*t*sigma*np.sqrt(t)) - q * norm.cdf(d1))
        else:
            charm = -np.exp(-q * t) * (norm_d1 * (2*(r-q)*t - d2*sigma*np.sqrt(t)) / (2*t*sigma*np.sqrt(t)) + q * norm.cdf(-d1))
        return charm
    except:
        return 0

def calculate_speed(flag, S, K, t, sigma, r=0.02, q=0):
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        gamma = np.exp(-q * t) * norm.pdf(d1) / (S * sigma * np.sqrt(t))
        speed = -gamma * (d1/(sigma * np.sqrt(t)) + 1) / S
        return speed
    except:
        return 0

def calculate_vomma(flag, S, K, t, sigma, r=0.02, q=0):
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        vega = S * np.exp(-q * t) * norm.pdf(d1) * np.sqrt(t)
        vomma = vega * (d1 * d2) / sigma
        return vomma
    except:
        return 0

def calculate_color(flag, S, K, t, sigma, r=0.02, q=0):
    try:
        t = max(t, 1e-5)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        norm_d1 = norm.pdf(d1)
        term1 = 2 * (r - q) * t
        term2 = d2 * sigma * np.sqrt(t)
        color = -np.exp(-q*t) * (norm_d1 / (2 * S * t * sigma * np.sqrt(t))) * \
                (1 + (term1 - term2) * d1 / (2 * t * sigma * np.sqrt(t)))
        return color
    except:
        return 0

def calculate_greek_exposures(option, S, weight, delta_adjusted: bool = False, calculate_in_notional: bool = True, iv_override=None):
    """Calculate accurate Greek exposures per $1 move, weighted by the provided weight.

    iv_override lets scenario callers re-run the same formula at a shifted IV
    without mutating the source option dict.
    """
    contract_size = 100

    # Recalculate Greeks to ensure consistency with S and t
    vol = option['impliedVolatility'] if iv_override is None else iv_override
    
    # Calculate time to expiration in years
    expiry_date = option['expiration']
    t = calculate_time_to_expiration(expiry_date)
    t = max(t, 1e-5)  # Minimum time to prevent division by zero
    
    # Determine flag (c/p) based on symbol if possible, or use parameter
    flag = 'c'
    if 'P' in option['contractSymbol'] and not 'C' in option['contractSymbol']:
         flag = 'p'
    import re
    match = re.search(r'\d{6}([CP])', option['contractSymbol'])
    if match:
        flag = match.group(1).lower()

    r = 0.02  # risk-free rate
    q = 0

    # Re-calculate Greeks using consistent inputs
    K = option['strike']
    delta, gamma, _, vanna = calculate_greeks(flag, S, K, t, vol, r, q)

    # Calculate exposures (per $1 move in underlying)
    # Check if calculation should be in notional (dollars) or standard (shares)
    spot_multiplier = S if calculate_in_notional else 1.0
    
    # DEX: Delta exposure
    # Delta is unitless (shares/contract / 100). 
    # Notional DEX = Delta * 100 * S. (Dollar Value of Delta).
    dex = delta * weight * contract_size * spot_multiplier
    
    # GEX: Gamma exposure
    # GEX (Notional) ~ Gamma * S * S * 0.01
    gex = gamma * weight * contract_size * S * spot_multiplier * 0.01
    
    # VEX: Vanna exposure
    vanna_exposure = vanna * weight * contract_size * spot_multiplier * 0.01

    # Charm
    charm = calculate_charm(flag, S, K, t, vol, r, q)
    charm_exposure = charm * weight * contract_size * spot_multiplier / 365.0
    
    # Speed
    # Speed Exposure (Notional) ~ Speed * S * S * 0.01 
    speed = calculate_speed(flag, S, K, t, vol, r, q)
    speed_exposure = speed * weight * contract_size * S * spot_multiplier * 0.01
    
    # Vomma
    vomma = calculate_vomma(flag, S, K, t, vol, r, q)
    vomma_exposure = vomma * weight * contract_size * 0.01

    # Color
    color = calculate_color(flag, S, K, t, vol, r, q)
    color_exposure = color * weight * contract_size * S * spot_multiplier * 0.01 / 365.0

    # Apply delta adjustment if enabled
    if delta_adjusted:
        abs_delta = abs(delta)
        gex *= abs_delta
        vanna_exposure *= abs_delta
        charm_exposure *= abs_delta
        speed_exposure *= abs_delta
        vomma_exposure *= abs_delta
        color_exposure *= abs_delta

    return {
        'DEX': dex,
        'GEX': gex,
        'VEX': vanna_exposure,
        'Charm': charm_exposure,
        'Speed': speed_exposure,
        'Vomma': vomma_exposure,
        'Color': color_exposure
    }

def get_current_price(ticker):
    if client is None:
        raise Exception("Schwab API client not initialized. Check your environment variables.")
        
    if ticker == "MARKET":
        ticker = "$SPX"
    elif ticker == "MARKET2":
        ticker = "SPY"
    try:
        quote_response = client.quotes(ticker)
        if not quote_response.ok:
            raise Exception(f"Failed to fetch quote: {quote_response.status_code} {quote_response.reason}")
        quote = quote_response.json()
        if quote and ticker in quote:
            return quote[ticker]['quote']['lastPrice']
        raise Exception("Malformed quote data returned from Schwab API")
    except Exception as e:
        msg = f"Error fetching price from Schwab API: {e}"
        print(msg)
        raise Exception(msg)

def get_option_expirations(ticker):
    if client is None:
        raise Exception("Schwab API client not initialized. Check your environment variables.")
    
    if ticker == "MARKET":
        ticker = "$SPX"
    elif ticker == "MARKET2":
        ticker = "SPY"
    try:
        response = client.option_expiration_chain(ticker)
        if not response.ok:
            raise Exception(f"Failed to fetch expirations: {response.status_code} {response.reason}")
        response_json = response.json()
        if response_json and 'expirationList' in response_json:
            expiration_dates = [item['expirationDate'] for item in response_json['expirationList']]
            return sorted(expiration_dates)
        return []
    except Exception as e:
        msg = f"Error fetching option expirations: {e}"
        print(msg)
        # Propagate the error so route handlers or Flask error handlers can return it to clients
        raise Exception(msg)

def get_color_with_opacity(value, max_value, base_color, color_intensity=True):
    """Get color with opacity based on value. Legacy function for backward compatibility."""
    if not color_intensity:
        opacity = 1.0  # Full opacity when color intensity is disabled
    else:
        # Ensure opacity is between 0.3 and 0.8 for better visibility and less intensity
        opacity = min(max(abs(value / max_value) if max_value != 0 else 0, 0.3), 0.8)
        
    if isinstance(base_color, str) and base_color.startswith('#'):
        # Convert hex to rgb
        r = int(base_color[1:3], 16)
        g = int(base_color[3:5], 16)
        b = int(base_color[5:7], 16)
        return f'rgba({r}, {g}, {b}, {opacity})'
    return base_color

def hex_to_rgba(hex_color, alpha=1.0):
    """Convert hex color to rgba string with specified alpha."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    return f'rgba({int(hex_color[0:2], 16)}, {int(hex_color[2:4], 16)}, {int(hex_color[4:6], 16)}, {alpha})'

def get_colors(base_color, values, max_val, coloring_mode='Solid'):
    """
    Apply coloring mode to a set of values.
    
    Args:
        base_color: Hex color string (e.g., '#00FF00')
        values: Array/list of numeric values
        max_val: Maximum value for normalization
        coloring_mode: 'Solid', 'Linear Intensity', or 'Ranked Intensity'
    
    Returns:
        Either a single color string (Solid mode) or list of RGBA colors
    """
    # Solid mode: return base color as-is
    if coloring_mode == 'Solid':
        return base_color
    
    # Handle edge case
    if max_val == 0:
        return base_color
    
    # Convert to list if series/array
    vals = values.tolist() if hasattr(values, 'tolist') else list(values)
    
    if coloring_mode == 'Linear Intensity':
        # Linear mapping: opacity from 0.3 to 1.0
        # Formula: 0.3 + 0.7 * (|value| / max_value)
        return [hex_to_rgba(base_color, 0.3 + 0.7 * (abs(v) / max_val)) for v in vals]
    
    elif coloring_mode == 'Ranked Intensity':
        # Exponential mapping: opacity from 0.1 to 1.0 with cubic power
        # Formula: 0.1 + 0.9 * ((|value| / max_value) ^ 3)
        # This aggressively fades lower values, making only top exposures bright
        return [hex_to_rgba(base_color, 0.1 + 0.9 * ((abs(v) / max_val) ** 3)) for v in vals]
    
    else:
        return base_color

def get_net_colors(values, max_val, call_color, put_color, coloring_mode='Solid'):
    """
    Apply coloring mode to net exposure values (can be positive or negative).
    Color is based on sign: positive = call_color, negative = put_color.
    
    Args:
        values: Array/list of numeric values (can be negative)
        max_val: Maximum absolute value for normalization
        call_color: Hex color for positive values
        put_color: Hex color for negative values
        coloring_mode: 'Solid', 'Linear Intensity', or 'Ranked Intensity'
    
    Returns:
        List of colors (either hex or RGBA based on mode)
    """
    vals = values.tolist() if hasattr(values, 'tolist') else list(values)
    
    if coloring_mode == 'Solid':
        return [call_color if v >= 0 else put_color for v in vals]
    
    if max_val == 0:
        return [call_color if v >= 0 else put_color for v in vals]
    
    colors = []
    for val in vals:
        base = call_color if val >= 0 else put_color
        
        if coloring_mode == 'Linear Intensity':
            opacity = 0.3 + 0.7 * (abs(val) / max_val)
            colors.append(hex_to_rgba(base, min(1.0, opacity)))
        
        elif coloring_mode == 'Ranked Intensity':
            opacity = 0.1 + 0.9 * ((abs(val) / max_val) ** 3)
            colors.append(hex_to_rgba(base, min(1.0, opacity)))
        
        else:  # Solid fallback
            colors.append(base)

    return colors


# Centralized Plotly theme — matches CSS tokens in the inline <style> :root block.
# Chart builders unpack **PLOT_THEME so the dashboard and its charts stay in lockstep.
PLOT_THEME = dict(
    paper_bgcolor='#0B0E11',
    plot_bgcolor='#0B0E11',
    font=dict(family='Inter, -apple-system, sans-serif', color='#9CA3AF', size=11),
    xaxis=dict(gridcolor='#1E242D', zerolinecolor='#2A313B'),
    yaxis=dict(gridcolor='#1E242D', zerolinecolor='#2A313B'),
    margin=dict(l=50, r=80, t=30, b=24),
)
CALL_COLOR = '#10B981'
PUT_COLOR = '#EF4444'


def apply_plotly_theme(fig) -> None:
    """Apply shared visual theme to a Plotly figure. Call at the END of every chart builder."""
    fig.update_layout(
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        font=dict(
            family=PLOT_THEME['font']['family'],
            size=PLOT_THEME['font']['size'],
            color=PLOT_THEME['font']['color'],
        ),
        xaxis=dict(
            gridcolor=PLOT_THEME['xaxis']['gridcolor'],
            zerolinecolor=PLOT_THEME['xaxis']['zerolinecolor'],
            nticks=10,
        ),
        yaxis=dict(
            gridcolor=PLOT_THEME['yaxis']['gridcolor'],
            zerolinecolor=PLOT_THEME['yaxis']['zerolinecolor'],
        ),
        hoverlabel=dict(
            bgcolor=PLOT_THEME['paper_bgcolor'],
            bordercolor=PLOT_THEME['xaxis']['gridcolor'],
            font=dict(family=PLOT_THEME['font']['family'], size=12, color=PLOT_THEME['font']['color']),
        ),
    )


def create_exposure_chart(calls, puts, exposure_type, title, S, strike_range=0.02, show_calls=True, show_puts=True, show_net=True, coloring_mode='Solid', call_color=CALL_COLOR, put_color=PUT_COLOR, selected_expiries=None, horizontal=False, show_abs_gex_area=False, abs_gex_opacity=0.2, highlight_max_level=False, max_level_color='#800080', max_level_mode='Absolute'):
    # Ensure the exposure_type column exists
    if exposure_type not in calls.columns or exposure_type not in puts.columns:
        print(f"Warning: {exposure_type} not found in data")
        return go.Figure().to_json()
    
    # Filter out zero values and create dataframes
    calls_df = calls[['strike', exposure_type]].copy()
    calls_df = calls_df[calls_df[exposure_type] != 0]
    calls_df['OptionType'] = 'Call'
    
    puts_df = puts[['strike', exposure_type]].copy()
    puts_df = puts_df[puts_df[exposure_type] != 0]
    puts_df['OptionType'] = 'Put'
    
    # Calculate range based on percentage of current price
    min_strike = S * (1 - strike_range)
    max_strike = S * (1 + strike_range)
    
    calls_df = calls_df[(calls_df['strike'] >= min_strike) & (calls_df['strike'] <= max_strike)]
    puts_df = puts_df[(puts_df['strike'] >= min_strike) & (puts_df['strike'] <= max_strike)]
    
    # Determine strike interval and aggregate by rounded strikes
    all_strikes = list(calls_df['strike']) + list(puts_df['strike'])
    if all_strikes:
        strike_interval = get_strike_interval(all_strikes)
        calls_df = aggregate_by_strike(calls_df, [exposure_type], strike_interval)
        puts_df = aggregate_by_strike(puts_df, [exposure_type], strike_interval)
    
    # Calculate total net exposure from the entire chain (not just strike range)
    total_call_exposure = calls[exposure_type].sum() if not calls.empty and exposure_type in calls.columns else 0
    total_put_exposure = puts[exposure_type].sum() if not puts.empty and exposure_type in puts.columns else 0

    if exposure_type == 'GEX':
        total_net_exposure = total_call_exposure - total_put_exposure
    elif exposure_type == 'DEX':
        total_net_exposure = total_call_exposure + total_put_exposure
    else:
        total_net_exposure = total_call_exposure + total_put_exposure
        # Calculate total net volume from the entire chain (not just strike range)
        total_call_volume = calls['volume'].sum() if not calls.empty and 'volume' in calls.columns else 0
        total_put_volume = puts['volume'].sum() if not puts.empty and 'volume' in puts.columns else 0
        total_net_volume = total_call_volume - total_put_volume
    
    # Create the main title and net exposure as separate annotations
    fig = go.Figure()
    
    # Add Absolute GEX Area Chart if enabled
    if exposure_type == 'GEX' and show_abs_gex_area:
        try:
            # Get all unique strikes in the range
            all_strikes_abs = sorted(list(set(calls_df['strike'].tolist() + puts_df['strike'].tolist())))
            abs_gex_values = []
            
            for strike in all_strikes_abs:
                # Calculate absolute gamma at this strike (Total Gamma)
                c_val = calls_df[calls_df['strike'] == strike][exposure_type].sum() if not calls_df.empty else 0
                p_val = puts_df[puts_df['strike'] == strike][exposure_type].sum() if not puts_df.empty else 0
                
                # Use absolute values to get total magnitude
                total_abs_val = abs(c_val) + abs(p_val)
                abs_gex_values.append(total_abs_val)
                
            # Add the area trace
            if horizontal:
                fig.add_trace(go.Scatter(
                    y=all_strikes_abs,
                    x=abs_gex_values,
                    mode='none',
                    fill='tozerox',
                    name='Abs GEX Total',
                    fillcolor=f'rgba(200, 200, 200, {abs_gex_opacity})',
                    hoverinfo='skip',
                    showlegend=False
                ))
            else:
                fig.add_trace(go.Scatter(
                    x=all_strikes_abs,
                    y=abs_gex_values,
                    mode='none',
                    fill='tozeroy',
                    name='Abs GEX Total',
                    fillcolor=f'rgba(200, 200, 200, {abs_gex_opacity})',
                    hoverinfo='skip',
                    showlegend=False
                ))
        except Exception as e:
            print(f"Error adding Abs GEX area: {e}")

    # Define colors
    grid_color = PLOT_THEME['xaxis']['gridcolor']
    text_color = PLOT_THEME['font']['color']
    background_color = PLOT_THEME['paper_bgcolor']
    
    # Calculate max exposure for normalization across all data (calls, puts, net)
    max_exposure = 1.0
    all_abs_vals = []
    if not calls_df.empty:
        all_abs_vals.extend(calls_df[exposure_type].abs().tolist())
    if not puts_df.empty:
        all_abs_vals.extend(puts_df[exposure_type].abs().tolist())
    if all_abs_vals:
        max_exposure = max(all_abs_vals)
    if max_exposure == 0:
        max_exposure = 1.0  # Prevent division by zero
    
    if show_calls and not calls_df.empty:
        # Apply coloring mode
        call_colors = get_colors(call_color, calls_df[exposure_type], max_exposure, coloring_mode)
        
        if horizontal:
            fig.add_trace(go.Bar(
                y=calls_df['strike'].tolist(),
                x=calls_df[exposure_type].tolist(),
                name='Call',
                marker_color=call_colors,
                text=[format_large_number(val) for val in calls_df[exposure_type]],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Value: %{text}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=calls_df['strike'].tolist(),
                y=calls_df[exposure_type].tolist(),
                name='Call',
                marker_color=call_colors,
                text=[format_large_number(val) for val in calls_df[exposure_type]],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Value: %{text}<extra></extra>',
                marker_line_width=0
            ))
    
    if show_puts and not puts_df.empty:
        # Apply coloring mode
        put_colors = get_colors(put_color, puts_df[exposure_type], max_exposure, coloring_mode)
            
        if horizontal:
            fig.add_trace(go.Bar(
                y=puts_df['strike'].tolist(),
                x=(-puts_df[exposure_type]).tolist(),
                name='Put',
                marker_color=put_colors,
                text=[format_large_number(val) for val in puts_df[exposure_type]],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Value: %{text}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=puts_df['strike'].tolist(),
                y=(-puts_df[exposure_type]).tolist(),
                name='Put',
                marker_color=put_colors,
                text=[format_large_number(val) for val in puts_df[exposure_type]],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Value: %{text}<extra></extra>',
                marker_line_width=0
            ))
    
    if show_net and not (calls_df.empty and puts_df.empty):
        # Create net exposure by combining calls and puts
        all_strikes = sorted(set(calls_df['strike'].tolist() + puts_df['strike'].tolist()))
        net_exposure = []
        
        for strike in all_strikes:
            call_value = calls_df[calls_df['strike'] == strike][exposure_type].sum() if not calls_df.empty else 0
            put_value = puts_df[puts_df['strike'] == strike][exposure_type].sum() if not puts_df.empty else 0
            
            if exposure_type == 'GEX':
                net_value = call_value - put_value
            elif exposure_type == 'DEX':
                net_value = call_value + put_value
            else:
                net_value = call_value + put_value
            
            net_exposure.append(net_value)
        
        # Calculate max for net exposure normalization
        max_net_exposure = max(abs(min(net_exposure)), abs(max(net_exposure))) if net_exposure else 1.0
        if max_net_exposure == 0:
            max_net_exposure = 1.0
        
        # Apply coloring mode for net values
        net_colors = get_net_colors(net_exposure, max_net_exposure, call_color, put_color, coloring_mode)
        
        if horizontal:
            fig.add_trace(go.Bar(
                y=all_strikes,
                x=net_exposure,
                name='Net',
                marker_color=net_colors,
                text=[format_large_number(val) for val in net_exposure],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Net Value: %{text}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=all_strikes,
                y=net_exposure,
                name='Net',
                marker_color=net_colors,
                text=[format_large_number(val) for val in net_exposure],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Net Value: %{text}<extra></extra>',
                marker_line_width=0
            ))
    
    if horizontal:
        # Add current price line
        fig.add_hline(
            y=S,
            line_dash="dash",
            line_color=text_color,
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="right",
            annotation_font_color=text_color,
            line_width=1
        )
    else:
        # Add current price line with improved styling
        fig.add_vline(
            x=S,
            line_dash="dash",
            line_color=text_color,
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="top",
            annotation_font_color=text_color,
            line_width=1
        )
    
    # Calculate padding as percentage of price range
    padding = (max_strike - min_strike) * 0.02
    
    # Add expiry info to title if multiple expiries are selected
    chart_title = title
    if selected_expiries and len(selected_expiries) > 1:
        chart_title = f"{title} ({len(selected_expiries)} expiries)"
    
    xaxis_config = dict(
        title='',
        title_font=dict(color=text_color),
        tickfont=dict(color=text_color, size=12),
        gridcolor=grid_color,
        linecolor=grid_color,
        showgrid=False,
        zeroline=True,
        zerolinecolor=grid_color
    )
    
    yaxis_config = dict(
        title='',
        title_font=dict(color=text_color),
        tickfont=dict(color=text_color),
        gridcolor=grid_color,
        linecolor=grid_color,
        showgrid=False,
        zeroline=True,
        zerolinecolor=grid_color
    )
    
    # Configure axes based on orientation
    if horizontal:
        # Strike axis is Y
        yaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False,
            tickformat='.0f',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor=text_color,
            automargin=True
        ))
        # Value axis is X
        xaxis_config.update(dict(
            showticklabels=True
        ))
    else:
        # Strike axis is X
        xaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False,
            tickangle=45,
            tickformat='.0f',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor=text_color,
            automargin=True
        ))
        # Value axis is Y
        yaxis_config.update(dict(
            showticklabels=True
        ))

    # Update layout with improved styling and split title
    fig.update_layout(
        title=dict(
            text=chart_title,  # Main title with expiry info
            font=dict(color=text_color, size=16),
            x=0.5,
            xanchor='center',
            y=0.98  # Push title higher to avoid collision with price annotation
        ),
        annotations=list(fig.layout.annotations) + [
            dict(
                text=f"Net: {format_large_number(abs(total_net_exposure))}",
                x=0.98,
                y=1.03,
                xref='paper',
                yref='paper',
                xanchor='right',
                yanchor='top',
                showarrow=False,
                font=dict(
                    size=14,
                    color=call_color if total_net_exposure >= 0 else put_color
                )
            )
        ],
        xaxis=xaxis_config,
        yaxis=yaxis_config,
        barmode='relative',
        hovermode='y unified' if horizontal else 'x unified',
        plot_bgcolor=background_color,
        paper_bgcolor=background_color,
        font=dict(color=text_color),
        showlegend=False,  # Removed legend
        bargap=0.1,
        bargroupgap=0.1,
        margin=dict(l=50, r=80, t=60, b=20),
        hoverlabel=dict(
            bgcolor=background_color,
            font_size=12,
            font_family="Arial"
        ),
        spikedistance=1000,
        hoverdistance=100,
        height=500
    )
    
    # Add hover spikes
    fig.update_xaxes(showspikes=True, spikecolor=text_color, spikethickness=1)
    fig.update_yaxes(showspikes=True, spikecolor=text_color, spikethickness=1)
    
    # Logic for Highlighting Max Level
    if highlight_max_level:
        try:
            if max_level_mode == 'Net':
                # Compute net exposure for the entire chain (not just plotted range)
                all_chain_strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
                chain_net_exposure = []
                for strike in all_chain_strikes:
                    call_value = calls[calls['strike'] == strike][exposure_type].sum() if not calls.empty else 0
                    put_value = puts[puts['strike'] == strike][exposure_type].sum() if not puts.empty else 0
                    if exposure_type == 'GEX':
                        net_value = call_value - put_value
                    elif exposure_type == 'DEX':
                        net_value = call_value + put_value
                    else:
                        net_value = call_value + put_value
                    chain_net_exposure.append(net_value)

                # Find the strike with the max absolute net exposure (or max/min depending on sign of total net)
                if chain_net_exposure:
                    total_chain_net = sum(chain_net_exposure)
                    if total_chain_net >= 0:
                        max_net_val = max(chain_net_exposure)
                    else:
                        max_net_val = min(chain_net_exposure)
                    # Find the strike(s) with this value
                    max_strikes = [s for s, v in zip(all_chain_strikes, chain_net_exposure) if v == max_net_val]
                    # Now, highlight the bar in the plotted Net trace that matches this strike (if present)
                    net_trace_idx = next((i for i, t in enumerate(fig.data) if t.type == 'bar' and t.name == 'Net'), None)
                    if net_trace_idx is not None:
                        plotted_strikes = fig.data[net_trace_idx].y if horizontal else fig.data[net_trace_idx].x
                        # For horizontal, y is strikes; else, x is strikes
                        if plotted_strikes:
                            # Find the index of the strike in the plot that matches max_strikes
                            highlight_idx = None
                            for idx, s in enumerate(plotted_strikes):
                                if s in max_strikes:
                                    highlight_idx = idx
                                    break
                            if highlight_idx is not None:
                                vals = fig.data[net_trace_idx].x if horizontal else fig.data[net_trace_idx].y
                                line_widths = [0] * len(vals)
                                line_widths[highlight_idx] = 5
                                fig.data[net_trace_idx].update(marker=dict(
                                    line=dict(width=line_widths, color=max_level_color)
                                ))
            else:  # 'Absolute' - default behaviour
                max_abs_val = 0
                max_trace_idx = -1
                max_bar_idx = -1
                for i, trace in enumerate(fig.data):
                    if trace.type == 'bar':
                        vals = trace.x if horizontal else trace.y
                        if vals:
                            abs_vals = [abs(v) for v in vals]
                            if abs_vals:
                                local_max = max(abs_vals)
                                if local_max > max_abs_val:
                                    max_abs_val = local_max
                                    max_trace_idx = i
                                    max_bar_idx = abs_vals.index(local_max)
                if max_trace_idx != -1:
                    vals = fig.data[max_trace_idx].x if horizontal else fig.data[max_trace_idx].y
                    line_widths = [0] * len(vals)
                    line_widths[max_bar_idx] = 5
                    fig.data[max_trace_idx].update(marker=dict(
                        line=dict(width=line_widths, color=max_level_color)
                    ))
        except Exception as e:
            print(f"Error highlighting max level: {e}")

    apply_plotly_theme(fig)
    return fig.to_json()

def create_volume_chart(call_volume, put_volume, use_itm=True, call_color=CALL_COLOR, put_color=PUT_COLOR, selected_expiries=None):
    base_title = '% Range Call vs Put Volume Ratio' if use_itm else 'Call vs Put Volume Ratio'
    title = base_title
    if selected_expiries and len(selected_expiries) > 1:
        title = f"{base_title} ({len(selected_expiries)} expiries)"
    fig = go.Figure(data=[go.Pie(
        labels=['Calls', 'Puts'],
        values=[call_volume, put_volume],
        hole=0.3,
        marker_colors=[call_color, put_color]
    )])
    
    fig.update_layout(
        title_text=title,
        showlegend=True,
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='white'),
        height=500
    )
    apply_plotly_theme(fig)
    return fig.to_json()

def create_options_volume_chart(calls, puts, S, strike_range=0.02, call_color=CALL_COLOR, put_color=PUT_COLOR, coloring_mode='Solid', show_calls=True, show_puts=True, show_net=False, selected_expiries=None, horizontal=False, highlight_max_level=False, max_level_color='#800080', max_level_mode='Absolute', show_totals=True):
    # Filter strikes within range
    min_strike = S * (1 - strike_range)
    max_strike = S * (1 + strike_range)
    
    calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)].copy()
    puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)].copy()
    
    # Determine strike interval and aggregate by rounded strikes
    all_strikes = list(calls['strike']) + list(puts['strike'])
    if all_strikes:
        strike_interval = get_strike_interval(all_strikes)
        calls = aggregate_by_strike(calls, ['volume'], strike_interval)
        puts = aggregate_by_strike(puts, ['volume'], strike_interval)

    all_strikes_list = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
    call_volume_by_strike = {
        float(strike): float(volume)
        for strike, volume in zip(calls['strike'].tolist(), calls['volume'].tolist())
    }
    put_volume_by_strike = {
        float(strike): float(volume)
        for strike, volume in zip(puts['strike'].tolist(), puts['volume'].tolist())
    }
    call_volume = [call_volume_by_strike.get(float(strike), 0.0) for strike in all_strikes_list]
    put_volume = [put_volume_by_strike.get(float(strike), 0.0) for strike in all_strikes_list]
    total_volume = [call + put for call, put in zip(call_volume, put_volume)]
    net_volume = [call - put for call, put in zip(call_volume, put_volume)]

    if not show_calls and not show_puts and not show_net:
        show_calls = True
        show_puts = True

    # Create figure
    fig = go.Figure()
    max_side_volume = max(call_volume + put_volume + [1.0])
    max_total_volume = max(total_volume + [1.0])
    max_net_volume = max([abs(val) for val in net_volume] + [1.0])
    label_threshold = max_side_volume * 0.16
    raw_max_total_volume = max(total_volume) if total_volume else 0.0
    poc_idx = total_volume.index(raw_max_total_volume) if all_strikes_list and raw_max_total_volume > 0 else None
    atm_idx = min(range(len(all_strikes_list)), key=lambda idx: abs(all_strikes_list[idx] - S)) if all_strikes_list else None

    def _call_put_customdata():
        return [
            [format_large_number(call), format_large_number(put), format_large_number(total), format_large_number(net)]
            for call, put, total, net in zip(call_volume, put_volume, total_volume, net_volume)
        ]

    def _format_strike_label(strike):
        return f"{strike:.2f}".rstrip('0').rstrip('.')

    def _build_strike_tick_labels():
        if not all_strikes_list:
            return []
        if not show_totals or not horizontal:
            return [_format_strike_label(strike) for strike in all_strikes_list]
        return [
            f"{_format_strike_label(strike)} ({format_large_number(total)})"
            for strike, total in zip(all_strikes_list, total_volume)
        ]

    customdata = _call_put_customdata()
    strike_ticktext = _build_strike_tick_labels()
    call_text = [format_large_number(val) if show_totals and val >= label_threshold else '' for val in call_volume]
    put_text = [format_large_number(val) if show_totals and val >= label_threshold else '' for val in put_volume]

    if horizontal and all_strikes_list:
        call_colors = get_colors(call_color, call_volume, max_side_volume, coloring_mode)
        put_colors = get_colors(put_color, put_volume, max_side_volume, coloring_mode)
        row_count = len(all_strikes_list)

        call_major_x = [0.0] * row_count
        put_major_x = [0.0] * row_count
        call_minor_x = [0.0] * row_count
        put_minor_x = [0.0] * row_count
        call_major_line_widths = [0] * row_count
        put_major_line_widths = [0] * row_count
        call_minor_line_widths = [0] * row_count
        put_minor_line_widths = [0] * row_count

        for idx, (call_val, put_val) in enumerate(zip(call_volume, put_volume)):
            if call_val >= put_val:
                if show_calls and call_val > 0:
                    call_major_x[idx] = -call_val
                    call_major_line_widths[idx] = 4 if highlight_max_level and idx == poc_idx else 0
                if show_puts and put_val > 0:
                    put_minor_x[idx] = -put_val
                    put_minor_line_widths[idx] = 4 if highlight_max_level and idx == poc_idx else 0
            else:
                if show_puts and put_val > 0:
                    put_major_x[idx] = -put_val
                    put_major_line_widths[idx] = 4 if highlight_max_level and idx == poc_idx else 0
                if show_calls and call_val > 0:
                    call_minor_x[idx] = -call_val
                    call_minor_line_widths[idx] = 4 if highlight_max_level and idx == poc_idx else 0

        def _add_overlay_volume_trace(name, x_values, colors, hovertemplate, line_widths, show_in_legend):
            if not any(abs(val) > 0 for val in x_values):
                return
            fig.add_trace(go.Bar(
                y=all_strikes_list,
                x=x_values,
                name=name,
                legendgroup=name,
                showlegend=show_in_legend,
                marker=dict(
                    color=colors,
                    line=dict(width=line_widths, color=max_level_color),
                ),
                customdata=customdata,
                text=[''] * row_count,
                textposition='inside',
                insidetextanchor='middle',
                textfont=dict(size=10, color='#E5E7EB'),
                orientation='h',
                hovertemplate=hovertemplate,
            ))

        _add_overlay_volume_trace(
            'Calls',
            call_major_x,
            call_colors,
            'Strike: %{y}<br>Calls: %{customdata[0]}<br>Puts: %{customdata[1]}<br>Total: %{customdata[2]}<extra></extra>',
            call_major_line_widths,
            not any(abs(val) > 0 for val in call_minor_x),
        )
        _add_overlay_volume_trace(
            'Puts',
            put_major_x,
            put_colors,
            'Strike: %{y}<br>Puts: %{customdata[1]}<br>Calls: %{customdata[0]}<br>Total: %{customdata[2]}<extra></extra>',
            put_major_line_widths,
            not any(abs(val) > 0 for val in put_minor_x),
        )
        _add_overlay_volume_trace(
            'Calls',
            call_minor_x,
            call_colors,
            'Strike: %{y}<br>Calls: %{customdata[0]}<br>Puts: %{customdata[1]}<br>Total: %{customdata[2]}<extra></extra>',
            call_minor_line_widths,
            any(abs(val) > 0 for val in call_minor_x),
        )
        _add_overlay_volume_trace(
            'Puts',
            put_minor_x,
            put_colors,
            'Strike: %{y}<br>Puts: %{customdata[1]}<br>Calls: %{customdata[0]}<br>Total: %{customdata[2]}<extra></extra>',
            put_minor_line_widths,
            any(abs(val) > 0 for val in put_minor_x),
        )
    else:
        # Add call volume bars
        if show_calls and all_strikes_list:
            call_colors = get_colors(call_color, call_volume, max_side_volume, coloring_mode)
            fig.add_trace(go.Bar(
                x=all_strikes_list,
                y=call_volume,
                name='Calls',
                marker_color=call_colors,
                customdata=customdata,
                text=call_text,
                textposition='inside',
                insidetextanchor='middle',
                textfont=dict(size=10, color='#E5E7EB'),
                hovertemplate='Strike: %{x}<br>Calls: %{customdata[0]}<br>Puts: %{customdata[1]}<br>Total: %{customdata[2]}<extra></extra>',
                marker_line_width=0
            ))

        # Add put volume bars mirrored from zero so the split is visible at each strike.
        if show_puts and all_strikes_list:
            put_colors = get_colors(put_color, put_volume, max_side_volume, coloring_mode)
            fig.add_trace(go.Bar(
                x=all_strikes_list,
                y=[-v for v in put_volume],
                name='Puts',
                marker_color=put_colors,
                customdata=customdata,
                text=put_text,
                textposition='inside',
                insidetextanchor='middle',
                textfont=dict(size=10, color='#E5E7EB'),
                hovertemplate='Strike: %{x}<br>Puts: %{customdata[1]}<br>Calls: %{customdata[0]}<br>Total: %{customdata[2]}<extra></extra>',
                marker_line_width=0
            ))

    # Add a lighter net overlay so the split profile stays primary.
    if show_net and all_strikes_list:
        net_colors = get_net_colors(net_volume, max_net_volume, call_color, put_color, coloring_mode)
        if horizontal:
            marker_sizes = [
                6 + (10 * (abs(val) / max_net_volume if max_net_volume else 0))
                for val in net_volume
            ]
            fig.add_trace(go.Scatter(
                y=all_strikes_list,
                x=[-total if total > 0 else None for total in total_volume],
                name='Net',
                mode='markers',
                customdata=customdata,
                marker=dict(
                    color=net_colors,
                    size=marker_sizes,
                    symbol='circle',
                    line=dict(color='rgba(255,255,255,0.18)', width=1),
                ),
                hovertemplate='Strike: %{y}<br>Net: %{customdata[3]}<br>Calls: %{customdata[0]}<br>Puts: %{customdata[1]}<extra></extra>',
            ))
        else:
            fig.add_trace(go.Bar(
                x=all_strikes_list,
                y=net_volume,
                name='Net',
                marker_color=net_colors,
                text=[format_large_number(val) for val in net_volume],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Net Volume: %{y:,.0f}<extra></extra>',
                marker_line_width=0,
                opacity=0.42
            ))
    
    if horizontal:
        # Add current price line
        fig.add_hline(
            y=S,
            line_dash="dash",
            line_color="white",
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="right",
            annotation_font_color="white",
            line_width=1
        )
    else:
        # Add current price line
        fig.add_vline(
            x=S,
            line_dash="dash",
            line_color="white",
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="top",
            annotation_font_color="white",
            line_width=1
        )
    
    # Add expiry info to title if multiple expiries are selected
    chart_title = 'Options Volume by Strike'
    if selected_expiries and len(selected_expiries) > 1:
        chart_title = f"Options Volume by Strike ({len(selected_expiries)} expiries)"
    
    xaxis_config = dict(
        title='',
        title_font=dict(color='#CCCCCC'),
        tickfont=dict(color='#CCCCCC'),
        gridcolor='#333333',
        linecolor='#333333',
        showgrid=False,
        zeroline=True,
        zerolinecolor='#333333',
        automargin=True
    )
    
    yaxis_config = dict(
        title='',
        title_font=dict(color='#CCCCCC'),
        tickfont=dict(color='#CCCCCC'),
        gridcolor='#333333',
        linecolor='#333333',
        showgrid=False,
        zeroline=True,
        zerolinecolor='#333333'
    )
    
    if horizontal:
        x_padding = max_side_volume * 1.08
        xaxis_config.update(dict(
            range=[-x_padding, 0],
            autorange=False,
            tickvals=[-max_side_volume, -(max_side_volume / 2.0), 0],
            ticktext=[
                format_large_number(max_side_volume),
                format_large_number(max_side_volume / 2.0),
                '0',
            ],
            zeroline=False,
        ))
        yaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False,
            tickvals=all_strikes_list,
            ticktext=strike_ticktext,
        ))
    else:
        xaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False,
            tickangle=45,
            tickformat='.0f',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor='#CCCCCC'
        ))

    # Update layout
    fig.update_layout(
        title=dict(
            text=chart_title,
            font=dict(color='#CCCCCC', size=16),
            x=0.5,
            xanchor='center'
        ),
        xaxis=xaxis_config,
        yaxis=yaxis_config,
        barmode='overlay' if horizontal else 'relative',
        hovermode='y unified' if horizontal else 'x unified',
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='#CCCCCC'),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=0.95,
            xanchor="right",
            x=1,
            font=dict(color='#CCCCCC'),
            bgcolor=PLOT_THEME['paper_bgcolor']
        ),
        bargap=0.14 if horizontal else 0.1,
        bargroupgap=0.1,
        margin=dict(l=40 if horizontal else 50, r=82 if horizontal else 50, t=50, b=(40 if horizontal else 100)),
        hoverlabel=dict(
            bgcolor=PLOT_THEME['paper_bgcolor'],
            font_size=12,
            font_family="Arial"
        ),
        spikedistance=1000,
        hoverdistance=100,
        showlegend=True,
        height=500
    )

    if horizontal and all_strikes_list:
        annotations = list(fig.layout.annotations) if fig.layout.annotations else []
        def _append_strike_badge(idx, text, border_color, yshift=0):
            if idx is None or idx < 0 or idx >= len(all_strikes_list):
                return
            annotations.append(dict(
                xref='paper',
                x=0.02,
                yref='y',
                y=all_strikes_list[idx],
                text=text,
                showarrow=False,
                xanchor='left',
                yanchor='middle',
                yshift=yshift,
                font=dict(size=9, color='#E5E7EB'),
                bgcolor=PLOT_THEME['paper_bgcolor'],
                bordercolor=border_color,
                borderwidth=1.5,
                borderpad=3,
                opacity=0.98,
            ))

        if atm_idx is not None and atm_idx == poc_idx and highlight_max_level:
            _append_strike_badge(atm_idx, 'ATM · POC', max_level_color)
        else:
            if highlight_max_level and poc_idx is not None:
                _append_strike_badge(poc_idx, 'POC', max_level_color, -10 if atm_idx is not None and atm_idx != poc_idx else 0)
            if atm_idx is not None:
                _append_strike_badge(atm_idx, 'ATM', '#F59E0B', 10 if highlight_max_level and poc_idx is not None and atm_idx != poc_idx else 0)
        fig.update_layout(annotations=annotations)
    
    # Add hover spikes
    fig.update_xaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    fig.update_yaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    
    # Logic for Highlighting Max Level
    if highlight_max_level and not horizontal:
        try:
            if max_level_mode == 'Net':
                net_trace_idx = next((i for i, t in enumerate(fig.data) if t.name == 'Net'), None)
                if net_trace_idx is not None:
                    raw = fig.data[net_trace_idx].x if horizontal else fig.data[net_trace_idx].y
                    if raw:
                        vals = list(raw)
                        total_net = sum(vals)
                        if total_net >= 0:
                            max_bar_idx = vals.index(max(vals))
                        else:
                            max_bar_idx = vals.index(min(vals))
                        net_trace = fig.data[net_trace_idx]
                        if net_trace.type == 'scatter':
                            base_sizes = list(net_trace.marker.size) if hasattr(net_trace.marker, 'size') else [8] * len(vals)
                            boosted_sizes = [float(size) for size in base_sizes]
                            boosted_sizes[max_bar_idx] = boosted_sizes[max_bar_idx] + 6
                            line_widths = [1] * len(vals)
                            line_widths[max_bar_idx] = 3
                            net_trace.update(marker=dict(
                                size=boosted_sizes,
                                line=dict(width=line_widths, color=max_level_color)
                            ))
                        else:
                            line_widths = [0] * len(vals)
                            line_widths[max_bar_idx] = 5
                            net_trace.update(marker=dict(
                                line=dict(width=line_widths, color=max_level_color)
                            ))
            else:
                max_abs_val = 0
                max_trace_idx = -1
                max_bar_idx = -1
                for i, trace in enumerate(fig.data):
                    if trace.type == 'bar':
                        vals = trace.x if horizontal else trace.y
                        if vals:
                            abs_vals = [abs(v) for v in vals]
                            if abs_vals:
                                local_max = max(abs_vals)
                                if local_max > max_abs_val:
                                    max_abs_val = local_max
                                    max_trace_idx = i
                                    max_bar_idx = abs_vals.index(local_max)
                    elif trace.type == 'scatter' and trace.name == 'Net':
                        vals = trace.x if horizontal else trace.y
                        if vals:
                            abs_vals = [abs(v) for v in vals]
                            if abs_vals:
                                local_max = max(abs_vals)
                                if local_max > max_abs_val:
                                    max_abs_val = local_max
                                    max_trace_idx = i
                                    max_bar_idx = abs_vals.index(local_max)
                if max_trace_idx != -1:
                    trace = fig.data[max_trace_idx]
                    vals = trace.x if horizontal else trace.y
                    if trace.type == 'scatter':
                        base_sizes = list(trace.marker.size) if hasattr(trace.marker, 'size') else [8] * len(vals)
                        boosted_sizes = [float(size) for size in base_sizes]
                        boosted_sizes[max_bar_idx] = boosted_sizes[max_bar_idx] + 6
                        line_widths = [1] * len(vals)
                        line_widths[max_bar_idx] = 3
                        trace.update(marker=dict(
                            size=boosted_sizes,
                            line=dict(width=line_widths, color=max_level_color)
                        ))
                    else:
                        line_widths = [0] * len(vals)
                        line_widths[max_bar_idx] = 5
                        trace.update(marker=dict(
                            line=dict(width=line_widths, color=max_level_color)
                        ))
        except Exception as e:
            print(f"Error highlighting max level in options volume: {e}")

    apply_plotly_theme(fig)
    return fig.to_json()

def update_options_chain(ticker, expiration_date=None):
    """Update the options chain by fetching new data from the API"""
    global current_chain, last_update_time, current_ticker, current_expiry
    
    current_time = time.time()
    if current_time - last_update_time < 1.0:  # Enforce 1 second minimum between API calls
        return  # Don't update if less than 1 second has passed
        
    try:
        # Fetch new options chain data (default to OI-weighted exposures for background cache)
        new_chain = fetch_options_for_date(ticker, expiration_date, exposure_metric="Open Interest")
        if new_chain and not new_chain[0].empty and not new_chain[1].empty:
            current_chain = {
                'calls': new_chain[0].to_dict('records'),
                'puts': new_chain[1].to_dict('records')
            }
            last_update_time = current_time
            current_ticker = ticker
            current_expiry = expiration_date
    except Exception as e:
        print(f"Error updating options chain: {e}")

def aggregate_candles_to_timeframe(candles, timeframe_minutes):
    """Aggregate minute candles into ET-aligned buckets for unsupported chart intervals."""
    if timeframe_minutes <= 1 or not candles:
        return candles

    tz = pytz.timezone('US/Eastern')
    buckets = {}
    for candle in candles:
        et = datetime.fromtimestamp(candle['datetime'] / 1000, tz)
        day_start = et.replace(hour=0, minute=0, second=0, microsecond=0)
        if timeframe_minutes >= 1440:
            bucket_key = day_start
        else:
            minutes_since_midnight = et.hour * 60 + et.minute
            bucket_minute = (minutes_since_midnight // timeframe_minutes) * timeframe_minutes
            bucket_key = day_start + timedelta(minutes=bucket_minute)
        buckets.setdefault(bucket_key, []).append(candle)

    result = []
    for bucket_key in sorted(buckets.keys()):
        group = buckets[bucket_key]
        result.append({
            'datetime': int(bucket_key.timestamp() * 1000),
            'open': group[0]['open'],
            'high': max(c['high'] for c in group),
            'low': min(c['low'] for c in group),
            'close': group[-1]['close'],
            'volume': sum(c.get('volume', 0) for c in group)
        })
    return result

def get_price_history(ticker, timeframe=1):
    if ticker == "MARKET":
        ticker = "$SPX"
    elif ticker == "MARKET2":
        ticker = "SPY"
    try:
        # Get current time in EST
        est = datetime.now(pytz.timezone('US/Eastern'))
        current_date = est.date()

        # Map timeframe (minutes) -> trading-day lookback. Finer timeframes get less
        # history to keep payload size bounded; coarser timeframes stretch further back
        # so the chart shows meaningful structure without wasting bandwidth on 1-min bars.
        PERIOD_BY_TF = {
            1: 5,
            2: 10,
            3: 10,
            5: 20,
            10: 20,
            15: 30,
            30: 30,
            60: 90,
            240: 120,
            1440: 180,
        }
        period_days = PERIOD_BY_TF.get(timeframe, 20)

        # +5 calendar-day cushion covers weekends/holidays that filter_market_hours() drops.
        start_date = datetime.combine(current_date - timedelta(days=period_days + 5), datetime.min.time())
        end_date = datetime.combine(current_date + timedelta(days=1), datetime.min.time())

        # Schwab API only supports minute frequencies: 1, 5, 10, 15, 30.
        # Unsupported chart intervals are derived from a smaller native fetch.
        if timeframe in (1, 5, 10, 15, 30):
            api_frequency = timeframe
        elif timeframe in (2, 3):
            api_frequency = 1
        else:
            api_frequency = 30

        # Schwab's `period` param strict-validates to {1,2,3,4,5,10} for periodType="day".
        # startDate/endDate drive the actual fetch window, so cap `period` at 10 to stay
        # within the enum while letting `period_days` control lookback above.
        api_period = min(period_days, 10)

        # Convert dates to milliseconds since epoch
        response = client.price_history(
            symbol=ticker,
            periodType="day",
            period=api_period,
            frequencyType="minute",
            frequency=api_frequency,
            startDate=int(start_date.timestamp() * 1000),
            endDate=int(end_date.timestamp() * 1000),
            needExtendedHoursData=True
        )
        
        if not response.ok:
            raise Exception(f"Failed to fetch price history: {response.status_code} {response.reason}")

        data = response.json()
        if not data or 'candles' not in data:
            raise Exception("Malformed price history data from Schwab API")

        # Filter for market hours
        candles = filter_market_hours(data['candles'])
        if not candles:
            raise Exception("No market-hour candles returned from Schwab API")

        # Sort candles by timestamp
        candles.sort(key=lambda x: x['datetime'])

        if timeframe not in (1, 5, 10, 15, 30):
            candles = aggregate_candles_to_timeframe(candles, timeframe)

        # Get previous trading day's close
        prev_day_candles = []
        for candle in reversed(candles):
            candle_time = datetime.fromtimestamp(candle['datetime']/1000, pytz.timezone('US/Eastern'))
            if candle_time.date() < current_date:
                prev_day_candles.append(candle)
                if len(prev_day_candles) >= 30:  # Get at least 30 minutes of data
                    break

        # Get the last candle of the previous trading day
        prev_day_close = prev_day_candles[-1]['close'] if prev_day_candles else None

        return {
            'candles': candles,
            'prev_day_close': prev_day_close
        }
    except Exception as e:
        msg = f"[DEBUG] Error fetching price history: {e}"
        print(msg)
        raise Exception(msg)

def filter_market_hours(candles):
    """Filter candles to trading-day hours: pre-market 04:00 ET through after-hours 20:00 ET.
    Overnight gap (20:00-04:00) is excluded because Schwab priceHistory returns no bars there."""
    filtered_candles = []
    for candle in candles:
        dt = datetime.fromtimestamp(candle['datetime']/1000)
        # Convert to Eastern Time
        et = dt.astimezone(pytz.timezone('US/Eastern'))
        # Check if it's a weekday and within extended trading hours
        if et.weekday() < 5:  # 0-4 is Monday-Friday
            session_open  = et.replace(hour=4,  minute=0, second=0, microsecond=0)
            session_close = et.replace(hour=20, minute=0, second=0, microsecond=0)
            if session_open <= et <= session_close:
                filtered_candles.append(candle)
    return filtered_candles


DEFAULT_SESSION_LEVEL_CONFIG = {
    'enabled': False,
    'today': True,
    'yesterday': True,
    'near_open': False,
    'premarket': True,
    'after_hours': True,
    'opening_range': False,
    'initial_balance': True,
    'show_or_mid': True,
    'show_or_cloud': False,
    'show_ib_mid': True,
    'show_ib_cloud': False,
    'show_ib_extensions': True,
    'near_open_minutes': 60,
    'opening_range_minutes': 15,
    'ib_start': '09:30',
    'ib_end': '10:30',
    'abbreviate_labels': True,
    'append_price': True,
    'today_rth_only': True,
    'yesterday_rth_only': True,
}


def _coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _normalize_hhmm(value, default):
    if not value:
        return default
    try:
        hour_text, minute_text = str(value).split(':', 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f'{hour:02d}:{minute:02d}'
    except Exception:
        pass
    return default


def _hhmm_to_minutes(value):
    hour_text, minute_text = str(value).split(':', 1)
    return int(hour_text) * 60 + int(minute_text)


def normalize_session_level_config(config=None):
    out = dict(DEFAULT_SESSION_LEVEL_CONFIG)
    if not isinstance(config, dict):
        return out

    bool_keys = (
        'enabled', 'today', 'yesterday', 'near_open', 'premarket', 'after_hours',
        'opening_range', 'initial_balance', 'show_or_mid',
        'show_or_cloud', 'show_ib_mid', 'show_ib_cloud',
        'show_ib_extensions', 'abbreviate_labels', 'append_price',
        'today_rth_only', 'yesterday_rth_only',
    )
    for key in bool_keys:
        if key in config:
            out[key] = _coerce_bool(config.get(key), out[key])

    try:
        out['near_open_minutes'] = max(0, min(330, int(config.get('near_open_minutes', out['near_open_minutes']))))
    except Exception:
        pass

    try:
        out['opening_range_minutes'] = max(1, min(60, int(config.get('opening_range_minutes', out['opening_range_minutes']))))
    except Exception:
        pass

    out['ib_start'] = _normalize_hhmm(config.get('ib_start'), out['ib_start'])
    out['ib_end'] = _normalize_hhmm(config.get('ib_end'), out['ib_end'])
    if _hhmm_to_minutes(out['ib_end']) <= _hhmm_to_minutes(out['ib_start']):
        out['ib_start'] = DEFAULT_SESSION_LEVEL_CONFIG['ib_start']
        out['ib_end'] = DEFAULT_SESSION_LEVEL_CONFIG['ib_end']
    return out


def get_session_level_candles(ticker, lookback_days=5):
    """Fetch raw 1-minute candles for session-level calculations."""
    if ticker == "MARKET":
        ticker = "$SPX"
    elif ticker == "MARKET2":
        ticker = "SPY"

    est = datetime.now(pytz.timezone('US/Eastern'))
    current_date = est.date()
    lookback_days = max(2, min(int(lookback_days or 5), 10))
    start_date = datetime.combine(current_date - timedelta(days=lookback_days + 5), datetime.min.time())
    end_date = datetime.combine(current_date + timedelta(days=1), datetime.min.time())

    response = client.price_history(
        symbol=ticker,
        periodType="day",
        period=min(lookback_days, 10),
        frequencyType="minute",
        frequency=1,
        startDate=int(start_date.timestamp() * 1000),
        endDate=int(end_date.timestamp() * 1000),
        needExtendedHoursData=True
    )
    if not response.ok:
        raise Exception(f"Failed to fetch session candles: {response.status_code} {response.reason}")

    data = response.json()
    candles = filter_market_hours((data or {}).get('candles') or [])
    candles.sort(key=lambda candle: candle['datetime'])
    return candles


def _build_session_level(price, short_label, full_label, group):
    if price is None or not np.isfinite(price):
        return None
    return {
        'price': round(float(price), 2),
        'label': short_label,
        'short_label': short_label,
        'full_label': full_label,
        'group': group,
    }


def compute_session_levels(candles_1m, *, anchor_date=None, timezone='US/Eastern', config=None):
    cfg = normalize_session_level_config(config)
    tz = pytz.timezone(timezone)
    rows_by_ts = {}
    for candle in candles_1m or []:
        try:
            ts_ms = int(candle['datetime'])
            dt_et = datetime.fromtimestamp(ts_ms / 1000, tz)
            rows_by_ts[ts_ms] = {
                'ts_ms': ts_ms,
                'dt': dt_et,
                'date': dt_et.date(),
                'minute': dt_et.hour * 60 + dt_et.minute,
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
            }
        except Exception:
            continue

    rows = [rows_by_ts[key] for key in sorted(rows_by_ts)]
    if not rows:
        return {
            'meta': {
                'anchor_date': None,
                'timezone': timezone,
                'overnight_supported': False,
                'source_frequency_minutes': 1,
            }
        }

    available_dates = sorted({row['date'] for row in rows})
    resolved_anchor_date = anchor_date or available_dates[-1]
    if isinstance(resolved_anchor_date, str):
        try:
            resolved_anchor_date = datetime.strptime(resolved_anchor_date, '%Y-%m-%d').date()
        except Exception:
            resolved_anchor_date = available_dates[-1]
    if resolved_anchor_date not in available_dates:
        resolved_anchor_date = available_dates[-1]

    date_index = available_dates.index(resolved_anchor_date)
    previous_trading_date = available_dates[date_index - 1] if date_index > 0 else None
    def rows_for(session_date, start_minute=None, end_minute=None):
        if not session_date:
            return []
        subset = [row for row in rows if row['date'] == session_date]
        if start_minute is not None:
            subset = [row for row in subset if row['minute'] >= start_minute]
        if end_minute is not None:
            subset = [row for row in subset if row['minute'] < end_minute]
        return subset

    def session_high(subset):
        return max((row['high'] for row in subset), default=None)

    def session_low(subset):
        return min((row['low'] for row in subset), default=None)

    def session_open(subset):
        return subset[0]['open'] if subset else None

    def session_close(subset):
        return subset[-1]['close'] if subset else None

    out = {}

    today_start = 9 * 60 + 30 if cfg['today_rth_only'] else 4 * 60
    today_end = 16 * 60 if cfg['today_rth_only'] else 20 * 60
    today_rows = rows_for(resolved_anchor_date, today_start, today_end)
    out['today_high'] = _build_session_level(session_high(today_rows), 'TDH', 'Today High', 'today')
    out['today_low'] = _build_session_level(session_low(today_rows), 'TDL', 'Today Low', 'today')
    out['today_open'] = _build_session_level(session_open(today_rows), 'TDO', 'Today Open', 'today')

    yesterday_start = 9 * 60 + 30 if cfg['yesterday_rth_only'] else 4 * 60
    yesterday_end = 16 * 60 if cfg['yesterday_rth_only'] else 20 * 60
    yesterday_rows = rows_for(previous_trading_date, yesterday_start, yesterday_end)
    out['yesterday_high'] = _build_session_level(session_high(yesterday_rows), 'YDH', 'Yesterday High', 'yesterday')
    out['yesterday_low'] = _build_session_level(session_low(yesterday_rows), 'YDL', 'Yesterday Low', 'yesterday')
    out['yesterday_open'] = _build_session_level(session_open(yesterday_rows), 'YDO', 'Yesterday Open', 'yesterday')
    out['yesterday_close'] = _build_session_level(session_close(yesterday_rows), 'YDC', 'Yesterday Close', 'yesterday')

    premarket_rows = rows_for(resolved_anchor_date, 4 * 60, 9 * 60 + 30)
    near_open_start = max(4 * 60, (9 * 60 + 30) - int(cfg['near_open_minutes']))
    near_open_rows = rows_for(resolved_anchor_date, near_open_start, 9 * 60 + 30)
    out['near_open_high'] = _build_session_level(session_high(near_open_rows), 'NOH', 'Near Open High', 'near_open')
    out['near_open_low'] = _build_session_level(session_low(near_open_rows), 'NOL', 'Near Open Low', 'near_open')
    out['premarket_high'] = _build_session_level(session_high(premarket_rows), 'PMH', 'Premarket High', 'premarket')
    out['premarket_low'] = _build_session_level(session_low(premarket_rows), 'PML', 'Premarket Low', 'premarket')

    anchor_session_rows = rows_for(resolved_anchor_date)
    anchor_latest_row = anchor_session_rows[-1] if anchor_session_rows else None
    after_hours_date = resolved_anchor_date if anchor_latest_row and anchor_latest_row['minute'] >= 16 * 60 else previous_trading_date
    after_hours_rows = rows_for(after_hours_date, 16 * 60, 20 * 60)
    out['after_hours_high'] = _build_session_level(session_high(after_hours_rows), 'AHH', 'After Hours High', 'after_hours')
    out['after_hours_low'] = _build_session_level(session_low(after_hours_rows), 'AHL', 'After Hours Low', 'after_hours')

    opening_range_end = 9 * 60 + 30 + int(cfg['opening_range_minutes'])
    opening_range_rows = rows_for(resolved_anchor_date, 9 * 60 + 30, opening_range_end)
    opening_range_high = session_high(opening_range_rows)
    opening_range_low = session_low(opening_range_rows)
    out['opening_range_high'] = _build_session_level(opening_range_high, 'ORH', 'Opening Range High', 'opening_range')
    out['opening_range_low'] = _build_session_level(opening_range_low, 'ORL', 'Opening Range Low', 'opening_range')
    if opening_range_high is not None and opening_range_low is not None:
        out['opening_range_mid'] = _build_session_level((opening_range_high + opening_range_low) / 2.0, 'ORM', 'Opening Range Mid', 'opening_range')

    ib_start = _hhmm_to_minutes(cfg['ib_start'])
    ib_end = _hhmm_to_minutes(cfg['ib_end'])
    ib_rows = rows_for(resolved_anchor_date, ib_start, ib_end)
    ib_high = session_high(ib_rows)
    ib_low = session_low(ib_rows)
    if ib_high is not None and ib_low is not None:
        ib_range = ib_high - ib_low
        out['ib_high'] = _build_session_level(ib_high, 'IBH', 'Initial Balance High', 'initial_balance')
        out['ib_low'] = _build_session_level(ib_low, 'IBL', 'Initial Balance Low', 'initial_balance')
        out['ib_mid'] = _build_session_level((ib_high + ib_low) / 2.0, 'IBM', 'Initial Balance Mid', 'initial_balance')
        out['ib_high_x2'] = _build_session_level(ib_high + ib_range, 'IBHx2', 'Initial Balance High x2', 'initial_balance_ext')
        out['ib_low_x2'] = _build_session_level(ib_low - ib_range, 'IBLx2', 'Initial Balance Low x2', 'initial_balance_ext')
        out['ib_high_x3'] = _build_session_level(ib_high + (2 * ib_range), 'IBHx3', 'Initial Balance High x3', 'initial_balance_ext')
        out['ib_low_x3'] = _build_session_level(ib_low - (2 * ib_range), 'IBLx3', 'Initial Balance Low x3', 'initial_balance_ext')

    out['meta'] = {
        'anchor_date': resolved_anchor_date.isoformat() if hasattr(resolved_anchor_date, 'isoformat') else str(resolved_anchor_date),
        'timezone': timezone,
        'overnight_supported': False,
        'source_frequency_minutes': 1,
    }
    return out

def convert_to_heikin_ashi(candles):
    """Convert regular OHLC candles to Heikin-Ashi candles"""
    if not candles:
        return []
    
    ha_candles = []
    prev_ha_open = None
    prev_ha_close = None
    
    for candle in candles:
        # Calculate Heikin-Ashi values
        ha_close = (candle['open'] + candle['high'] + candle['low'] + candle['close']) / 4
        
        if prev_ha_open is None:
            # First candle: HA_Open = (Open + Close) / 2
            ha_open = (candle['open'] + candle['close']) / 2
        else:
            # Subsequent candles: HA_Open = (Previous HA_Open + Previous HA_Close) / 2
            ha_open = (prev_ha_open + prev_ha_close) / 2
        
        ha_high = max(candle['high'], ha_open, ha_close)
        ha_low = min(candle['low'], ha_open, ha_close)
        
        # Create new candle with Heikin-Ashi values
        ha_candle = {
            'datetime': candle['datetime'],
            'open': ha_open,
            'high': ha_high,
            'low': ha_low,
            'close': ha_close,
            'volume': candle['volume']
        }
        
        ha_candles.append(ha_candle)
        
        # Store values for next iteration
        prev_ha_open = ha_open
        prev_ha_close = ha_close
    
    return ha_candles

def create_price_chart(price_data, calls=None, puts=None, exposure_levels_types=[], exposure_levels_count=3, call_color=CALL_COLOR, put_color=PUT_COLOR, strike_range=0.02, use_heikin_ashi=False, highlight_max_level=False, max_level_color='#800080', coloring_mode='Linear Intensity'):
    # Handle backward compatibility or empty default
    if isinstance(exposure_levels_types, str):
        if exposure_levels_types == 'None':
            exposure_levels_types = []
        else:
            exposure_levels_types = [exposure_levels_types]
            
    if not price_data or 'candles' not in price_data or not price_data['candles']:
        return go.Figure().to_json()
    
    # Filter for market hours
    candles = filter_market_hours(price_data['candles'])
    if not candles:
        return go.Figure().to_json()
    
    # Get current time in EST
    est = datetime.now(pytz.timezone('US/Eastern'))
    current_date = est.date()
    
    # Sort candles by datetime and remove duplicates
    unique_candles = {}
    for candle in candles:
        candle_time = datetime.fromtimestamp(candle['datetime']/1000, pytz.timezone('US/Eastern'))
        unique_candles[candle_time] = candle
    
    # Convert back to list and sort
    sorted_candles = sorted(unique_candles.items(), key=lambda x: x[0])
    all_candles = [candle for _, candle in sorted_candles]
    
    # Filter for current day's candles only
    current_day_candles = []
    for candle in all_candles:
        candle_time = datetime.fromtimestamp(candle['datetime']/1000, pytz.timezone('US/Eastern'))
        # Convert both dates to EST and compare
        candle_date = candle_time.date()
        if candle_date == current_date:
            current_day_candles.append(candle)
    
    # If no current day candles, use the most recent day's candles
    if not current_day_candles:
        # Get the most recent trading day
        most_recent_day = max(candle['datetime'] for candle in all_candles)
        most_recent_day = datetime.fromtimestamp(most_recent_day/1000, pytz.timezone('US/Eastern')).date()
        
        # Filter candles for most recent trading day
        current_day_candles = []
        for candle in all_candles:
            candle_time = datetime.fromtimestamp(candle['datetime']/1000, pytz.timezone('US/Eastern'))
            if candle_time.date() == most_recent_day:
                current_day_candles.append(candle)
    
    # Use all candles for calculations but current day candles for display
    if use_heikin_ashi:
        ha_candles = convert_to_heikin_ashi(all_candles)  # Use all candles for calculations
        display_candles = convert_to_heikin_ashi(current_day_candles)  # Use current day for display
    else:
        # Use regular candles
        ha_candles = all_candles
        display_candles = current_day_candles
    
    # Get previous day's close
    previous_day_close = None
    for candle in reversed(all_candles):
        candle_time = datetime.fromtimestamp(candle['datetime']/1000, pytz.timezone('US/Eastern'))
        if candle_time.date() < current_date:
            previous_day_close = candle['close']
            break
    
    if previous_day_close is None:
        previous_day_close = display_candles[0]['close'] if display_candles else 0
    
    dates = [datetime.fromtimestamp(candle['datetime']/1000) for candle in display_candles]
    opens = [candle['open'] for candle in display_candles]
    highs = [candle['high'] for candle in display_candles]
    lows = [candle['low'] for candle in display_candles]
    closes = [candle['close'] for candle in display_candles]
    volumes = [candle['volume'] for candle in display_candles]
    

    
    # Calculate price range for proper scaling
    if not lows or not highs:  # Check if lists are empty
        return go.Figure().to_json()
        
    price_min = min(lows)
    price_max = max(highs)
    price_range = price_max - price_min
    padding = price_range * 0.02  # 2% padding
    
    # Get current price for strike range calculation
    current_price = closes[-1] if closes else (price_min + price_max) / 2
    
    # Determine if last candle is up or down
    last_candle_up = closes[-1] >= opens[-1] if len(closes) > 0 else True
    current_price_color = call_color if last_candle_up else put_color
    
    # Calculate strike range boundaries
    min_strike = current_price * (1 - strike_range)
    max_strike = current_price * (1 + strike_range)
    
    # Create figure with subplots
    fig = go.Figure()
    
    # Add candlestick trace to the first subplot
    fig.add_trace(go.Candlestick(
        x=dates,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        name='OHLC',
        increasing_line_color=call_color,
        decreasing_line_color=put_color,
        increasing_fillcolor=call_color,
        decreasing_fillcolor=put_color
    ))
    
    # Modify the volume trace coloring
    volume_colors = []
    for i in range(len(closes)):
        if i == 0:
            # For first candle, compare close to open
            is_up = closes[i] >= opens[i]
        else:
            # For other candles, compare to previous close
            is_up = closes[i] >= closes[i-1]
        # Use call_color for up volume and put_color for down volume
        volume_colors.append(call_color if is_up else put_color)
    
    # Update the volume trace with the new colors
    fig.add_trace(go.Bar(
        x=dates,
        y=volumes,
        name='Volume',
        marker_color=volume_colors,
        marker_line_width=0,
        yaxis='y2',
        opacity=0.7  # Add some transparency
    ))
    

    
    # Update layout with subplots
    chart_title = 'Price Chart (Heikin-Ashi)' if use_heikin_ashi else 'Price Chart'
    fig.update_layout(
        title=dict(
            text=chart_title,
            font=dict(color='#CCCCCC', size=16),
            x=0.5,
            xanchor='center',
            y=0.98
        ),
        xaxis=dict(
            title='',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333',
            rangeslider=dict(visible=False),
            tickformat='%H:%M',
            showline=True,
            linewidth=1,
            mirror=True,
            domain=[0, 1]
        ),
        yaxis=dict(
            title='Price',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333',
            showline=True,
            linewidth=1,
            mirror=True,
            autorange=True,  # Enable auto-scaling
            domain=[0.25, 1],  # Price takes up 75% of the space
            side='right',  # Move axis to right side
            title_standoff=0,  # Reduce space between title and axis
            automargin=True  # Enable automatic margin adjustment
        ),
        yaxis2=dict(
            title='Volume',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333',
            showline=True,
            linewidth=1,
            mirror=True,
            domain=[0, 0.2],  # Volume takes up 20% of the space
            side='right',  # Move axis to right side
            title_standoff=0,  # Reduce space between title and axis
            automargin=True  # Enable automatic margin adjustment
        ),

        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='#CCCCCC'),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color='#CCCCCC'),
            bgcolor=PLOT_THEME['paper_bgcolor']
        ),
        bargap=0.1,
        bargroupgap=0.1,
        margin=dict(l=50, r=120, t=30, b=20),  # Increased right margin further
        hovermode='x unified',
        showlegend=True,
        height=550,  # Increased height for better visibility
        dragmode='pan',  # Set default tool to pan
        # Add current price annotation
        annotations=[
            dict(
                x=1,
                y=current_price,
                xref="paper",
                yref="y",
                text=f"${current_price:.2f}",
                showarrow=False,
                font=dict(
                    size=10,
                    color=current_price_color
                ),
                bgcolor=PLOT_THEME['paper_bgcolor'],
                bordercolor=current_price_color,
                borderwidth=1,
                borderpad=2,
                xanchor='left',
                yanchor='middle',
                xshift=1  # Moved left
            )
        ]
    )
    
    # Logic to add Exposure Levels to Price Chart
    if exposure_levels_types and calls is not None and puts is not None:
        # Filter options within strike range for better visualization
        range_calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)]
        range_puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)]
        
        # Define dash styles to differentiate if multiple types are selected
        dash_styles = ['dot', 'dash', 'longdash', 'dashdot', 'longdashdot']
        
        # Pre-calculate all top levels to find the overall absolute maximum for highlighting
        all_top_levels = [] # List of (strike, value, type_name, type_index)
        
        for i, exposure_levels_type in enumerate(exposure_levels_types):
            # --- Expected Move Chart Level ---
            if exposure_levels_type.lower() == 'expected move':
                expected_move_snapshot = calculate_expected_move_snapshot(calls, puts, current_price)
                if expected_move_snapshot:
                    upper = expected_move_snapshot['upper']
                    lower = expected_move_snapshot['lower']
                    em_color = '#036bfc'
                    # Plot dashed lines for expected move in #036bfc
                    fig.add_hline(y=upper, line_dash='dash', line_color=em_color, line_width=2)
                    fig.add_hline(y=lower, line_dash='dash', line_color=em_color, line_width=2)
                    # Add consistent annotation with value
                    fig.add_annotation(
                        x=1, y=upper, xref="paper", yref="y",
                        text=f"EM + {upper:.2f}", showarrow=False,
                        font=dict(size=10, color=em_color),
                        xanchor='left', yanchor='bottom', xshift=-105, yshift=-5
                    )
                    fig.add_annotation(
                        x=1, y=lower, xref="paper", yref="y",
                        text=f"EM - {lower:.2f}", showarrow=False,
                        font=dict(size=10, color=em_color),
                        xanchor='left', yanchor='top', xshift=-105, yshift=5
                    )
                continue

            # Determine column name based on type
            col_name = exposure_levels_type
            if exposure_levels_type == 'Vanna' or exposure_levels_type == 'VEX': col_name = 'VEX'
            if exposure_levels_type == 'AbsGEX': col_name = 'GEX'
            if exposure_levels_type == 'Volume': col_name = 'volume'
            
            # Check if column exists
            if col_name in range_calls.columns and col_name in range_puts.columns:
                # Calculate aggregated exposure for each strike
                call_ex = range_calls.groupby('strike')[col_name].sum().to_dict() if not range_calls.empty else {}
                put_ex = range_puts.groupby('strike')[col_name].sum().to_dict() if not range_puts.empty else {}
                
                levels = {}
                all_strikes = set(call_ex.keys()) | set(put_ex.keys())
                
                for strike in all_strikes:
                    c_val = call_ex.get(strike, 0)
                    p_val = put_ex.get(strike, 0)
                    
                    # Calculate Net Exposure based on type logic
                    if exposure_levels_type == 'GEX':
                        # GEX is Call - Put (puts are positive in calculation)
                        net_val = c_val - p_val
                    elif exposure_levels_type == 'AbsGEX':
                        # Absolute GEX = |Call GEX| + |Put GEX|
                        net_val = abs(c_val) + abs(p_val)
                    elif exposure_levels_type == 'Volume':
                        # Volume levels use call volume minus put volume.
                        net_val = c_val - p_val
                    elif exposure_levels_type == 'DEX':
                         # DEX: Call + Put. (Puts have negative delta).
                         net_val = c_val + p_val
                    else: 
                         # Others: Call + Put.
                         net_val = c_val + p_val
                    
                    levels[strike] = net_val

                # Sort by absolute exposure and get top levels
                sorted_levels = sorted(levels.items(), key=lambda x: abs(x[1]), reverse=True)
                top_levels = sorted_levels[:exposure_levels_count]
                
                for strike, val in top_levels:
                    all_top_levels.append((strike, val, exposure_levels_type, i))

        # Find the max level independently for EACH exposure type for highlighting
        max_abs_by_type = {}
        if highlight_max_level and all_top_levels:
            for strike, val, etype, tidx in all_top_levels:
                abs_val = abs(val)
                if etype not in max_abs_by_type or abs_val > max_abs_by_type[etype]:
                    max_abs_by_type[etype] = abs_val

        # Draw all collected levels
        for strike, val, exposure_levels_type, type_index in all_top_levels:
            # Pick dash style
            dash_style = dash_styles[type_index % len(dash_styles)]
            
            # Check if this is the maximum level within its own exposure type
            type_max = max_abs_by_type.get(exposure_levels_type, 0)
            is_max_level = highlight_max_level and type_max > 0 and abs(val) == type_max
            
            if is_max_level:
                color = max_level_color
                intensity = 1.0
            else:
                # Determine color: Green for positive, Red for negative
                color = call_color if val >= 0 else put_color
                
                # Calculate color intensity based on coloring mode
                type_max_val = max(abs(l[1]) for l in all_top_levels if l[2] == exposure_levels_type)
                if type_max_val == 0: type_max_val = 1
                if coloring_mode == 'Solid':
                    intensity = 1.0
                elif coloring_mode == 'Ranked Intensity':
                    intensity = 0.1 + 0.9 * ((abs(val) / type_max_val) ** 3)
                else:  # Linear Intensity (default)
                    intensity = 0.3 + 0.7 * (abs(val) / type_max_val)
            
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            rgba_color = f'rgba({r}, {g}, {b}, {intensity:.2f})'
            
            # Add the horizontal line
            fig.add_hline(
                y=strike,
                line_dash=dash_style,
                line_color=rgba_color,
                line_width=2 if is_max_level else 1
            )
            
            # Add separate annotation for the text
            y_offset_pixels = 5 + (type_index * 15)
            
            # Map type to display name
            display_name = exposure_levels_type
            if exposure_levels_type == 'VEX': display_name = 'Vanna'
            if exposure_levels_type == 'AbsGEX': display_name = 'Abs GEX'
            
            display_text = f"<b>{display_name}: {format_large_number(val)}</b>" if is_max_level else f"{display_name}: {format_large_number(val)}"
            
            fig.add_annotation(
                x=1,
                y=strike,
                xref="paper",
                yref="y",
                text=display_text,
                showarrow=False,
                font=dict(
                    size=10,
                    color=rgba_color,
                ),
                textangle=0,
                xanchor='left',
                yanchor='top',
                xshift=-105,
                yshift=-y_offset_pixels
            )

    return fig.to_json()


def snap_timestamp_to_chart_time(timestamp, chart_times):
    """Snap a stored interval timestamp to the nearest visible candle time."""
    if not chart_times:
        return None

    idx = bisect_left(chart_times, timestamp)
    if idx <= 0:
        return chart_times[0]
    if idx >= len(chart_times):
        return chart_times[-1]

    prev_time = chart_times[idx - 1]
    next_time = chart_times[idx]
    if abs(timestamp - prev_time) <= abs(next_time - timestamp):
        return prev_time
    return next_time


def build_historical_levels_overlay(ticker, display_date, chart_times, latest_price, strike_range,
                                    selected_types, levels_count, call_color, put_color,
                                    highlight_max_level=False, max_level_color='#800080',
                                    coloring_mode='Linear Intensity'):
    """Build historical intraday exposure overlays for the TradingView price chart."""
    if not ticker or not chart_times or not selected_types or not latest_price:
        return [], []

    normalized_types = []
    include_expected_move = False
    for level_type in selected_types:
        normalized = normalize_level_type(level_type)
        if normalized == 'Expected Move':
            include_expected_move = True
            continue
        if normalized in INTERVAL_LEVEL_VALUE_KEYS and normalized not in normalized_types:
            normalized_types.append(normalized)

    min_strike = latest_price * (1 - strike_range)
    max_strike = latest_price * (1 + strike_range)
    interval_rows = get_interval_data(ticker, display_date) if normalized_types else []
    session_rows = get_interval_session_data(ticker, display_date) if include_expected_move else []

    points_by_time = {}
    for row in interval_rows:
        timestamp, _, strike, net_gamma, net_delta, net_vanna, net_charm, abs_gex_total, net_volume, net_speed, net_vomma, net_color = row
        if strike < min_strike or strike > max_strike:
            continue

        snapped_time = snap_timestamp_to_chart_time(timestamp, chart_times)
        if snapped_time is None:
            continue

        value_map = {
            'GEX': net_gamma,
            'AbsGEX': abs_gex_total,
            'DEX': net_delta,
            'VEX': net_vanna,
            'Charm': net_charm,
            'Volume': net_volume,
            'Speed': net_speed,
            'Vomma': net_vomma,
            'Color': net_color,
        }
        bucket = points_by_time.setdefault(snapped_time, {'time': snapped_time, 'by_type': {}})
        for level_type in normalized_types:
            value = value_map.get(level_type)
            if value is None or value == 0:
                continue
            bucket['by_type'].setdefault(level_type, []).append((float(strike), float(value)))

    def _select_historical_candidates(level_type, candidates, count):
        ranked = sorted(candidates, key=lambda item: abs(item[1]), reverse=True)
        if level_type != 'GEX' or count <= 0:
            return [
                {'strike': strike, 'value': value, 'rank': rank}
                for rank, (strike, value) in enumerate(ranked[:count], start=1)
            ]

        selected = []
        seen = set()

        def _append_first(match_fn):
            for strike, value in ranked:
                key = (strike, value)
                if key in seen or not match_fn(value):
                    continue
                seen.add(key)
                selected.append({'strike': strike, 'value': value, 'rank': 1})
                return

        # GEX bubbles should retain the dynamic "call side / put side leader"
        # behavior even though chart walls are now OI-based. That keeps the
        # historical dots informative intraday instead of pinning them to static
        # OI strikes that rarely move during the session.
        _append_first(lambda value: value > 0)
        _append_first(lambda value: value < 0)

        next_rank = 2
        for strike, value in ranked:
            key = (strike, value)
            if key in seen:
                continue
            seen.add(key)
            selected.append({'strike': strike, 'value': value, 'rank': next_rank})
            next_rank += 1
            if len(selected) >= count:
                break

        return selected[:count]

    selected_points = []
    max_abs_by_type = {}
    for bucket in points_by_time.values():
        snapped_time = bucket['time']
        for level_type, candidates in bucket['by_type'].items():
            top_levels = _select_historical_candidates(level_type, candidates, levels_count)
            for point in top_levels:
                selected_points.append({
                    'time': snapped_time,
                    'price': point['strike'],
                    'value': point['value'],
                    'type': level_type,
                    'rank': point['rank'],
                })
                max_abs_by_type[level_type] = max(max_abs_by_type.get(level_type, 0), abs(point['value']))

    highlight_abs_by_bucket_type = {}
    if highlight_max_level:
        for point in selected_points:
            bucket_key = (point['time'], point['type'])
            highlight_abs_by_bucket_type[bucket_key] = max(
                highlight_abs_by_bucket_type.get(bucket_key, 0),
                abs(point['value'])
            )

    historical_points = []
    for point in selected_points:
        level_type = point['type']
        type_max_value = max_abs_by_type.get(level_type, 0) or 1.0
        normalized_value = min(1.0, abs(point['value']) / type_max_value)
        if coloring_mode == 'Solid':
            intensity = 0.95
        elif coloring_mode == 'Ranked Intensity':
            intensity = 0.15 + 0.85 * (normalized_value ** 3)
        else:
            intensity = 0.35 + 0.65 * normalized_value

        bucket_key = (point['time'], level_type)
        is_max = (
            highlight_max_level
            and highlight_abs_by_bucket_type.get(bucket_key, 0) > 0
            and abs(point['value']) == highlight_abs_by_bucket_type[bucket_key]
        )
        base_color = call_color if point['value'] >= 0 else put_color
        historical_points.append({
            'time': point['time'],
            'price': round(point['price'], 4),
            'size': round(6 + (12 * normalized_value) + (2 if is_max else 0), 2),
            'color': hex_to_rgba(base_color, intensity),
            'border_color': max_level_color if is_max else base_color,
            'border_width': 2 if is_max else 1,
            'label': INTERVAL_LEVEL_DISPLAY_NAMES.get(level_type, level_type),
            'rank': point['rank'],
            'side': 'Call' if point['value'] >= 0 else 'Put',
            'value': format_large_number(point['value']),
            'kind': 'exposure',
        })

    expected_move_by_time = {}
    for row in session_rows:
        timestamp, price, expected_move, expected_move_upper, expected_move_lower = row
        if expected_move is None or expected_move <= 0 or expected_move_upper is None or expected_move_lower is None:
            continue
        snapped_time = snap_timestamp_to_chart_time(timestamp, chart_times)
        if snapped_time is None:
            continue
        expected_move_by_time[snapped_time] = {
            'time': snapped_time,
            'price': round(price, 4),
            'move': round(expected_move, 4),
            'upper': round(expected_move_upper, 4),
            'lower': round(expected_move_lower, 4),
        }

    expected_move_rows = [
        expected_move_by_time[time_key]
        for time_key in sorted(expected_move_by_time.keys())
    ]
    max_expected_move = max((row['move'] for row in expected_move_rows), default=0) or 1.0
    for row in expected_move_rows:
        normalized_value = min(1.0, abs(row['move']) / max_expected_move)
        if coloring_mode == 'Solid':
            intensity = 0.95
        elif coloring_mode == 'Ranked Intensity':
            intensity = 0.15 + 0.85 * (normalized_value ** 3)
        else:
            intensity = 0.35 + 0.65 * normalized_value

        bubble_size = round(7 + (10 * normalized_value), 2)
        for direction, bubble_price in (('Upper', row['upper']), ('Lower', row['lower'])):
            historical_points.append({
                'time': row['time'],
                'price': bubble_price,
                'size': bubble_size,
                'color': hex_to_rgba('#036bfc', intensity),
                'border_color': '#81b4ff',
                'border_width': 1,
                'label': 'Expected Move',
                'rank': None,
                'side': direction,
                'value': f"${row['move']:.2f}",
                'kind': 'expected-move',
                'reference_price': f"${row['price']:.2f}",
            })

    historical_points.sort(
        key=lambda point: (
            point['time'],
            0 if point.get('kind') == 'expected-move' else 1,
            point.get('rank') or 99,
            point['price'],
        )
    )

    historical_expected_moves = []

    return historical_points, historical_expected_moves


def create_gex_side_panel(calls, puts, S, strike_range=0.02,
                          call_color=CALL_COLOR, put_color=PUT_COLOR,
                          selected_expiries=None):
    """Horizontal-bar GEX panel keyed to strike, intended to render in a sibling
    div next to the TradingView candle chart with a shared visible price range.

    Calls contribute positive net GEX (dealers long gamma, green), puts
    contribute negative (dealers short gamma, red). Each row in calls/puts is
    summed at its native strike — SPY's native grid is $1, SPX's is $5, so the
    resulting bar resolution naturally matches the underlying.
    """
    empty = go.Figure()
    empty.update_layout(
        paper_bgcolor=PLOT_THEME['paper_bgcolor'], plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        margin=dict(l=4, r=4, t=4, b=24),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )

    if (calls is None or getattr(calls, 'empty', True)) and \
       (puts is None or getattr(puts, 'empty', True)):
        return empty.to_json()

    if calls is not None and not calls.empty and selected_expiries and 'expiration_date' in calls.columns:
        calls = calls[calls['expiration_date'].isin(selected_expiries)]
    if puts is not None and not puts.empty and selected_expiries and 'expiration_date' in puts.columns:
        puts = puts[puts['expiration_date'].isin(selected_expiries)]

    min_strike = S * (1 - strike_range)
    max_strike = S * (1 + strike_range)

    def strike_sum(df):
        if df is None or df.empty or 'GEX' not in df.columns:
            return {}
        f = df[(df['strike'] >= min_strike) & (df['strike'] <= max_strike)]
        if f.empty:
            return {}
        return f.groupby('strike')['GEX'].sum().to_dict()

    call_map = strike_sum(calls)
    put_map = strike_sum(puts)
    strikes = sorted(set(call_map) | set(put_map))
    if not strikes:
        return empty.to_json()

    call_vals = [call_map.get(s, 0) for s in strikes]
    put_vals = [put_map.get(s, 0) for s in strikes]
    net = [c - p for c, p in zip(call_vals, put_vals)]

    def _hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    call_rgb = _hex_to_rgb(call_color)
    put_rgb = _hex_to_rgb(put_color)
    max_abs = max((abs(v) for v in net), default=0) or 1.0

    def _shade(v):
        alpha = 0.30 + 0.70 * (abs(v) / max_abs)
        r, g, b = call_rgb if v >= 0 else put_rgb
        return f'rgba({r},{g},{b},{alpha:.3f})'

    colors = [_shade(v) for v in net]
    customdata = list(zip(call_vals, put_vals))

    native_interval = 1.0
    if len(strikes) >= 2:
        diffs = [round(strikes[i] - strikes[i - 1], 4) for i in range(1, len(strikes))]
        diffs = [d for d in diffs if d > 0]
        if diffs:
            from collections import Counter
            native_interval = Counter(diffs).most_common(1)[0][0]
    bar_width = max(native_interval * 0.3, 0.05)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=net, y=strikes,
        orientation='h',
        width=bar_width,
        marker=dict(color=colors, line=dict(width=0)),
        customdata=customdata,
        hovertemplate=(
            'Strike %{y}<br>'
            'Net GEX %{x:,.0f}<br>'
            'Call GEX %{customdata[0]:,.0f}<br>'
            'Put GEX %{customdata[1]:,.0f}<extra></extra>'
        ),
        showlegend=False,
    ))
    fig.add_vline(x=0, line_color='#555', line_width=1)
    fig.add_hline(y=S, line_color='#888', line_dash='dot', line_width=1)

    fig.update_layout(
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        margin=dict(l=4, r=4, t=4, b=24),
        height=680,
        xaxis=dict(
            zeroline=False,
            gridcolor='#2A2A2A',
            color='#888',
            tickfont=dict(size=9),
            title=dict(text='Net GEX', font=dict(size=10, color='#888')),
        ),
        yaxis=dict(
            zeroline=False,
            gridcolor='#2A2A2A',
            color='#aaa',
            tickfont=dict(size=9),
            side='right',
            range=[min_strike, max_strike],
        ),
    )
    return fig.to_json()


def _expiration_series_iso(df):
    """Return an ISO-date Series for whichever expiration column the chain carries."""
    if df is None or df.empty:
        return None
    if 'expiration_date' in df.columns:
        return pd.to_datetime(df['expiration_date'], errors='coerce').dt.date.astype(str)
    if 'expiration' in df.columns:
        return pd.to_datetime(df['expiration'], errors='coerce').dt.date.astype(str)
    return None


def _nearest_expiration(df):
    """Return the nearest expiration date string from a calls or puts DataFrame."""
    expiries = _expiration_series_iso(df)
    if expiries is None:
        return None
    expiries = expiries.dropna()
    if expiries.empty:
        return None
    from datetime import date
    today_str = date.today().isoformat()
    future = expiries[expiries >= today_str]
    if not future.empty:
        return future.min()
    return expiries.min()


def compute_top_oi_strikes(calls, puts, n=5):
    """Return top-N OI strikes per side for the nearest expiration.

    Returns dict with keys 'calls' (list of {strike, oi}), 'puts', 'both' (overlap strikes).
    Tolerates empty DataFrames — returns empty lists rather than raising.
    """
    try:
        n = max(1, min(10, int(n)))
    except Exception:
        n = 5
    empty = {'calls': [], 'puts': [], 'both': []}
    if (calls is None or calls.empty) and (puts is None or puts.empty):
        return empty

    nearest = _nearest_expiration(calls if (calls is not None and not calls.empty) else puts)
    if nearest is None:
        return empty

    def top_oi(df):
        if df is None or df.empty or 'openInterest' not in df.columns:
            return []
        expiries = _expiration_series_iso(df)
        subset = df[expiries == nearest] if expiries is not None else df
        if subset.empty:
            return []
        agg = subset.groupby('strike')['openInterest'].sum().nlargest(n).reset_index()
        return [{'strike': float(row['strike']), 'oi': int(row['openInterest'])} for _, row in agg.iterrows()]

    top_calls = top_oi(calls)
    top_puts = top_oi(puts)
    call_strikes = {r['strike'] for r in top_calls}
    put_strikes = {r['strike'] for r in top_puts}
    overlap = sorted(call_strikes & put_strikes)
    return {'calls': top_calls, 'puts': top_puts, 'both': overlap}


def _coerce_epoch_ms(value):
    """Normalize optional Schwab epoch timestamps to integer milliseconds."""
    try:
        if value is None or pd.isna(value):
            return None
        ts = int(float(value))
    except Exception:
        return None
    if ts <= 0:
        return None
    if ts < 10_000_000_000:
        ts *= 1000
    return ts


def _format_flow_blotter_time(ts_ms):
    if not ts_ms:
        return '—'
    try:
        local_dt = datetime.fromtimestamp(ts_ms / 1000, tz=pytz.UTC).astimezone()
        today = datetime.now(local_dt.tzinfo).date()
        if local_dt.date() == today:
            return local_dt.strftime('%H:%M:%S')
        return local_dt.strftime('%b %d %H:%M')
    except Exception:
        return '—'


def _format_flow_blotter_expiry(expiry_value):
    if expiry_value is None or pd.isna(expiry_value):
        return '—'
    try:
        expiry_dt = pd.to_datetime(expiry_value, errors='coerce')
        if pd.isna(expiry_dt):
            return '—'
        return expiry_dt.strftime('%b %d')
    except Exception:
        return '—'


def _flow_blotter_market_text(bid, mid, ask):
    if bid <= 0 and ask <= 0:
        return '—'
    bid_text = f"{bid:.2f}" if bid > 0 else '—'
    mid_text = f"{mid:.2f}" if mid > 0 else '—'
    ask_text = f"{ask:.2f}" if ask > 0 else '—'
    return f"{bid_text} / {mid_text} / {ask_text}"


def _flow_blotter_side_label(side_code, bid, ask):
    if bid <= 0 and ask <= 0:
        return 'unknown'
    if side_code > 0:
        return 'ask'
    if side_code < 0:
        return 'bid'
    return 'mid'


_FLOW_CONTRACT_HISTORY = collections.defaultdict(lambda: collections.deque(maxlen=96))


def _flow_pulse_dte_days(expiry_iso):
    if not expiry_iso:
        return None
    try:
        expiry_dt = pd.to_datetime(expiry_iso, errors='coerce')
        session_dt = pd.to_datetime(_current_session_date_str(), errors='coerce')
        if pd.isna(expiry_dt) or pd.isna(session_dt):
            return None
        return int((expiry_dt.normalize() - session_dt.normalize()).days)
    except Exception:
        return None


def _flow_pulse_moneyness_band(option_type, moneyness_pct):
    try:
        pct = float(moneyness_pct)
    except Exception:
        return 'unknown'
    abs_pct = abs(pct)
    if abs_pct <= 0.25:
        return 'atm'
    is_otm = pct >= 0 if option_type == 'call' else pct <= 0
    if is_otm and abs_pct > 1.0:
        return 'far_otm'
    if is_otm:
        return 'near_otm'
    return 'itm'


def _classify_flow_pulse_lean(option_type, side, moneyness_pct, premium_1m, pace_1m, voi, dte_days):
    band = _flow_pulse_moneyness_band(option_type, moneyness_pct)
    base = 0.0
    label = 'mixed'
    hint = 'Limited directional read'
    if option_type == 'call':
        if side == 'ask':
            base = {'atm': 0.80, 'near_otm': 0.74, 'far_otm': 0.42, 'itm': 0.24}.get(band, 0.18)
            label = 'bullish'
            hint = 'Call buying is leaning upside'
        elif side == 'bid':
            base = {'atm': -0.42, 'near_otm': -0.36, 'far_otm': -0.18, 'itm': -0.14}.get(band, -0.10)
            label = 'bearish'
            hint = 'Call selling is leaning risk-off'
        elif side == 'mid':
            base = {'atm': 0.22, 'near_otm': 0.18, 'far_otm': 0.08, 'itm': 0.04}.get(band, 0.05)
            label = 'bullish' if abs(base) >= 0.12 else 'mixed'
            hint = 'Call activity is leaning upside'
    elif option_type == 'put':
        if band == 'far_otm' and side in ('ask', 'mid', 'unknown'):
            base = 0.08
            label = 'hedge'
            hint = 'Far OTM put activity often reads as hedge flow'
        elif side == 'ask':
            base = {'atm': -0.82, 'near_otm': -0.66, 'itm': -0.24}.get(band, -0.14)
            label = 'bearish'
            hint = 'Put buying is leaning downside'
        elif side == 'bid':
            base = {'atm': 0.42, 'near_otm': 0.34, 'far_otm': 0.16, 'itm': 0.14}.get(band, 0.10)
            label = 'bullish'
            hint = 'Put selling is leaning supportive'
        elif side == 'mid':
            base = {'atm': -0.22, 'near_otm': -0.18, 'itm': -0.04}.get(band, -0.08)
            label = 'bearish' if abs(base) >= 0.12 else 'mixed'
            hint = 'Put activity is leaning downside'
        else:
            base = -0.06 if band != 'far_otm' else 0.06
            label = 'mixed' if band != 'far_otm' else 'hedge'
            hint = 'Put flow direction is not cleanly classified'
    pace_factor = 0.72 + min(max(float(pace_1m or 0.0), 0.0), 6.0) * 0.055
    voi_factor = 0.90 + min(max(float(voi or 0.0), 0.0), 3.0) * 0.05
    premium = max(float(premium_1m or 0.0), 0.0)
    premium_factor = 0.86
    if premium >= 250000:
        premium_factor = 1.16
    elif premium >= 100000:
        premium_factor = 1.08
    elif premium >= 50000:
        premium_factor = 1.00
    if dte_days is None:
        dte_factor = 1.0
    elif dte_days <= 1:
        dte_factor = 1.14
    elif dte_days <= 3:
        dte_factor = 1.07
    elif dte_days >= 14:
        dte_factor = 0.94
    else:
        dte_factor = 1.0
    score = max(-1.0, min(1.0, base * pace_factor * voi_factor * premium_factor * dte_factor))
    if label == 'hedge':
        score = max(-0.18, min(0.18, score))
    if abs(score) < 0.14 and label != 'hedge':
        label = 'mixed'
    return {
        'moneyness_band': band,
        'lean_label': label,
        'lean_score': score,
        'lean_hint': hint,
    }


def summarize_flow_pulse(rows):
    if not rows:
        return {
            'label': 'mixed',
            'score': 0.0,
            'weighted_premium': 0.0,
            'gross_premium': 0.0,
            'hedge_share': 0.0,
        }
    weighted = 0.0
    gross = 0.0
    hedge_premium = 0.0
    for row in rows:
        premium = max(float(row.get('premium_delta_1m') or 0.0), 0.0)
        score = float(row.get('lean_score') or 0.0)
        weighted += premium * score
        gross += premium * abs(score)
        if row.get('lean_label') == 'hedge':
            hedge_premium += premium
    net_score = (weighted / gross) if gross > 0 else 0.0
    hedge_share = (hedge_premium / max(sum(max(float(r.get('premium_delta_1m') or 0.0), 0.0) for r in rows), 1.0))
    if gross < 25000:
        label = 'mixed'
    elif hedge_share >= 0.55 and abs(net_score) < 0.22:
        label = 'hedge'
    elif net_score >= 0.18:
        label = 'bullish'
    elif net_score <= -0.18:
        label = 'bearish'
    else:
        label = 'mixed'
    return {
        'label': label,
        'score': net_score,
        'weighted_premium': weighted,
        'gross_premium': gross,
        'hedge_share': hedge_share,
    }


def _build_contract_direction_meta(option_type, strike, S, expiry_iso='', side='unknown',
                                   premium_est=None, pace_hint=None, voi_hint=None):
    if option_type not in ('call', 'put') or strike is None or S in (None, 0):
        return {'direction_classifiable': False}
    try:
        moneyness_pct = ((float(strike) - float(S)) / float(S) * 100.0)
    except Exception:
        return {'direction_classifiable': False}
    dte_days = _flow_pulse_dte_days(expiry_iso)
    lean = _classify_flow_pulse_lean(
        option_type=option_type,
        side=side,
        moneyness_pct=moneyness_pct,
        premium_1m=premium_est,
        pace_1m=pace_hint,
        voi=voi_hint,
        dte_days=dte_days,
    )
    return {
        'direction_classifiable': True,
        'direction_label': lean['lean_label'],
        'direction_score': lean['lean_score'],
        'direction_hint': lean['lean_hint'],
        'moneyness_pct': moneyness_pct,
        'moneyness_band': lean['moneyness_band'],
        'dte_days': dte_days,
        'side': side,
    }


def _current_session_date_str():
    try:
        return datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
    except Exception:
        return datetime.now(pytz.UTC).strftime('%Y-%m-%d')


def _normalize_expiry_iso(value):
    if value is None or pd.isna(value):
        return ''
    try:
        expiry_dt = pd.to_datetime(value, errors='coerce')
        if pd.isna(expiry_dt):
            return ''
        return expiry_dt.strftime('%Y-%m-%d')
    except Exception:
        return ''


def _estimate_contract_ref_price(row):
    bid = float(row.get('bid', 0) or 0)
    ask = float(row.get('ask', 0) or 0)
    last = float(row.get('lastPrice', 0) or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last > 0:
        return last
    if bid > 0:
        return bid
    if ask > 0:
        return ask
    return 0.0


def _history_delta_over_window(history, latest_ts, field, seconds, min_age_ratio=0.75, max_age_ratio=2.5):
    if not history:
        return None, None
    latest = history[-1]
    latest_value = float(latest.get(field, 0) or 0)
    min_age = seconds * min_age_ratio
    max_age = seconds * max_age_ratio
    ref = None
    for sample in reversed(history):
        age = latest_ts - float(sample.get('ts', latest_ts) or latest_ts)
        if age < min_age:
            continue
        if age <= max_age:
            ref = sample
            break
    if ref is None:
        return None, None
    base_value = float(ref.get(field, 0) or 0)
    return max(0.0, latest_value - base_value), max(0.0, latest_ts - float(ref.get('ts', latest_ts) or latest_ts))


def build_flow_pulse_snapshot(ticker, calls, puts, S, strike_range=0.02, top_n=6):
    """Return contract-level flow acceleration reads from in-process volume history."""
    if not ticker or S is None:
        return []
    try:
        lo = float(S) * (1.0 - float(strike_range))
        hi = float(S) * (1.0 + float(strike_range))
    except Exception:
        return []
    now_ts = time.time()
    session_date = _current_session_date_str()
    rows = []

    def _sort_metric(value):
        try:
            numeric = float(value)
        except Exception:
            return 0.0
        return numeric if np.isfinite(numeric) else 0.0

    def _process_df(df, option_type):
        if df is None or getattr(df, 'empty', True):
            return
        working = df.copy()
        if 'volume' in working.columns:
            working = working[pd.to_numeric(working['volume'], errors='coerce').fillna(0) > 0]
        if 'strike' in working.columns:
            strikes = pd.to_numeric(working['strike'], errors='coerce')
            working = working[(strikes >= lo) & (strikes <= hi)]
        if working.empty:
            return
        for _, row in working.iterrows():
            strike = float(row.get('strike', 0) or 0)
            volume = max(0.0, float(row.get('volume', 0) or 0))
            oi = max(0.0, float(row.get('openInterest', 0) or 0))
            if volume <= 0:
                continue
            expiry_iso = _normalize_expiry_iso(row.get('expiration', row.get('expiration_date')))
            history_key = (session_date, ticker, option_type, expiry_iso, round(strike, 4))
            history = _FLOW_CONTRACT_HISTORY[history_key]
            sample = {'ts': now_ts, 'volume': volume, 'oi': oi}
            if history:
                last = history[-1]
                age = now_ts - float(last.get('ts', now_ts) or now_ts)
                changed = volume != float(last.get('volume', 0) or 0) or oi != float(last.get('oi', 0) or 0)
                if age < 8:
                    if changed:
                        history[-1] = sample
                else:
                    history.append(sample)
            else:
                history.append(sample)

            latest = history[-1]
            vol_1m, age_1m = _history_delta_over_window(history, now_ts, 'volume', 60)
            vol_5m, age_5m = _history_delta_over_window(history, now_ts, 'volume', 300)
            if (vol_1m is None or vol_1m <= 0) and (vol_5m is None or vol_5m <= 0):
                continue

            ref_price = _estimate_contract_ref_price(row)
            premium_1m = (vol_1m or 0.0) * max(ref_price, 0.0) * 100.0
            premium_5m = (vol_5m or 0.0) * max(ref_price, 0.0) * 100.0
            baseline_1m = (vol_5m / 5.0) if vol_5m and vol_5m > 0 else 0.0
            pace_1m = (vol_1m / max(baseline_1m, 1.0)) if vol_1m is not None else None
            voi = (volume / oi) if oi > 0 else float(volume)
            moneyness_pct = ((strike - float(S)) / float(S) * 100.0) if S else None
            side_label = _flow_blotter_side_label(int(row.get('side', 0) or 0), float(row.get('bid', 0) or 0), float(row.get('ask', 0) or 0))
            dte_days = _flow_pulse_dte_days(expiry_iso)
            lean = _classify_flow_pulse_lean(
                option_type=option_type,
                side=side_label,
                moneyness_pct=moneyness_pct,
                premium_1m=premium_1m,
                pace_1m=pace_1m,
                voi=voi,
                dte_days=dte_days,
            )
            score = premium_1m * min(max(pace_1m or 1.0, 1.0), 8.0) * (1.0 + min(voi, 2.0))
            rows.append({
                'key': f"{expiry_iso}:{option_type}:{strike:.4f}",
                'ticker': ticker,
                'option_type': option_type,
                'strike': strike,
                'expiry_iso': expiry_iso,
                'expiry_text': _format_flow_blotter_expiry(expiry_iso),
                'contract_label': f"{strike:.0f}{'C' if option_type == 'call' else 'P'}",
                'volume': volume,
                'open_interest': oi,
                'voi': voi,
                'price': ref_price,
                'vol_delta_1m': vol_1m,
                'vol_delta_5m': vol_5m,
                'premium_delta_1m': premium_1m,
                'premium_delta_5m': premium_5m,
                'pace_1m': pace_1m,
                'age_1m': age_1m,
                'age_5m': age_5m,
                'score': score,
                'moneyness_pct': moneyness_pct,
                'moneyness_band': lean['moneyness_band'],
                'dte_days': dte_days,
                'side': side_label,
                'lean_label': lean['lean_label'],
                'lean_score': lean['lean_score'],
                'lean_hint': lean['lean_hint'],
            })

    _process_df(calls, 'call')
    _process_df(puts, 'put')
    rows.sort(
        key=lambda row: (
            _sort_metric(row.get('score')),
            _sort_metric(row.get('premium_delta_1m')),
            _sort_metric(row.get('vol_delta_1m')),
        ),
        reverse=True,
    )
    return rows[:max(1, int(top_n or 6))]


def compute_key_levels(calls, puts, S, selected_expiries=None, strike_range=None):
    """Return the key dealer-flow levels to draw on the price chart.

    Call Wall: strike with the highest call-side open interest.
    Put Wall:  strike with the highest put-side open interest.
    Gamma Flip: interpolated strike where the displayed per-strike net GEX
                profile crosses zero. If several sign changes exist, prefer
                the crossing closest to spot because that is the most relevant
                local regime boundary on the chart.
    Max +/- GEX: live strongest positive / negative net GEX strikes. When a
                 strike window is provided, rank these inside that window so
                 the chart overlays match the visible strike rail.
    EM Upper/Lower: ATM straddle-based ±1σ expected move bracket.

    All values can be None independently if the inputs don't support them
    (e.g. EM only when bid/ask are present).
    """
    out = {
        'call_wall': None, 'put_wall': None, 'gamma_flip': None,
        'em_upper': None, 'em_lower': None,
        'call_wall_2': None, 'put_wall_2': None, 'hvl': None,
        'max_positive_gex': None, 'max_negative_gex': None,
        'em_upper_2': None, 'em_lower_2': None,
    }
    if S is None:
        return out

    def _filter(df):
        if df is None or df.empty or 'GEX' not in df.columns:
            return None
        if selected_expiries and 'expiration_date' in df.columns:
            df = df[df['expiration_date'].isin(selected_expiries)]
        return df if not df.empty else None

    c = _filter(calls)
    p = _filter(puts)

    call_map = c.groupby('strike')['GEX'].sum().to_dict() if c is not None else {}
    put_map  = p.groupby('strike')['GEX'].sum().to_dict() if p is not None else {}
    strikes = sorted(set(call_map) | set(put_map))
    def _rank_side_oi(df):
        if df is None or df.empty or 'openInterest' not in df.columns:
            return []
        grouped = (
            df[['strike', 'openInterest']]
            .assign(openInterest=lambda x: pd.to_numeric(x['openInterest'], errors='coerce').fillna(0.0))
            .groupby('strike', as_index=False)['openInterest']
            .sum()
        )
        if grouped.empty:
            return []
        ranked = grouped[grouped['openInterest'] > 0].sort_values(
            ['openInterest', 'strike'],
            ascending=[False, True],
            kind='mergesort',
        )
        return [(float(row['strike']), float(row['openInterest'])) for _, row in ranked.iterrows()]

    ranked_calls = _rank_side_oi(c)
    ranked_puts = _rank_side_oi(p)
    if ranked_calls:
        cw_strike, cw_oi = ranked_calls[0]
        out['call_wall'] = {'price': float(cw_strike), 'oi': float(cw_oi)}
        if len(ranked_calls) > 1:
            cw2_strike, cw2_oi = ranked_calls[1]
            out['call_wall_2'] = {'price': float(cw2_strike), 'oi': float(cw2_oi)}
    if ranked_puts:
        pw_strike, pw_oi = ranked_puts[0]
        out['put_wall'] = {'price': float(pw_strike), 'oi': float(pw_oi)}
        if len(ranked_puts) > 1:
            pw2_strike, pw2_oi = ranked_puts[1]
            out['put_wall_2'] = {'price': float(pw2_strike), 'oi': float(pw2_oi)}

    if strikes:
        net = {s: call_map.get(s, 0) - put_map.get(s, 0) for s in strikes}
        if strike_range is not None and S is not None:
            lo = float(S) * (1 - float(strike_range))
            hi = float(S) * (1 + float(strike_range))
            extrema_items = [(s, v) for s, v in net.items() if lo <= s <= hi]
            if not extrema_items:
                extrema_items = list(net.items())
        else:
            extrema_items = list(net.items())

        pos_extrema = [(s, v) for s, v in extrema_items if v > 0]
        neg_extrema = [(s, v) for s, v in extrema_items if v < 0]
        if pos_extrema:
            pos_strike, pos_val = max(pos_extrema, key=lambda item: item[1])
            out['max_positive_gex'] = {'price': float(pos_strike), 'gex': float(pos_val)}
        if neg_extrema:
            neg_strike, neg_val = min(neg_extrema, key=lambda item: item[1])
            out['max_negative_gex'] = {'price': float(neg_strike), 'gex': float(neg_val)}

        # Match the displayed strike-rail profile: look for adjacent net-GEX bars
        # whose signs differ, then interpolate between them. Exact zero bars win
        # immediately. Multiple crossings can happen on noisy chains, so use the
        # one closest to spot.
        crossings = []
        prev_s, prev_v = None, None
        eps = 1e-12
        for s in strikes:
            v = float(net.get(s, 0.0) or 0.0)
            if abs(v) <= eps:
                crossings.append((abs(s - S), float(s)))
            if prev_s is not None and prev_v is not None:
                if abs(prev_v) <= eps:
                    crossings.append((abs(prev_s - S), float(prev_s)))
                elif prev_v * v < 0:
                    t = (0.0 - prev_v) / (v - prev_v)
                    flip_price = prev_s + t * (s - prev_s)
                    crossings.append((abs(flip_price - S), float(flip_price)))
            prev_s, prev_v = s, v

        if crossings:
            crossings.sort(key=lambda item: (item[0], item[1]))
            out['gamma_flip'] = {'price': float(crossings[0][1])}

    em = calculate_expected_move_snapshot(c, p, S, selected_expiries=selected_expiries)
    if em:
        out['em_upper'] = {'price': float(em['upper']), 'move': float(em['move'])}
        out['em_lower'] = {'price': float(em['lower']), 'move': float(em['move'])}
        out['em_upper_2'] = {'price': float(S + 2 * em['move']), 'move': float(em['move'])}
        out['em_lower_2'] = {'price': float(S - 2 * em['move']), 'move': float(em['move'])}

    # HVL — highest-volume strike (fallback to openInterest for illiquid names).
    try:
        parts = []
        for df in (c, p):
            if df is None or df.empty:
                continue
            col = 'volume' if 'volume' in df.columns else ('openInterest' if 'openInterest' in df.columns else None)
            if col:
                parts.append(df[['strike', col]].rename(columns={col: '_w'}))
        if parts:
            combined = pd.concat(parts, ignore_index=True)
            combined['_w'] = pd.to_numeric(combined['_w'], errors='coerce').fillna(0.0)
            if combined['_w'].sum() <= 0:
                # fallback to openInterest if volume was all zero
                oi_parts = []
                for df in (c, p):
                    if df is not None and not df.empty and 'openInterest' in df.columns:
                        oi_parts.append(df[['strike', 'openInterest']].rename(columns={'openInterest': '_w'}))
                if oi_parts:
                    combined = pd.concat(oi_parts, ignore_index=True)
                    combined['_w'] = pd.to_numeric(combined['_w'], errors='coerce').fillna(0.0)
            grouped = combined.groupby('strike')['_w'].sum()
            if not grouped.empty and grouped.max() > 0:
                out['hvl'] = {'price': float(grouped.idxmax()), 'weight': float(grouped.max())}
    except Exception:
        pass

    return out


def _recompute_gex_row(row, S, iv_override=None,
                       delta_adjusted: bool = False, calculate_in_notional: bool = True):
    """Return GEX for a single option row at spot S, optionally with a shifted IV.

    Delegates to calculate_greek_exposures so the formula can never drift from the
    one used at chain-fetch time. Requires the row to carry '_weight' (set by the
    chain fetcher) — falls back to openInterest if the row pre-dates that change.
    """
    try:
        weight = row.get('_weight')
        if weight is None:
            weight = row.get('openInterest', 0) or 0
        opt = {
            'contractSymbol': row.get('contractSymbol', ''),
            'strike': float(row['strike']),
            'impliedVolatility': float(row.get('impliedVolatility', 0.0) or 0.0),
            'expiration': row['expiration'],
        }
        return float(calculate_greek_exposures(
            opt, S, weight,
            delta_adjusted=delta_adjusted,
            calculate_in_notional=calculate_in_notional,
            iv_override=iv_override,
        )['GEX'])
    except Exception:
        return 0.0


def compute_scenario_gex(calls, puts, S, spot_shift=0.0, iv_shift=0.0,
                         strike_range=0.02, selected_expiries=None,
                         delta_adjusted: bool = False, calculate_in_notional: bool = True):
    """Re-sum net GEX under a spot and/or IV shift.

    No new math — every row is fed back through calculate_greek_exposures with
    the shifted spot/IV. Strike window is anchored at the *shifted* spot so the
    sample of strikes tracks the dealer's effective book under the scenario.
    """
    S_new = float(S) * (1.0 + float(spot_shift))
    if S_new <= 0:
        return {'net_gex': 0.0, 'regime': 'Long Gamma'}
    lo, hi = S_new * (1.0 - strike_range), S_new * (1.0 + strike_range)

    def _sum(df):
        if df is None or df.empty:
            return 0.0
        f = df
        if selected_expiries and 'expiration_date' in f.columns:
            f = f[f['expiration_date'].isin(selected_expiries)]
        f = f[(f['strike'] >= lo) & (f['strike'] <= hi)]
        if f.empty:
            return 0.0
        total = 0.0
        for _, r in f.iterrows():
            iv_base = float(r.get('impliedVolatility', 0.0) or 0.0)
            iv_new = max(0.0, iv_base + float(iv_shift)) if iv_base > 0 else iv_base
            total += _recompute_gex_row(
                r, S_new,
                iv_override=(iv_new if iv_shift else None),
                delta_adjusted=delta_adjusted,
                calculate_in_notional=calculate_in_notional,
            )
        return total

    call_gex = _sum(calls)
    put_gex  = _sum(puts)
    net = call_gex - put_gex
    return {'net_gex': float(net), 'regime': 'Long Gamma' if net >= 0 else 'Short Gamma'}


# Phase 3 Stage 2 — session baselines for the Net GEX/DEX Δ columns. First
# tick of the trading session captures the baseline; subsequent ticks emit
# (current - baseline). In-process dict — acceptable to lose state on restart.
_SESSION_BASELINE = {}
_SESSION_LEVEL_BASELINE = {}
_SESSION_IV_BASELINE = {}

def _compute_session_deltas(ticker, net_gex, net_dex, scope_id=None):
    if ticker is None or net_gex is None:
        return None
    try:
        today = datetime.now(pytz.timezone('US/Eastern')).date()
    except Exception:
        return None
    key = (ticker, today, scope_id or 'default')
    if key not in _SESSION_BASELINE:
        _SESSION_BASELINE[key] = {'net_gex': net_gex, 'net_dex': net_dex}
    base = _SESSION_BASELINE[key]
    return {
        'net_gex_vs_open': (net_gex - base['net_gex']) if base.get('net_gex') is not None else None,
        'net_dex_vs_open': (net_dex - base['net_dex']) if (net_dex is not None and base.get('net_dex') is not None) else None,
    }


def _compute_level_session_deltas(ticker, levels, scope_id=None):
    if ticker is None or not isinstance(levels, dict):
        return None
    try:
        today = datetime.now(pytz.timezone('US/Eastern')).date()
    except Exception:
        return None
    keys = ('call_wall', 'put_wall', 'gamma_flip', 'em_upper', 'em_lower')
    key = (ticker, today, scope_id or 'default')
    if key not in _SESSION_LEVEL_BASELINE:
        _SESSION_LEVEL_BASELINE[key] = {name: levels.get(name) for name in keys}
    base = _SESSION_LEVEL_BASELINE[key]
    out = {}
    for name in keys:
        cur = levels.get(name)
        ref = base.get(name)
        out[name] = (cur - ref) if (cur is not None and ref is not None) else None
    return out


def _pick_strike_iv(df, target, side='nearest'):
    if df is None or getattr(df, 'empty', True) or 'strike' not in df.columns or 'impliedVolatility' not in df.columns:
        return None
    working = df.copy()
    working['strike'] = pd.to_numeric(working['strike'], errors='coerce')
    working['impliedVolatility'] = pd.to_numeric(working['impliedVolatility'], errors='coerce')
    working = working.dropna(subset=['strike', 'impliedVolatility'])
    working = working[working['impliedVolatility'] > 0]
    if working.empty:
        return None
    if side == 'below':
        subset = working[working['strike'] <= target]
        if subset.empty:
            subset = working
    elif side == 'above':
        subset = working[working['strike'] >= target]
        if subset.empty:
            subset = working
    else:
        subset = working
    if subset.empty:
        return None
    row = subset.iloc[(subset['strike'] - target).abs().argsort()[:1]]
    if row.empty:
        return None
    strike = float(row['strike'].iloc[0])
    iv = float(row['impliedVolatility'].iloc[0])
    return {'strike': strike, 'iv': iv}


def compute_iv_context(calls, puts, S, ticker=None):
    """Build a compact ATM/wing IV + skew read for the alerts rail."""
    out = {
        'expiry_text': 'Near expiry',
        'atm_iv': None,
        'atm_call_iv': None,
        'atm_put_iv': None,
        'put_wing_iv': None,
        'call_wing_iv': None,
        'put_wing_strike': None,
        'call_wing_strike': None,
        'skew_spread': None,
        'skew_ratio': None,
        'atm_iv_change': None,
        'skew_change': None,
        'headline': 'IV context unavailable',
        'blurb': 'Need implied volatility on the near expiry to build a skew read.',
    }
    if S is None:
        return out

    nearest = _nearest_expiration(calls if (calls is not None and not calls.empty) else puts)
    if nearest is None:
        return out

    def _filter_exp(df):
        if df is None or getattr(df, 'empty', True):
            return pd.DataFrame()
        if 'expiration_date' in df.columns:
            return df[df['expiration_date'] == nearest].copy()
        expiries = _expiration_series_iso(df)
        if expiries is None:
            return df.copy()
        return df[expiries == nearest].copy()

    c0 = _filter_exp(calls)
    p0 = _filter_exp(puts)
    atm_call = _pick_strike_iv(c0, float(S), side='nearest')
    atm_put = _pick_strike_iv(p0, float(S), side='nearest')
    put_wing = _pick_strike_iv(p0, float(S), side='below')
    call_wing = _pick_strike_iv(c0, float(S), side='above')

    atm_values = [node['iv'] for node in (atm_call, atm_put) if node]
    atm_iv = (sum(atm_values) / len(atm_values)) if atm_values else None
    skew_spread = None
    skew_ratio = None
    if put_wing and call_wing and call_wing['iv'] > 0:
        skew_spread = put_wing['iv'] - call_wing['iv']
        skew_ratio = put_wing['iv'] / call_wing['iv']

    out.update({
        'expiry_text': _format_flow_blotter_expiry(nearest) or 'Near expiry',
        'atm_iv': atm_iv,
        'atm_call_iv': atm_call['iv'] if atm_call else None,
        'atm_put_iv': atm_put['iv'] if atm_put else None,
        'put_wing_iv': put_wing['iv'] if put_wing else None,
        'call_wing_iv': call_wing['iv'] if call_wing else None,
        'put_wing_strike': put_wing['strike'] if put_wing else None,
        'call_wing_strike': call_wing['strike'] if call_wing else None,
        'skew_spread': skew_spread,
        'skew_ratio': skew_ratio,
    })

    try:
        today = datetime.now(pytz.timezone('US/Eastern')).date()
        key = (ticker or '__anon__', nearest, today)
        if key not in _SESSION_IV_BASELINE:
            _SESSION_IV_BASELINE[key] = {'atm_iv': atm_iv, 'skew_spread': skew_spread}
        base = _SESSION_IV_BASELINE[key]
        out['atm_iv_change'] = (
            atm_iv - base['atm_iv']
            if atm_iv is not None and base.get('atm_iv') is not None
            else None
        )
        out['skew_change'] = (
            skew_spread - base['skew_spread']
            if skew_spread is not None and base.get('skew_spread') is not None
            else None
        )
    except Exception:
        out['atm_iv_change'] = None
        out['skew_change'] = None

    spread_pts = (skew_spread * 100.0) if skew_spread is not None else None
    atm_pts = (atm_iv * 100.0) if atm_iv is not None else None
    if spread_pts is None:
        out['headline'] = 'ATM IV read only'
        out['blurb'] = 'Wing skew needs both a downside put wing and upside call wing on the near expiry.'
    elif spread_pts >= 6.0:
        out['headline'] = 'Downside rich'
        out['blurb'] = f"Put wing IV is {spread_pts:.1f} pts over the call wing. Traders are paying up for downside convexity."
    elif spread_pts >= 2.0:
        out['headline'] = 'Put skew firm'
        out['blurb'] = f"Put wing IV is {spread_pts:.1f} pts over the call wing. Downside demand is leading the surface."
    elif spread_pts <= -2.0:
        out['headline'] = 'Upside rich'
        out['blurb'] = f"Call wing IV is {abs(spread_pts):.1f} pts over the put wing. Upside speculation is leading the surface."
    else:
        out['headline'] = 'Skew balanced'
        out['blurb'] = f"ATM IV is {atm_pts:.1f}% with only {abs(spread_pts):.1f} pts between the put and call wings."
    return out


# Phase 3 Stage 3 — Live flow alerts engine
# Module-level state; intentionally module-scoped so it survives across ticks.
_ALERT_COOLDOWNS = {}   # (ticker, alert_id) -> float unix ts of last fire
_IV_BUFFER = collections.defaultdict(lambda: collections.deque(maxlen=30))  # (ticker, side, expiry, strike) -> deque[float]
_LAST_WALLS = {}        # ticker -> {'call_wall': float|None, 'put_wall': float|None}
_VOL_SPIKE_CACHE = {}   # (ticker, date, lo, hi) -> {'ts': float, 'data': {strike: {'avg20', 'curr'}}}


def _alert_cooldown_ok(ticker, alert_id, seconds):
    key = (ticker, alert_id)
    last = _ALERT_COOLDOWNS.get(key, 0.0)
    now_ts = time.time()
    if now_ts - last >= seconds:
        _ALERT_COOLDOWNS[key] = now_ts
        return True
    return False


def _fetch_vol_spike_data(ticker, S, strike_range):
    """Batch-load last-20-min per-strike volume from interval_data, cached 30 s."""
    now_ts = time.time()
    cutoff_ts = int(now_ts) - 20 * 60
    today = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
    lo = S * (1 - strike_range)
    hi = S * (1 + strike_range)
    cache_key = (ticker, today, round(lo, 4), round(hi, 4))
    cached = _VOL_SPIKE_CACHE.get(cache_key)
    if cached and now_ts - cached['ts'] < 30:
        return cached['data']
    try:
        with closing(sqlite_connect()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    SELECT strike, timestamp, net_volume
                    FROM interval_data
                    WHERE ticker = ? AND date = ? AND timestamp >= ?
                      AND strike >= ? AND strike <= ?
                    ORDER BY strike, timestamp
                ''', (ticker, today, cutoff_ts, lo, hi))
                rows = cursor.fetchall()
    except Exception:
        rows = []
    by_strike = {}
    for s_raw, _ts, nvol in rows:
        by_strike.setdefault(float(s_raw), []).append(float(nvol) if nvol is not None else 0.0)
    result = {}
    for strike, vols in by_strike.items():
        if len(vols) < 2:
            continue
        interval_deltas = [float(curr - prev) for prev, curr in zip(vols[:-1], vols[1:])]
        abs_deltas = [abs(delta) for delta in interval_deltas]
        curr = abs_deltas[-1]
        avg20 = (sum(abs_deltas[:-1]) / len(abs_deltas[:-1])) if len(abs_deltas) > 1 else 0.0
        result[strike] = {'avg20': avg20, 'curr': curr}
    _VOL_SPIKE_CACHE[cache_key] = {'ts': now_ts, 'data': result}
    return result


def _extract_key_level_prices(key_levels):
    """Flatten the active key-level payload into numeric prices for alert gating."""
    if not isinstance(key_levels, dict):
        return []
    prices = []
    for key in (
        'call_wall', 'call_wall_2',
        'put_wall', 'put_wall_2',
        'gamma_flip', 'hvl',
        'em_upper', 'em_upper_2',
        'em_lower', 'em_lower_2',
    ):
        node = key_levels.get(key)
        if not isinstance(node, dict):
            continue
        price = node.get('price')
        if price is None:
            continue
        try:
            prices.append(float(price))
        except Exception:
            continue
    return prices


def compute_flow_alerts(ticker, calls, puts, now_iso, S, strike_range=0.02,
                        call_wall=None, put_wall=None, key_levels=None,
                        gate_strike_alerts=True):
    """Return list of flow-level alert dicts. Called from compute_trader_stats."""
    if not ticker or S is None:
        return []
    alerts = []
    active_level_prices = _extract_key_level_prices(key_levels)

    def _passes_key_level_gate(alert_type, strike):
        if alert_type in ('wall_shift', 'gamma_flip_move'):
            return True
        if not gate_strike_alerts:
            return True
        if strike is None or S is None or S <= 0 or not active_level_prices:
            return True
        try:
            proximity = min(abs(float(strike) - lvl) for lvl in active_level_prices) / float(S)
        except Exception:
            return True
        return proximity <= 0.0025

    # 1. Wall shift
    prev_walls = _LAST_WALLS.get(ticker, {})
    _LAST_WALLS[ticker] = {'call_wall': call_wall, 'put_wall': put_wall}
    for wtype, label in (('call_wall', 'Call Wall'), ('put_wall', 'Put Wall')):
        prev = prev_walls.get(wtype)
        curr = {'call_wall': call_wall, 'put_wall': put_wall}[wtype]
        if prev is not None and curr is not None and prev != curr:
            aid = f'wall_shift:{wtype}'
            if _alert_cooldown_ok(ticker, aid, 120):
                alerts.append({
                    'id': aid, 'level': 'flow',
                    'text': f'{label} {prev:.0f} → {curr:.0f}',
                    'strike': curr, 'ts': now_iso, 'detail': None,
                    'alert_type': 'wall_shift',
                    'wall_type': wtype,
                    'direction_classifiable': True,
                    'direction_label': 'structural',
                    'direction_hint': 'Level shift is structural context, not a clean directional flow read',
                })

    # 2. Volume spike (SQLite rolling 20-min baseline)
    try:
        vol_data = _fetch_vol_spike_data(ticker, S, strike_range)
        for strike, d in vol_data.items():
            curr_v, avg20 = d['curr'], d['avg20']
            if curr_v >= max(500.0, 3.0 * avg20):
                aid = f'vol_spike:{strike:.0f}'
                ratio = (curr_v / avg20) if avg20 > 0 else 99.0
                if _passes_key_level_gate('vol_spike', strike) and _alert_cooldown_ok(ticker, aid, 300):
                    alerts.append({
                        'id': aid, 'level': 'flow',
                        'text': f'Vol spike @ {strike:.0f} ({ratio:.1f}× avg)',
                        'strike': float(strike), 'ts': now_iso, 'detail': None,
                        'alert_type': 'vol_spike',
                        'direction_classifiable': False,
                    })
    except Exception as e:
        print(f'[compute_flow_alerts] vol_spike: {e}')

    # 3. Volume / OI ratio unusual (live chain)
    try:
        lo = S * (1 - strike_range)
        hi = S * (1 + strike_range)
        for option_type, df in (('call', calls), ('put', puts)):
            if df is None or getattr(df, 'empty', True):
                continue
            window = df[(df['strike'] >= lo) & (df['strike'] <= hi)]
            for _, row in window.iterrows():
                strike = float(row['strike'])
                oi  = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                expiry_iso = _normalize_expiry_iso(row.get('expiration', row.get('expiration_date')))
                side_label = _flow_blotter_side_label(int(row.get('side', 0) or 0), float(row.get('bid', 0) or 0), float(row.get('ask', 0) or 0))
                ref_price = _estimate_contract_ref_price(row)
                if oi < 100 or vol / oi <= 0.25:
                    continue
                aid = f'voi_ratio:{option_type}:{expiry_iso}:{strike:.0f}'
                if _passes_key_level_gate('voi_ratio', strike) and _alert_cooldown_ok(ticker, aid, 600):
                    direction_meta = _build_contract_direction_meta(
                        option_type=option_type,
                        strike=strike,
                        S=S,
                        expiry_iso=expiry_iso,
                        side=side_label,
                        premium_est=max(vol, 0.0) * max(ref_price, 0.0) * 100.0,
                        pace_hint=1.0,
                        voi_hint=(vol / oi) if oi > 0 else float(vol),
                    )
                    alerts.append({
                        'id': aid, 'level': 'flow',
                        'text': f'Heavy vol/OI @ {strike:.0f} ({vol/oi:.2f})',
                        'strike': strike, 'ts': now_iso, 'detail': None,
                        'alert_type': 'voi_ratio',
                        'option_type': option_type,
                        'expiry_iso': expiry_iso,
                        **direction_meta,
                    })
    except Exception as e:
        print(f'[compute_flow_alerts] voi_ratio: {e}')

    # 4. IV surge (in-process ring buffer; resets on restart — acceptable)
    try:
        lo = S * (1 - strike_range)
        hi = S * (1 + strike_range)
        for option_type, df in (('call', calls), ('put', puts)):
            if df is None or getattr(df, 'empty', True):
                continue
            window = df[(df['strike'] >= lo) & (df['strike'] <= hi)]
            for _, row in window.iterrows():
                strike = float(row['strike'])
                expiry_iso = _normalize_expiry_iso(row.get('expiration', row.get('expiration_date')))
                curr_iv = float(row.get('impliedVolatility', 0) or 0)
                side_label = _flow_blotter_side_label(int(row.get('side', 0) or 0), float(row.get('bid', 0) or 0), float(row.get('ask', 0) or 0))
                ref_price = _estimate_contract_ref_price(row)
                vol = float(row.get('volume', 0) or 0)
                oi = float(row.get('openInterest', 0) or 0)
                if curr_iv <= 0:
                    continue
                buffer_key = (ticker, option_type, expiry_iso, round(strike, 4))
                buf = _IV_BUFFER[buffer_key]
                buf.append(curr_iv)
                if len(buf) < 5:
                    continue
                mu  = sum(buf) / len(buf)
                std = (sum((x - mu) ** 2 for x in buf) / len(buf)) ** 0.5
                if std < 0.001:
                    continue
                z = (curr_iv - mu) / std
                if z > 2.0:
                    aid = f'iv_surge:{option_type}:{expiry_iso}:{strike:.0f}'
                    if _passes_key_level_gate('iv_surge', strike) and _alert_cooldown_ok(ticker, aid, 600):
                        detail = f'Expiry {expiry_iso}' if expiry_iso else None
                        direction_meta = _build_contract_direction_meta(
                            option_type=option_type,
                            strike=strike,
                            S=S,
                            expiry_iso=expiry_iso,
                            side=side_label,
                            premium_est=max(vol, 0.0) * max(ref_price, 0.0) * 100.0,
                            pace_hint=max(1.0, z),
                            voi_hint=(vol / oi) if oi > 0 else float(vol),
                        )
                        alerts.append({
                            'id': aid, 'level': 'flow',
                            'text': f'{option_type.capitalize()} IV surge @ {strike:.0f} (+{z:.1f}σ)',
                            'strike': strike, 'ts': now_iso, 'detail': detail,
                            'alert_type': 'iv_surge',
                            'option_type': option_type,
                            'expiry_iso': expiry_iso,
                            **direction_meta,
                        })
    except Exception as e:
        print(f'[compute_flow_alerts] iv_surge: {e}')

    return alerts


def compute_trader_stats(calls, puts, S, strike_range=0.02, selected_expiries=None,
                         delta_adjusted: bool = False, calculate_in_notional: bool = True,
                         ticker: str = None, gate_strike_alerts: bool = True,
                         scope_id: str = None):
    """High-level trader KPIs + a short alerts list, for the header strip.

    Reuses compute_key_levels for the wall/flip/EM lookups so we don't drift
    between the chart lines and the KPI strip.
    """
    out = {
        'net_gex': None,              # dollar-notional net GEX in the window
        'net_dex': None,              # dollar-notional net DEX in the window (Phase 3)
        'hedge_per_1pct': None,       # dollar-notional dealer hedge for ±1% move
        'regime': None,               # 'Long Gamma' | 'Short Gamma'
        'em_move': None, 'em_upper': None, 'em_lower': None, 'em_pct': None,
        'call_wall': None, 'put_wall': None, 'gamma_flip': None,
        'spot': float(S) if S is not None else None,
        'alerts': [],
        # Stage 2 — dealer hedge impact block
        'hedge_on_up_1pct':   None,
        'hedge_on_down_1pct': None,
        'vanna_delta_shift_per_1volpt': None,
        'charm_by_close':     None,
        # Stage 3 — scenario stress table
        'scenarios': [],
        # Phase 3 Stage 2 — alerts rail card payloads
        'chain_activity': None,
        'profile':        None,
        'session_deltas': None,
        'level_deltas':   None,
        'iv_context':     None,
        'flow_pulse':     [],
        'flow_pulse_summary': {'label': 'mixed', 'score': 0.0, 'weighted_premium': 0.0, 'gross_premium': 0.0, 'hedge_share': 0.0},
    }
    if S is None:
        return out

    levels = compute_key_levels(calls, puts, S, selected_expiries=selected_expiries, strike_range=strike_range)
    if levels.get('call_wall'):  out['call_wall']  = levels['call_wall']['price']
    if levels.get('put_wall'):   out['put_wall']   = levels['put_wall']['price']
    if levels.get('gamma_flip'): out['gamma_flip'] = levels['gamma_flip']['price']
    if levels.get('em_upper'):
        out['em_upper'] = levels['em_upper']['price']
        out['em_move']  = levels['em_upper']['move']
    if levels.get('em_lower'):   out['em_lower']   = levels['em_lower']['price']
    if out['em_move'] is not None and S:
        out['em_pct'] = round(out['em_move'] / S * 100, 2)

    def _window_sum(df, col='GEX'):
        if df is None or df.empty or col not in df.columns:
            return 0.0
        if selected_expiries and 'expiration_date' in df.columns:
            df = df[df['expiration_date'].isin(selected_expiries)]
        lo = S * (1 - strike_range); hi = S * (1 + strike_range)
        f = df[(df['strike'] >= lo) & (df['strike'] <= hi)]
        return float(f[col].sum()) if not f.empty else 0.0

    call_gex = _window_sum(calls)
    put_gex  = _window_sum(puts)
    net_gex  = call_gex - put_gex
    out['net_gex'] = net_gex
    # A 1% spot move requires dealers to re-hedge ~1% of the gross gamma
    # notional; this is the standard back-of-envelope number UW and gammalab
    # display as "Hedging Impact per 1%".
    out['hedge_per_1pct'] = 0.01 * net_gex

    # Net DEX (window). Calls carry positive delta, puts negative; summing
    # the per-strike DEX column gives signed dealer delta exposure directly.
    dex_call = _window_sum(calls, col='DEX')
    dex_put  = _window_sum(puts,  col='DEX')
    out['net_dex'] = dex_call + dex_put

    # Dealer Hedge Impact block (Stage 2)
    # Spot ±1%: signed dealer hedge flow for the move direction.
    # Long-gamma posture fades moves (sell strength / buy weakness);
    # short-gamma posture reinforces them (buy strength / sell weakness).
    out['hedge_on_up_1pct']   = -0.01 * net_gex
    out['hedge_on_down_1pct'] = +0.01 * net_gex

    # Vanna delta-shift per +1 vol point. VEX row values already carry the *0.01
    # factor (see compute_greek_exposures :1408), so the window sum is directly
    # "Δ$ per +1 vol point". Negate on the frontend for the -1 vol side.
    vex_call = _window_sum(calls, col='VEX')
    vex_put  = _window_sum(puts,  col='VEX')
    out['vanna_delta_shift_per_1volpt'] = vex_call + vex_put

    # Charm-by-close. Row Charm carries /365 (see :1412) → per *calendar day*.
    # Fraction of calendar day from now to 16:00 ET is hours_left / 24
    # (not /6.5 — that would conflate calendar-day and session-day units).
    try:
        now_et = datetime.now(pytz.timezone('US/Eastern'))
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        hours_left = max(0.0, (close_et - now_et).total_seconds() / 3600.0)
        charm_call = _window_sum(calls, col='Charm')
        charm_put  = _window_sum(puts,  col='Charm')
        out['charm_by_close'] = (charm_call + charm_put) * (hours_left / 24.0)
    except Exception:
        out['charm_by_close'] = None

    if out['gamma_flip'] is not None:
        out['regime'] = 'Long Gamma' if S >= out['gamma_flip'] else 'Short Gamma'
    else:
        out['regime'] = 'Long Gamma' if net_gex >= 0 else 'Short Gamma'

    # Phase 3 Stage 2 — chain activity, gamma profile, session deltas
    try:
        call_oi  = _window_sum(calls, col='openInterest')
        put_oi   = _window_sum(puts,  col='openInterest')
        call_vol = _window_sum(calls, col='volume')
        put_vol  = _window_sum(puts,  col='volume')
        total_vol = call_vol + put_vol
        sentiment = 0.0
        if total_vol > 0:
            sentiment = (call_vol - put_vol) / total_vol  # -1..+1
        out['chain_activity'] = {
            'call_oi': call_oi,
            'put_oi': put_oi,
            'call_vol': call_vol,
            'put_vol': put_vol,
            'oi_call_share': (call_oi / (call_oi + put_oi)) if (call_oi + put_oi) > 0 else None,
            'vol_call_share': (call_vol / total_vol) if total_vol > 0 else None,
            'oi_cp_ratio':  (call_oi  / put_oi)  if put_oi  > 0 else None,
            'vol_cp_ratio': (call_vol / put_vol) if put_vol > 0 else None,
            'sentiment':    sentiment,
        }
    except Exception as e:
        print(f"[compute_trader_stats] chain_activity build failed: {e}")
        out['chain_activity'] = None

    is_long = (out['regime'] == 'Long Gamma')
    out['profile'] = {
        'regime':   out['regime'],
        'headline': 'Positive Gamma' if is_long else 'Negative Gamma',
        'blurb':    'dealer hedging dampens moves' if is_long else 'dealer hedging amplifies moves',
    }

    try:
        out['session_deltas'] = _compute_session_deltas(
            ticker, out['net_gex'], out['net_dex'], scope_id=scope_id
        )
    except Exception as e:
        print(f"[compute_trader_stats] session_deltas failed: {e}")
        out['session_deltas'] = None

    try:
        out['level_deltas'] = _compute_level_session_deltas(ticker, out, scope_id=scope_id)
    except Exception as e:
        print(f"[compute_trader_stats] level_deltas failed: {e}")
        out['level_deltas'] = None

    try:
        out['centroid_panel'] = build_centroid_panel_payload(ticker) if ticker else None
    except Exception as e:
        print(f"[compute_trader_stats] centroid_panel failed: {e}")
        out['centroid_panel'] = None

    try:
        out['iv_context'] = compute_iv_context(calls, puts, S, ticker=ticker)
    except Exception as e:
        print(f"[compute_trader_stats] iv_context failed: {e}")
        out['iv_context'] = None

    try:
        out['flow_pulse'] = build_flow_pulse_snapshot(
            ticker=ticker,
            calls=calls,
            puts=puts,
            S=S,
            strike_range=strike_range,
            top_n=5,
        ) if ticker else []
    except Exception as e:
        print(f"[compute_trader_stats] flow_pulse failed: {e}")
        out['flow_pulse'] = []
    try:
        out['flow_pulse_summary'] = summarize_flow_pulse(out.get('flow_pulse') or [])
    except Exception as e:
        print(f"[compute_trader_stats] flow_pulse summary failed: {e}")
        out['flow_pulse_summary'] = {'label': 'mixed', 'score': 0.0, 'weighted_premium': 0.0, 'gross_premium': 0.0, 'hedge_share': 0.0}

    # Scenario GEX table (Stage 3). Seven rows: current + ±2% spot + ±5 vol pts
    # + two diagonals. "Current" mirrors out['net_gex'] exactly so the table can't
    # disagree with the KPI strip; the other 6 re-sum row-by-row at shifted
    # spot/IV via _recompute_gex_row.
    try:
        base_abs = abs(out['net_gex']) if out['net_gex'] else 1.0
        def _mag(v):
            if v is None or not base_abs:
                return 'low'
            r = abs(v) / max(base_abs, 1.0)
            return 'high' if r >= 0.75 else ('med' if r >= 0.35 else 'low')
        scenarios_spec = [
            ('Current',      0.0,    0.0),
            ('+2% spot',    +0.02,   0.0),
            ('-2% spot',    -0.02,   0.0),
            ('+5 vol',       0.0,   +0.05),
            ('-5 vol',       0.0,   -0.05),
            ('+2%/-5 vol',  +0.02, -0.05),
            ('-2%/+5 vol',  -0.02, +0.05),
        ]
        rows = []
        for label, ss, ivs in scenarios_spec:
            if ss == 0.0 and ivs == 0.0:
                net = out['net_gex']
                regime = out['regime']
            else:
                r = compute_scenario_gex(
                    calls, puts, S,
                    spot_shift=ss, iv_shift=ivs,
                    strike_range=strike_range,
                    selected_expiries=selected_expiries,
                    delta_adjusted=delta_adjusted,
                    calculate_in_notional=calculate_in_notional,
                )
                net = r['net_gex']
                regime = r['regime']
            rows.append({
                'label': label,
                'net_gex': net,
                'regime': regime,
                'magnitude': _mag(net),
            })
        out['scenarios'] = rows
    except Exception as e:
        print(f"[compute_trader_stats] scenarios build failed: {e}")
        out['scenarios'] = []

    alerts = []
    def _near(a, b, pct):
        return a is not None and b is not None and b > 0 and abs(a - b) / b <= pct
    if _near(S, out['call_wall'], 0.003):
        alerts.append({
            'level': 'warn',
            'text': f"Near Call Wall @ {out['call_wall']:.2f}",
            'alert_type': 'wall_proximity',
            'wall_type': 'call_wall',
            'strike': out['call_wall'],
            'direction_classifiable': True,
            'direction_label': 'structural',
            'direction_hint': 'Level proximity is structural context around spot',
        })
    if _near(S, out['put_wall'], 0.003):
        alerts.append({
            'level': 'warn',
            'text': f"Near Put Wall @ {out['put_wall']:.2f}",
            'alert_type': 'wall_proximity',
            'wall_type': 'put_wall',
            'strike': out['put_wall'],
            'direction_classifiable': True,
            'direction_label': 'structural',
            'direction_hint': 'Level proximity is structural context around spot',
        })
    if _near(S, out['gamma_flip'], 0.005):
        alerts.append({
            'level': 'info',
            'text': f"Approaching Gamma Flip @ {out['gamma_flip']:.2f}",
            'alert_type': 'gamma_flip',
            'strike': out['gamma_flip'],
            'direction_classifiable': True,
            'direction_label': 'structural',
            'direction_hint': 'Gamma flip proximity is structural regime context',
        })
    if out['regime'] == 'Short Gamma':
        alerts.append({
            'level': 'warn',
            'text': 'Short-gamma regime — moves may accelerate',
            'alert_type': 'regime',
            'direction_classifiable': True,
            'direction_label': 'structural',
            'direction_hint': 'Gamma regime is structural volatility context, not a one-sided flow read',
        })
    elif out['regime'] == 'Long Gamma':
        alerts.append({
            'level': 'info',
            'text': 'Long-gamma regime — dealer hedging dampens moves',
            'alert_type': 'regime',
            'direction_classifiable': True,
            'direction_label': 'structural',
            'direction_hint': 'Gamma regime is structural volatility context, not a one-sided flow read',
        })

    # Stamp existing rule-based alerts with id + ts (backwards-compatible)
    now_iso = datetime.now(pytz.UTC).isoformat().replace('+00:00', 'Z')
    for a in alerts:
        a.setdefault('id', f"{a['level']}:{hash(a['text']) & 0xffff}")
        a.setdefault('ts', now_iso)

    # Append live flow alerts
    try:
        flow = compute_flow_alerts(
            ticker, calls, puts, now_iso, S,
            strike_range=strike_range,
            call_wall=out.get('call_wall'),
            put_wall=out.get('put_wall'),
            key_levels=levels,
            gate_strike_alerts=gate_strike_alerts,
        )
        alerts.extend(flow)
    except Exception as e:
        print(f'[compute_trader_stats] compute_flow_alerts failed: {e}')

    # Contract-level acceleration alerts from the in-process pulse snapshot.
    try:
        for pulse in (out.get('flow_pulse') or [])[:3]:
            vol_1m = float(pulse.get('vol_delta_1m') or 0.0)
            premium_1m = float(pulse.get('premium_delta_1m') or 0.0)
            pace_1m = float(pulse.get('pace_1m') or 0.0)
            if vol_1m < 250 or premium_1m < 25000 or pace_1m < 2.0:
                continue
            strike = float(pulse.get('strike') or 0.0)
            expiry_text = pulse.get('expiry_text') or ''
            contract_label = f"{strike:.0f}{'C' if pulse.get('option_type') == 'call' else 'P'}"
            aid = f"flow_pulse:{pulse.get('key')}"
            if not _alert_cooldown_ok(ticker, aid, 180):
                continue
            alerts.append({
                'id': aid,
                'level': 'flow',
                'text': f"1m {pulse.get('option_type', 'flow')} burst {contract_label} {expiry_text}".strip(),
                'strike': strike,
                'ts': now_iso,
                'detail': f"+{int(vol_1m):,} vol · {pace_1m:.1f}x pace · ~${format_large_number(premium_1m)} est premium",
                'alert_type': 'flow_pulse',
                'option_type': pulse.get('option_type'),
                'expiry_iso': pulse.get('expiry_iso'),
                'expiry_text': expiry_text,
                'direction_classifiable': True,
                'direction_label': pulse.get('lean_label'),
                'direction_score': pulse.get('lean_score'),
                'direction_hint': pulse.get('lean_hint'),
                'moneyness_band': pulse.get('moneyness_band'),
                'moneyness_pct': pulse.get('moneyness_pct'),
                'side': pulse.get('side'),
                'dte_days': pulse.get('dte_days'),
            })
    except Exception as e:
        print(f'[compute_trader_stats] flow_pulse alerts failed: {e}')

    out['alerts'] = alerts
    return out


def prepare_price_chart_data(price_data, calls=None, puts=None, exposure_levels_types=[],
                              exposure_levels_count=3, call_color=CALL_COLOR, put_color=PUT_COLOR,
                              strike_range=0.1, use_heikin_ashi=False,
                              highlight_max_level=False, max_level_color='#800080',
                              coloring_mode='Linear Intensity', ticker=None,
                              selected_expiries=None):
    """Return raw OHLCV + overlay data as JSON for TradingView Lightweight Charts rendering."""
    import json as _json

    # Handle backward compatibility
    if isinstance(exposure_levels_types, str):
        if exposure_levels_types == 'None':
            exposure_levels_types = []
        else:
            exposure_levels_types = [exposure_levels_types]

    if not price_data or 'candles' not in price_data or not price_data['candles']:
        return _json.dumps({'error': 'No price data'})

    candles = filter_market_hours(price_data['candles'])
    if not candles:
        return _json.dumps({'error': 'No market-hour candles'})

    est = pytz.timezone('US/Eastern')
    current_date = datetime.now(est).date()

    # Deduplicate and sort
    unique_candles = {}
    for c in candles:
        t = datetime.fromtimestamp(c['datetime'] / 1000, est)
        unique_candles[t] = c
    sorted_candles = [c for _, c in sorted(unique_candles.items(), key=lambda x: x[0])]

    # Filter to current day
    current_day_candles = [c for c in sorted_candles
                           if datetime.fromtimestamp(c['datetime'] / 1000, est).date() == current_date]
    display_date = current_date
    if not current_day_candles:
        most_recent_date = max(
            datetime.fromtimestamp(c['datetime'] / 1000, est).date() for c in sorted_candles)
        display_date = most_recent_date
        current_day_candles = [c for c in sorted_candles
                               if datetime.fromtimestamp(c['datetime'] / 1000, est).date() == most_recent_date]

    # Display the full multi-day window so the chart shows ~20+ RTH sessions.
    # current_day_candles is still computed above because current_day_start_time
    # (daily VWAP anchor) depends on it.
    if use_heikin_ashi:
        display_candles = convert_to_heikin_ashi(sorted_candles)
    else:
        display_candles = sorted_candles

    # Previous day close
    previous_day_close = None
    for c in reversed(sorted_candles):
        t = datetime.fromtimestamp(c['datetime'] / 1000, est)
        if t.date() < current_date:
            previous_day_close = c['close']
            break

    # Build Lightweight Charts candle data (time in seconds UTC)
    lc_candles = []
    lc_volume = []
    for i, c in enumerate(display_candles):
        ts = int(c['datetime'] / 1000)
        lc_candles.append({'time': ts, 'open': c['open'], 'high': c['high'],
                           'low': c['low'], 'close': c['close']})
        is_up = c['close'] >= c['open'] if i == 0 else c['close'] >= display_candles[i - 1]['close']
        lc_volume.append({'time': ts, 'value': c['volume'],
                          'color': call_color if is_up else put_color})

    # Multi-day raw candles for indicator warmup (SMA200, EMA, etc. need prior-day history)
    lc_indicator_candles = [
        {'time': int(c['datetime'] / 1000), 'open': c['open'], 'high': c['high'],
         'low': c['low'], 'close': c['close'], 'volume': c.get('volume', 0)}
        for c in sorted_candles
    ]
    current_day_start_time = int(current_day_candles[0]['datetime'] / 1000) if current_day_candles else 0

    current_price = display_candles[-1]['close'] if display_candles else 0
    last_candle = display_candles[-1] if display_candles else None
    last_candle_up = (last_candle['close'] >= last_candle['open']) if last_candle else True

    historical_exposure_levels, historical_expected_moves = build_historical_levels_overlay(
        ticker=ticker,
        display_date=display_date.strftime('%Y-%m-%d') if hasattr(display_date, 'strftime') else str(display_date),
        chart_times=[c['time'] for c in lc_candles],
        latest_price=current_price,
        strike_range=strike_range,
        selected_types=exposure_levels_types,
        levels_count=exposure_levels_count,
        call_color=call_color,
        put_color=put_color,
        highlight_max_level=highlight_max_level,
        max_level_color=max_level_color,
        coloring_mode=coloring_mode,
    )

    # Compute exposure levels
    exposure_levels = []
    expected_moves = []

    if exposure_levels_types and calls is not None and puts is not None:
        min_strike = current_price * (1 - strike_range)
        max_strike = current_price * (1 + strike_range)
        range_calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)]
        range_puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)]

        dash_map = ['dashed', 'dotted', 'large_dashed', 'dotted', 'dashed']
        all_top_levels = []

        for i, etype in enumerate(exposure_levels_types):
            if etype.lower() == 'expected move':
                expected_move_snapshot = calculate_expected_move_snapshot(
                    calls, puts, current_price, selected_expiries=selected_expiries
                )
                if expected_move_snapshot:
                    expected_moves.append({
                        'upper': round(expected_move_snapshot['upper'], 2),
                        'lower': round(expected_move_snapshot['lower'], 2)
                    })
                continue

            col_name = etype
            if etype in ('Vanna', 'VEX'):
                col_name = 'VEX'
            if etype == 'AbsGEX':
                col_name = 'GEX'
            if etype == 'Volume':
                col_name = 'volume'

            if col_name in range_calls.columns and col_name in range_puts.columns:
                call_ex = range_calls.groupby('strike')[col_name].sum().to_dict() if not range_calls.empty else {}
                put_ex = range_puts.groupby('strike')[col_name].sum().to_dict() if not range_puts.empty else {}
                all_strikes_set = set(call_ex.keys()) | set(put_ex.keys())
                levels = {}
                for strike in all_strikes_set:
                    c_val = call_ex.get(strike, 0)
                    p_val = put_ex.get(strike, 0)
                    if etype == 'GEX':
                        net_val = c_val - p_val
                    elif etype == 'AbsGEX':
                        net_val = abs(c_val) + abs(p_val)
                    elif etype == 'Volume':
                        net_val = c_val - p_val
                    else:
                        net_val = c_val + p_val
                    levels[strike] = net_val

                top = sorted(levels.items(), key=lambda x: abs(x[1]), reverse=True)[:exposure_levels_count]
                for strike, val in top:
                    all_top_levels.append((strike, val, etype, i))

        # Max per type for highlight
        max_abs_by_type = {}
        if highlight_max_level:
            for strike, val, etype, tidx in all_top_levels:
                if etype not in max_abs_by_type or abs(val) > max_abs_by_type[etype]:
                    max_abs_by_type[etype] = abs(val)

        type_max_vals = {}
        for strike, val, etype, tidx in all_top_levels:
            if etype not in type_max_vals or abs(val) > type_max_vals[etype]:
                type_max_vals[etype] = abs(val)

        for strike, val, etype, type_index in all_top_levels:
            type_max_val = type_max_vals.get(etype, 1) or 1
            is_max = (highlight_max_level
                      and max_abs_by_type.get(etype, 0) > 0
                      and abs(val) == max_abs_by_type[etype])

            if is_max:
                intensity = 1.0
            elif coloring_mode == 'Solid':
                intensity = 1.0
            elif coloring_mode == 'Ranked Intensity':
                intensity = 0.1 + 0.9 * ((abs(val) / type_max_val) ** 3)
            else:  # Linear Intensity (default)
                intensity = 0.3 + 0.7 * (abs(val) / type_max_val)

            color = max_level_color if is_max else (call_color if val >= 0 else put_color)
            line_width = 2 if is_max else 1

            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            rgba = f'rgba({r},{g},{b},{intensity:.2f})'

            display_name = etype
            if etype == 'VEX':
                display_name = 'Vanna'
            if etype == 'AbsGEX':
                display_name = 'Abs GEX'

            exposure_levels.append({
                'price': float(strike),
                'value': float(val),
                'type': display_name,
                'color': rgba,
                'line_width': line_width,
                'dash_style': dash_map[type_index % len(dash_map)],
                'is_max': is_max,
                'label': f"{display_name}: {format_large_number(val)}"
            })

    return _json.dumps({
        'candles': lc_candles,
        'volume': lc_volume,
        'previous_day_close': previous_day_close,
        'current_price': current_price,
        'call_color': call_color,
        'put_color': put_color,
        'use_heikin_ashi': use_heikin_ashi,
        'last_candle_up': last_candle_up,
        'exposure_levels': exposure_levels,
        'expected_moves': expected_moves,
        'historical_exposure_levels': historical_exposure_levels,
        'historical_expected_moves': historical_expected_moves,
        'indicator_candles': lc_indicator_candles,
        'current_day_start_time': current_day_start_time,
    })


def create_large_trades_table(calls, puts, S, strike_range, call_color=CALL_COLOR, put_color=PUT_COLOR, selected_expiries=None, ticker=None):
    """Create a flow-oriented blotter from the option chain snapshot."""
    try:
        spot = float(S)
    except Exception:
        spot = None

    max_rows = 120
    min_strike = spot * (1 - strike_range) if spot and np.isfinite(spot) else None
    max_strike = spot * (1 + strike_range) if spot and np.isfinite(spot) else None

    pulse_snapshot = build_flow_pulse_snapshot(ticker, calls, puts, S, strike_range=strike_range, top_n=4000) if ticker else []
    pulse_map = {(row['option_type'], row['expiry_iso'], round(float(row['strike']), 4)): row for row in pulse_snapshot}

    def build_rows(df, is_put=False):
        if df is None or df.empty:
            return []
        working = df.copy()
        if 'volume' in working.columns:
            working = working[pd.to_numeric(working['volume'], errors='coerce').fillna(0) > 0]
        if min_strike is not None and max_strike is not None and 'strike' in working.columns:
            strikes = pd.to_numeric(working['strike'], errors='coerce')
            working = working[(strikes >= min_strike) & (strikes <= max_strike)]

        rows = []
        for _, row in working.iterrows():
            strike = float(row.get('strike', 0) or 0)
            bid = float(row.get('bid', 0) or 0)
            ask = float(row.get('ask', 0) or 0)
            last = float(row.get('lastPrice', 0) or 0)
            volume = int(float(row.get('volume', 0) or 0))
            open_interest = int(float(row.get('openInterest', 0) or 0))
            mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else (bid if bid > 0 else ask)
            ref_price = last if last > 0 else mid
            premium = max(ref_price, 0) * max(volume, 0) * 100
            if premium <= 0:
                continue

            expiry_value = row.get('expiration', row.get('expiration_date'))
            expiry_value_norm = pd.to_datetime(expiry_value, errors='coerce')
            expiry_iso = expiry_value_norm.strftime('%Y-%m-%d') if not pd.isna(expiry_value_norm) else ''
            trade_ts = _coerce_epoch_ms(row.get('tradeTimeInLong'))
            quote_ts = _coerce_epoch_ms(row.get('quoteTimeInLong'))
            event_ts = trade_ts or quote_ts
            voi = (volume / open_interest) if open_interest > 0 else float(volume)
            side_label = _flow_blotter_side_label(int(row.get('side', 0) or 0), bid, ask)
            side_text = {'ask': 'Ask', 'bid': 'Bid', 'mid': 'Mid', 'unknown': 'Unknown'}[side_label]
            distance = abs(strike - spot) if spot and np.isfinite(spot) else 0.0
            option_type = 'put' if is_put else 'call'
            pulse = pulse_map.get((option_type, expiry_iso, round(strike, 4)), {})
            vol_1m = float(pulse.get('vol_delta_1m') or 0.0)
            pace_1m = float(pulse.get('pace_1m') or 0.0)
            time_title = 'Latest trade time from chain snapshot' if trade_ts else (
                'Latest quote time from chain snapshot' if quote_ts else 'Chain snapshot does not include per-contract time here'
            )
            rows.append({
                'time_text': _format_flow_blotter_time(event_ts),
                'time_value': event_ts or 0,
                'time_title': time_title,
                'type_key': option_type,
                'type_label': 'Put' if is_put else 'Call',
                'type_color': put_color if is_put else call_color,
                'strike': strike,
                'expiry_text': _format_flow_blotter_expiry(expiry_value),
                'expiry_value': expiry_iso,
                'last': last,
                'market_text': _flow_blotter_market_text(bid, mid, ask),
                'market_value': mid,
                'volume': volume,
                'open_interest': open_interest,
                'voi': voi,
                'voi_text': 'new' if open_interest <= 0 and volume > 0 else f"{voi:.2f}x",
                'premium': premium,
                'premium_text': f"${format_large_number(premium)}",
                'side_key': side_label,
                'side_text': side_text,
                'distance': distance,
                'vol_1m': vol_1m,
                'vol_1m_text': ('+' + format_large_number(vol_1m)) if vol_1m > 0 else '—',
                'pace_1m': pace_1m,
                'pace_1m_text': (f"{pace_1m:.1f}x" if pace_1m > 0 else '—'),
            })
        return rows

    flow_rows = build_rows(calls, is_put=False) + build_rows(puts, is_put=True)
    has_event_times = any(row['time_value'] > 0 for row in flow_rows)
    if has_event_times:
        flow_rows.sort(
            key=lambda row: (
                row['time_value'],
                row['premium'],
                row['voi'],
                row['volume'],
                -row['distance'],
            ),
            reverse=True,
        )
        default_sort = 'time'
        sort_note = 'Sorted by latest chain timestamp, then premium.'
    else:
        flow_rows.sort(
            key=lambda row: (
                row['premium'],
                row['voi'],
                row['volume'],
                -row['distance'],
            ),
            reverse=True,
        )
        default_sort = 'premium'
        sort_note = 'No per-contract timestamps in this chain snapshot, so rows rank by premium.'

    visible_rows = flow_rows[:max_rows]
    total_premium = sum(row['premium'] for row in visible_rows)
    hidden_count = max(0, len(flow_rows) - len(visible_rows))

    chart_title = 'Flow Blotter'
    if selected_expiries and len(selected_expiries) > 1:
        chart_title = f"Flow Blotter ({len(selected_expiries)} expiries)"

    subtitle_parts = [f"{len(flow_rows):,} active contracts in range"]
    if hidden_count:
        subtitle_parts.append(f"showing top {len(visible_rows):,} by signal")
    subtitle_parts.append(sort_note)
    subtitle = ' · '.join(subtitle_parts)

    rows_html = []
    for row in visible_rows:
        rows_html.append(f'''
                    <tr data-flow-row="1"
                        data-option-type="{row['type_key']}"
                        data-time="{row['time_value']}"
                        data-strike="{row['strike']}"
                        data-expiry="{row['expiry_value']}"
                        data-last="{row['last']}"
                        data-market="{row['market_value']}"
                        data-volume="{row['volume']}"
                        data-open-interest="{row['open_interest']}"
                        data-voi="{row['voi']}"
                        data-premium="{row['premium']}"
                        data-vol1m="{row['vol_1m']}"
                        data-pace="{row['pace_1m']}"
                        data-side="{row['side_key']}">
                        <td class="flow-blotter__time" title="{row['time_title']}">{row['time_text']}</td>
                        <td><span class="flow-blotter__badge flow-blotter__badge--{row['type_key']}" style="--flow-badge-color:{row['type_color']};">{row['type_label']}</span></td>
                        <td class="num">{row['strike']:.0f}</td>
                        <td>{row['expiry_text']}</td>
                        <td class="num">{row['last']:.2f}</td>
                        <td class="flow-blotter__market num">{row['market_text']}</td>
                        <td class="num">{row['volume']:,}</td>
                        <td class="num">{row['open_interest']:,}</td>
                        <td class="num">{row['voi_text']}</td>
                        <td class="num">{row['vol_1m_text']}</td>
                        <td class="num">{row['pace_1m_text']}</td>
                        <td class="num">{row['premium_text']}</td>
                        <td><span class="flow-blotter__side flow-blotter__side--{row['side_key']}">{row['side_text']}</span></td>
                    </tr>
        ''')

    if not rows_html:
        rows_html.append('''
                    <tr>
                        <td colspan="13" class="flow-blotter__empty">
                            No in-range contracts with non-zero day volume are available for this snapshot.
                        </td>
                    </tr>
        ''')

    return f'''
    <style>
        .flow-blotter {{
            display: flex;
            flex-direction: column;
            height: 100%;
            min-height: 0;
            background: var(--bg-1);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            overflow: hidden;
        }}
        .flow-blotter__header {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            padding: 10px 12px 8px;
            border-bottom: 1px solid var(--border);
            background: linear-gradient(180deg, rgba(59,130,246,0.08), rgba(59,130,246,0));
            flex-wrap: wrap;
        }}
        .flow-blotter__title {{
            font-size: 14px;
            font-weight: 700;
            color: var(--fg-0);
        }}
        .flow-blotter__meta,
        .flow-blotter__summary,
        .flow-blotter__note {{
            font-size: 11px;
            color: var(--fg-1);
        }}
        .flow-blotter__controls {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .flow-blotter__segmented {{
            display: inline-flex;
            background: var(--bg-0);
            border: 1px solid var(--border);
            border-radius: 999px;
            padding: 2px;
            gap: 2px;
        }}
        .flow-blotter__chip,
        .flow-blotter__reset {{
            border: 1px solid var(--border);
            background: var(--bg-2);
            color: var(--fg-1);
            border-radius: 999px;
            padding: 4px 9px;
            font-size: 11px;
            cursor: pointer;
            transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
        }}
        .flow-blotter__segmented .flow-blotter__chip {{
            border: none;
            background: transparent;
        }}
        .flow-blotter__chip:hover,
        .flow-blotter__reset:hover {{
            color: var(--fg-0);
            border-color: var(--border-strong);
        }}
        .flow-blotter__chip.active {{
            background: var(--accent);
            color: var(--fg-0);
        }}
        .flow-blotter__threshold {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            color: var(--fg-1);
            background: var(--bg-0);
            border: 1px solid var(--border);
            border-radius: 999px;
            padding: 0 8px;
            min-height: 30px;
        }}
        .flow-blotter__threshold input {{
            width: 96px;
            border: none;
            outline: none;
            background: transparent;
            color: var(--fg-0);
            font: inherit;
        }}
        .flow-blotter__summary,
        .flow-blotter__note {{
            padding: 0 12px 8px;
        }}
        .flow-blotter__table-wrap {{
            flex: 1;
            min-height: 0;
            overflow: auto;
        }}
        .flow-blotter table {{
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
        }}
        .flow-blotter thead th {{
            position: sticky;
            top: 0;
            z-index: 1;
            background: var(--bg-2);
            border-bottom: 1px solid var(--border);
            text-align: left;
            padding: 0;
        }}
        .flow-blotter__sort {{
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
            background: transparent;
            border: none;
            color: var(--fg-1);
            font-size: 10px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            padding: 7px 10px;
            cursor: pointer;
        }}
        .flow-blotter__sort:hover {{
            color: var(--fg-0);
        }}
        .flow-blotter__sort-indicator {{
            color: var(--fg-2);
            font-size: 11px;
        }}
        .flow-blotter tbody td {{
            padding: 7px 10px;
            border-bottom: 1px solid var(--border);
            color: var(--fg-0);
            font-size: 11px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .flow-blotter tbody tr:hover td {{
            background: rgba(59,130,246,0.06);
        }}
        .flow-blotter__badge,
        .flow-blotter__side {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 44px;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 10px;
            font-weight: 700;
        }}
        .flow-blotter__badge {{
            color: var(--flow-badge-color);
            background: rgba(255,255,255,0.03);
            border: 1px solid var(--border-strong);
        }}
        .flow-blotter__side--ask {{
            color: var(--call);
            background: rgba(16,185,129,0.10);
            border: 1px solid rgba(16,185,129,0.28);
        }}
        .flow-blotter__side--bid {{
            color: var(--put);
            background: rgba(239,68,68,0.10);
            border: 1px solid rgba(239,68,68,0.28);
        }}
        .flow-blotter__side--mid {{
            color: var(--warn);
            background: rgba(245,158,11,0.10);
            border: 1px solid rgba(245,158,11,0.28);
        }}
        .flow-blotter__side--unknown {{
            color: var(--fg-1);
            background: var(--bg-2);
            border: 1px solid var(--border);
        }}
        .flow-blotter__market,
        .flow-blotter__time {{
            color: var(--fg-1);
        }}
        .flow-blotter__empty {{
            text-align: center;
            color: var(--fg-1);
            padding: 24px 12px;
        }}
        .flow-blotter__empty-state {{
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 14px;
            color: var(--fg-1);
            font-size: 11px;
            border-top: 1px solid var(--border);
        }}
        .flow-blotter__empty-state[hidden] {{
            display: none;
        }}
        @media (max-width: 900px) {{
            .flow-blotter__header {{
                flex-direction: column;
                align-items: stretch;
            }}
            .flow-blotter__controls {{
                justify-content: flex-start;
            }}
        }}
    </style>
    <div class="flow-blotter" data-default-sort="{default_sort}" data-default-dir="desc" data-initial-type="all">
        <div class="flow-blotter__header">
            <div>
                <div class="flow-blotter__title">{chart_title}</div>
                <div class="flow-blotter__meta">{subtitle}</div>
            </div>
            <div class="flow-blotter__controls">
                <div class="flow-blotter__segmented" role="tablist" aria-label="Flow blotter contract type filter">
                    <button type="button" class="flow-blotter__chip active" data-flow-type="all" aria-pressed="true">All</button>
                    <button type="button" class="flow-blotter__chip" data-flow-type="call" aria-pressed="false">Calls</button>
                    <button type="button" class="flow-blotter__chip" data-flow-type="put" aria-pressed="false">Puts</button>
                </div>
                <label class="flow-blotter__threshold">
                    <span>Min prem</span>
                    <input type="number" min="0" step="25000" value="0" data-flow-min-premium>
                </label>
                <button type="button" class="flow-blotter__reset" data-flow-reset>Reset</button>
            </div>
        </div>
        <div class="flow-blotter__summary" data-flow-summary">
            {len(visible_rows):,} shown · Approx premium ${format_large_number(total_premium)}
        </div>
        <div class="flow-blotter__note">
            Chain snapshot view: premium is estimated as last x day volume x 100. 1m ΔVol and Pace come from in-process contract volume history. Side is inferred from last vs. bid/ask and is not a tape classification.
        </div>
        <div class="flow-blotter__empty-state" data-flow-empty hidden>No rows match the current filters.</div>
        <div class="flow-blotter__table-wrap">
            <table>
                <thead>
                    <tr>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="time" data-sort-type="number">Time <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="optionType" data-sort-type="string">Type <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="strike" data-sort-type="number">Strike <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="expiry" data-sort-type="string">Expiry <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="last" data-sort-type="number">Last <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="market" data-sort-type="number">Bid / Mid / Ask <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="volume" data-sort-type="number">Vol <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="openInterest" data-sort-type="number">OI <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="voi" data-sort-type="number">V/OI <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="vol1m" data-sort-type="number">1m ΔVol <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="pace" data-sort-type="number">Pace <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="premium" data-sort-type="number">Premium <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                        <th><button type="button" class="flow-blotter__sort" data-sort-key="side" data-sort-type="string">Side <span class="flow-blotter__sort-indicator" data-sort-indicator>↕</span></button></th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>
        </div>
    </div>
    '''





def create_historical_bubble_levels_chart(ticker, strike_range, call_color='#00FFA3', put_color='#FF3B3B', exposure_type='gamma', absolute=False, highlight_max_level=False, max_level_color='#800080'):
    """Create a chart showing price and exposure (gamma, delta, or vanna) over time for the full session.

    Supports optional highlighting of the max exposure bubble via highlight_max_level and max_level_color.
    If absolute is True and exposure_type == 'gamma', gamma exposures are plotted as absolute values (useful for absolute GEX charts).
    """
    # Get interval data; fall back to most recent session if today has no data
    showing_last_session = False
    interval_data = get_interval_data(ticker)

    if not interval_data:
        last_date = get_last_session_date(ticker, 'interval_data')
        if last_date:
            interval_data = get_interval_data(ticker, last_date)
            showing_last_session = True

    if not interval_data:
        return None
    
    # Get the latest price from the most recent data point to establish strike range
    latest_price = interval_data[-1][1]
    min_strike = latest_price * (1 - strike_range)
    max_strike = latest_price * (1 + strike_range)
    
    # Group data by timestamp (show full session, no time filtering)
    data_by_time = {}
    for row in interval_data:
        timestamp = row[0]
            
        price = row[1]
        strike = row[2]
        net_gamma = row[3]
        net_delta = row[4]
        net_vanna = row[5]
        # Check if net_charm exists (for backward compatibility during readout)
        if len(row) > 6:
            net_charm = row[6] if row[6] is not None else 0
        else:
            net_charm = 0
        # Check if abs_gex_total exists (newer DB schema)
        if len(row) > 7:
            abs_gex_total = row[7] if row[7] is not None else None
        else:
            abs_gex_total = None
        
        # Filter strikes based on fixed strike_range relative to latest price
        if strike < min_strike or strike > max_strike:
            continue  # Skip strikes outside the range
        
        if timestamp not in data_by_time:
            data_by_time[timestamp] = {
                'price': price,
                'strikes': []
            }
        
        # Store the exposure value based on the requested type
        exposure = 0
        if exposure_type == 'gamma':
            exposure = net_gamma
        elif exposure_type == 'delta':
            exposure = net_delta
        elif exposure_type == 'vanna':
            exposure = net_vanna
        elif exposure_type == 'charm':
            exposure = net_charm
        
        if exposure is None:
            exposure = 0
        
        # If absolute flag is set for gamma, prefer stored abs_gex_total (call+put magnitudes)
        if absolute and exposure_type == 'gamma':
            if abs_gex_total is not None:
                exposure = abs_gex_total
            else:
                exposure = abs(exposure)
            
        data_by_time[timestamp]['strikes'].append((strike, exposure))
    
    # Convert to lists for plotting
    timestamps = []
    prices = []
    strikes = []
    exposures = []
    
    # Group exposures by timestamp for per-time scaling
    exposures_by_time = {}
    for timestamp, data in data_by_time.items():
        dt = datetime.fromtimestamp(timestamp)
        for strike, exposure in data['strikes']:
            timestamps.append(dt)
            prices.append(data['price'])
            strikes.append(strike)
            exposures.append(exposure)
            if dt not in exposures_by_time:
                exposures_by_time[dt] = []
            exposures_by_time[dt].append(exposure)
    
    # Calculate max exposure for each time slice
    max_exposure_by_time = {dt: max(abs(e) for e in exposures) for dt, exposures in exposures_by_time.items()}
    
    # Create colors and sizes based on per-time scaling
    colors = []
    bubble_sizes = []
    adjusted_strikes = []  # New list for adjusted strike positions
    
    # Group strikes by timestamp to handle overlaps
    strikes_by_time = {}
    for i, (dt, strike) in enumerate(zip(timestamps, strikes)):
        if dt not in strikes_by_time:
            strikes_by_time[dt] = []
        strikes_by_time[dt].append((i, strike))
    
    # Adjust strike positions to prevent overlap
    for dt, strike_data in strikes_by_time.items():
        # Sort strikes for this timestamp
        strike_data.sort(key=lambda x: x[1])
        
        # Group strikes that are close to each other
        groups = []
        current_group = []
        for idx, strike in strike_data:
            if not current_group:
                current_group.append((idx, strike))
            else:
                # If this strike is close to the last one in the group, add it
                if abs(strike - current_group[-1][1]) < 0.1:  # Adjust this threshold as needed
                    current_group.append((idx, strike))
                else:
                    groups.append(current_group)
                    current_group = [(idx, strike)]
        if current_group:
            groups.append(current_group)
        
        # Adjust positions within each group
        for group in groups:
            if len(group) == 1:
                # Single strike, no adjustment needed
                adjusted_strikes.append(group[0][1])
            else:
                # Multiple strikes, spread them out
                center = sum(s for _, s in group) / len(group)
                spread = 0.1  # Adjust this value to control spread
                for i, (idx, strike) in enumerate(group):
                    # Calculate offset based on position in group
                    offset = (i - (len(group) - 1) / 2) * spread
                    adjusted_strikes.append(strike + offset)
    
    # Create colors and sizes for the adjusted strikes
    hover_sides = []
    formatted_exposures = []
    original_strikes = []
    for i, exposure in enumerate(exposures):
        dt = timestamps[i]
        max_exposure = max_exposure_by_time[dt]
        if max_exposure == 0:
            max_exposure = 1  # Prevent division by zero

        # Calculate color and side label
        if absolute and exposure_type == 'gamma':
            colors.append(get_color_with_opacity(exposure, max_exposure, call_color, True))
            hover_sides.append('Total')
        elif exposure >= 0:
            colors.append(get_color_with_opacity(exposure, max_exposure, call_color, True))
            hover_sides.append('Call')
        else:
            colors.append(get_color_with_opacity(exposure, max_exposure, put_color, True))
            hover_sides.append('Put')

        # Calculate bubble size (scaled to the max exposure for this time slice)
        size = max(4, min(25, abs(exposure) * 20 / max_exposure))
        bubble_sizes.append(size)
        formatted_exposures.append(format_large_number(exposure))
        original_strikes.append(strikes[i])

    # If highlight is enabled, mark the max bubble for each timestamp (historical highlighting)
    if highlight_max_level:
        try:
            # Compute local maximum absolute exposure for each timestamp
            local_max_by_dt = {dt: max(abs(v) for v in vals) for dt, vals in exposures_by_time.items()}

            # Prepare a list of line widths to add an outline to highlighted bubbles
            highlight_line_widths = [0] * len(colors)

            # Iterate through each bubble and mark it if it equals the local max for its timestamp
            for idx, (dt, e) in enumerate(zip(timestamps, exposures)):
                local_max = local_max_by_dt.get(dt, 0)
                if local_max > 0 and abs(e) == local_max:
                    colors[idx] = max_level_color
                    highlight_line_widths[idx] = 4
        except Exception as e:
            print(f"Error computing highlight for historical bubble levels: {e}")

    # Create figure
    fig = go.Figure()

    # Add exposure bubbles for each strike first (bottom layer)
    exposure_name = {
        'gamma': 'Gamma',
        'delta': 'Delta',
        'vanna': 'Vanna',
        'charm': 'Charm'
    }.get(exposure_type, 'Exposure')

    # If absolute gamma is requested, adjust the label
    if absolute and exposure_type == 'gamma':
        exposure_name = 'Gamma (Abs)'

    # Build customdata: [side, original_strike, formatted_exposure]
    bubble_customdata = list(zip(hover_sides, original_strikes, formatted_exposures))

    fig.add_trace(go.Scatter(
        x=timestamps,
        y=adjusted_strikes,
        mode='markers',
        name=exposure_name,
        marker=dict(
            size=bubble_sizes,
            color=colors,
            opacity=1.0,
            line=dict(width=0)
        ),
        customdata=bubble_customdata,
        hovertemplate='<b>%{customdata[0]}</b><br>Strike: $%{customdata[1]:.2f}<br>' + exposure_name + ': %{customdata[2]}<br>Time: %{x|%H:%M}<extra></extra>',
        yaxis='y1'
    ))

    # If highlight was computed above, apply marker line widths and color for outline
    if highlight_max_level and 'highlight_line_widths' in locals():
        try:
            # Find the bubble trace and update its marker line widths
            for i, trace in enumerate(fig.data):
                if trace.name == exposure_name and 'markers' in trace.mode:
                    fig.data[i].update(marker=dict(line=dict(width=highlight_line_widths, color=max_level_color)))
                    break
        except Exception as e:
            print(f"Error applying highlight to bubble trace: {e}")

    # Add price line last (top layer)
    unique_times = sorted(set(timestamps))
    unique_prices = [data_by_time[int(t.timestamp())]['price'] for t in unique_times]
    fig.add_trace(go.Scatter(
        x=unique_times,
        y=unique_prices,
        mode='lines',
        name='Price',
        line=dict(color='gold', width=2),
        hovertemplate='<b>Price</b>: $%{y:.2f}<br>Time: %{x|%H:%M}<extra></extra>',
        yaxis='y1'
    ))
    
    # Update layout
    fig.update_layout(
        title=dict(
            text=f'Historical Bubble Levels - {exposure_name}' + (' (Last Session)' if showing_last_session else ''),
            font=dict(color='#CCCCCC', size=16),
            x=0.5,
            xanchor='center'
        ),
        xaxis=dict(
            title='Time (Full Session)',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333',
            tickformat='%H:%M',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor='#CCCCCC',
            automargin=True
        ),
        yaxis=dict(
            title='Price/Strike',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333'
        ),
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='#CCCCCC'),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color='#CCCCCC'),
            bgcolor=PLOT_THEME['paper_bgcolor']
        ),
        margin=dict(l=50, r=50, t=50, b=20),
        showlegend=True,
        autosize=True,
        hovermode='closest',
        hoverlabel=dict(
            bgcolor=PLOT_THEME['paper_bgcolor'],
            font_size=12,
            font_family="Arial"
        ),
        spikedistance=1000,
        hoverdistance=100
    )

    # Add hover spikes
    fig.update_xaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    fig.update_yaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)

    return fig.to_json()



def create_open_interest_chart(calls, puts, S, strike_range=0.02, call_color=CALL_COLOR, put_color=PUT_COLOR, coloring_mode='Solid', show_calls=True, show_puts=True, show_net=True, selected_expiries=None, horizontal=False, highlight_max_level=False, max_level_color='#800080', max_level_mode='Absolute'):
    # Filter strikes within range
    min_strike = S * (1 - strike_range)
    max_strike = S * (1 + strike_range)
    
    calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)].copy()
    puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)].copy()
    
    # Determine strike interval and aggregate by rounded strikes
    all_strikes = list(calls['strike']) + list(puts['strike'])
    if all_strikes:
        strike_interval = get_strike_interval(all_strikes)
        calls = aggregate_by_strike(calls, ['openInterest'], strike_interval)
        puts = aggregate_by_strike(puts, ['openInterest'], strike_interval)
    
    # Create figure
    fig = go.Figure()
    
    # Calculate max OI for normalization across all data
    max_oi = 1.0
    all_abs_vals = []
    if not calls.empty:
        all_abs_vals.extend(calls['openInterest'].abs().tolist())
    if not puts.empty:
        all_abs_vals.extend(puts['openInterest'].abs().tolist())
    if all_abs_vals:
        max_oi = max(all_abs_vals)
    if max_oi == 0:
        max_oi = 1.0
    
    # Add call OI bars
    if show_calls and not calls.empty:
        call_colors = get_colors(call_color, calls['openInterest'], max_oi, coloring_mode)
            
        if horizontal:
            fig.add_trace(go.Bar(
                y=calls['strike'].tolist(),
                x=calls['openInterest'].tolist(),
                name='Call',
                marker_color=call_colors,
                text=[format_large_number(v) for v in calls['openInterest']],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>OI: %{text}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=calls['strike'].tolist(),
                y=calls['openInterest'].tolist(),
                name='Call',
                marker_color=call_colors,
                text=[format_large_number(v) for v in calls['openInterest']],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>OI: %{text}<extra></extra>',
                marker_line_width=0
            ))
    
    # Add put OI bars (as negative values)
    if show_puts and not puts.empty:
        put_colors = get_colors(put_color, puts['openInterest'], max_oi, coloring_mode)
            
        if horizontal:
            fig.add_trace(go.Bar(
                y=puts['strike'].tolist(),
                x=[-v for v in puts['openInterest'].tolist()],
                name='Put',
                marker_color=put_colors,
                text=[format_large_number(v) for v in puts['openInterest']],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>OI: %{text}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=puts['strike'].tolist(),
                y=[-v for v in puts['openInterest'].tolist()],
                name='Put',
                marker_color=put_colors,
                text=[format_large_number(v) for v in puts['openInterest']],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>OI: %{text}<extra></extra>',
                marker_line_width=0
            ))
    
    # Add net OI bars if enabled
    if show_net and not (calls.empty and puts.empty):
        all_strikes_list = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
        net_oi = []
        
        for strike in all_strikes_list:
            call_val = calls[calls['strike'] == strike]['openInterest'].sum() if not calls.empty else 0
            put_val = puts[puts['strike'] == strike]['openInterest'].sum() if not puts.empty else 0
            net_oi.append(call_val - put_val)
        
        max_net_oi = max(abs(min(net_oi)), abs(max(net_oi))) if net_oi else 1.0
        if max_net_oi == 0:
            max_net_oi = 1.0
        
        net_colors = get_net_colors(net_oi, max_net_oi, call_color, put_color, coloring_mode)
        
        if horizontal:
            fig.add_trace(go.Bar(
                y=all_strikes_list,
                x=net_oi,
                name='Net',
                marker_color=net_colors,
                text=[format_large_number(val) for val in net_oi],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Net OI: %{text}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=all_strikes_list,
                y=net_oi,
                name='Net',
                marker_color=net_colors,
                text=[format_large_number(val) for val in net_oi],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Net OI: %{text}<extra></extra>',
                marker_line_width=0
            ))
    
    if horizontal:
        fig.add_hline(
            y=S,
            line_dash="dash",
            line_color="white",
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="right",
            annotation_font_color="white",
            line_width=1
        )
    else:
        fig.add_vline(
            x=S,
            line_dash="dash",
            line_color="white",
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="top",
            annotation_font_color="white",
            line_width=1
        )
    
    base_title = 'Open Interest by Strike'
    chart_title = base_title
    if selected_expiries and len(selected_expiries) > 1:
        chart_title = f"{base_title} ({len(selected_expiries)} expiries)"
    
    xaxis_config = dict(
        title='',
        title_font=dict(color='#CCCCCC'),
        tickfont=dict(color='#CCCCCC'),
        gridcolor='#333333',
        linecolor='#333333',
        showgrid=False,
        zeroline=True,
        zerolinecolor='#333333',
        automargin=True
    )
    
    yaxis_config = dict(
        title='',
        title_font=dict(color='#CCCCCC'),
        tickfont=dict(color='#CCCCCC'),
        gridcolor='#333333',
        linecolor='#333333',
        showgrid=False,
        zeroline=True,
        zerolinecolor='#333333'
    )
    
    if horizontal:
         yaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False
         ))
    else:
        xaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False,
            tickangle=45,
            tickformat='.0f',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor='#CCCCCC'
        ))

    fig.update_layout(
        title=dict(
            text=chart_title,
            font=dict(color='#CCCCCC', size=16),
            x=0.5,
            xanchor='center'
        ),
        xaxis=xaxis_config,
        yaxis=yaxis_config,
        barmode='relative',
        hovermode='y unified' if horizontal else 'x unified',
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='#CCCCCC'),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=0.95,
            xanchor="right",
            x=1,
            font=dict(color='#CCCCCC'),
            bgcolor=PLOT_THEME['paper_bgcolor']
        ),
        bargap=0.1,
        bargroupgap=0.1,
        margin=dict(l=50, r=50, t=50, b=100),
        hoverlabel=dict(
            bgcolor=PLOT_THEME['paper_bgcolor'],
            font_size=12,
            font_family="Arial"
        ),
        spikedistance=1000,
        hoverdistance=100,
        showlegend=True,
        height=500
    )
    
    fig.update_xaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    fig.update_yaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    
    if highlight_max_level:
        try:
            if max_level_mode == 'Net':
                net_trace_idx = next((i for i, t in enumerate(fig.data) if t.type == 'bar' and t.name == 'Net'), None)
                if net_trace_idx is not None:
                    raw = fig.data[net_trace_idx].x if horizontal else fig.data[net_trace_idx].y
                    if raw:
                        vals = list(raw)
                        total_net = sum(vals)
                        if total_net >= 0:
                            max_bar_idx = vals.index(max(vals))
                        else:
                            max_bar_idx = vals.index(min(vals))
                        line_widths = [0] * len(vals)
                        line_widths[max_bar_idx] = 5
                        fig.data[net_trace_idx].update(marker=dict(
                            line=dict(width=line_widths, color=max_level_color)
                        ))
            else:
                max_abs_val = 0
                max_trace_idx = -1
                max_bar_idx = -1
                for i, trace in enumerate(fig.data):
                    if trace.type == 'bar':
                        vals = trace.x if horizontal else trace.y
                        if vals:
                            abs_vals = [abs(v) for v in vals]
                            if abs_vals:
                                local_max = max(abs_vals)
                                if local_max > max_abs_val:
                                    max_abs_val = local_max
                                    max_trace_idx = i
                                    max_bar_idx = abs_vals.index(local_max)
                if max_trace_idx != -1:
                    vals = fig.data[max_trace_idx].x if horizontal else fig.data[max_trace_idx].y
                    line_widths = [0] * len(vals)
                    line_widths[max_bar_idx] = 5
                    fig.data[max_trace_idx].update(marker=dict(
                        line=dict(width=line_widths, color=max_level_color)
                    ))
        except Exception as e:
            print(f"Error highlighting max level in open interest chart: {e}")

    apply_plotly_theme(fig)
    return fig.to_json()

def create_premium_chart(calls, puts, S, strike_range=0.02, call_color=CALL_COLOR, put_color=PUT_COLOR, coloring_mode='Solid', show_calls=True, show_puts=True, show_net=True, selected_expiries=None, horizontal=False, highlight_max_level=False, max_level_color='#800080', max_level_mode='Absolute'):
    # Filter strikes within range
    min_strike = S * (1 - strike_range)
    max_strike = S * (1 + strike_range)
    
    calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)].copy()
    puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)].copy()
    
    # Determine strike interval and aggregate by rounded strikes
    all_strikes = list(calls['strike']) + list(puts['strike'])
    if all_strikes:
        strike_interval = get_strike_interval(all_strikes)
        calls = aggregate_by_strike(calls, ['lastPrice'], strike_interval)
        puts = aggregate_by_strike(puts, ['lastPrice'], strike_interval)
    
    # Create figure
    fig = go.Figure()
    
    # Calculate max premium for normalization across all data
    max_premium = 1.0
    all_abs_vals = []
    if not calls.empty:
        all_abs_vals.extend(calls['lastPrice'].abs().tolist())
    if not puts.empty:
        all_abs_vals.extend(puts['lastPrice'].abs().tolist())
    if all_abs_vals:
        max_premium = max(all_abs_vals)
    if max_premium == 0:
        max_premium = 1.0
    
    # Add call premium bars
    if show_calls and not calls.empty:
        # Apply coloring mode
        call_colors = get_colors(call_color, calls['lastPrice'], max_premium, coloring_mode)
            
        if horizontal:
            fig.add_trace(go.Bar(
                y=calls['strike'].tolist(),
                x=calls['lastPrice'].tolist(),
                name='Call',
                marker_color=call_colors,
                text=[f"${price:.2f}" for price in calls['lastPrice']],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Premium: $%{x:.2f}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=calls['strike'].tolist(),
                y=calls['lastPrice'].tolist(),
                name='Call',
                marker_color=call_colors,
                text=[f"${price:.2f}" for price in calls['lastPrice']],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Premium: $%{y:.2f}<extra></extra>',
                marker_line_width=0
            ))
    
    # Add put premium bars
    if show_puts and not puts.empty:
        # Apply coloring mode
        put_colors = get_colors(put_color, puts['lastPrice'], max_premium, coloring_mode)
            
        if horizontal:
            fig.add_trace(go.Bar(
                y=puts['strike'].tolist(),
                x=puts['lastPrice'].tolist(),
                name='Put',
                marker_color=put_colors,
                text=[f"${price:.2f}" for price in puts['lastPrice']],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Premium: $%{x:.2f}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=puts['strike'].tolist(),
                y=puts['lastPrice'].tolist(),
                name='Put',
                marker_color=put_colors,
                text=[f"${price:.2f}" for price in puts['lastPrice']],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Premium: $%{y:.2f}<extra></extra>',
                marker_line_width=0
            ))
    
    # Add net premium bars if enabled
    if show_net and not (calls.empty and puts.empty):
        # Create net premium by combining calls and puts
        all_strikes_list = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
        net_premium = []
        
        for strike in all_strikes_list:
            call_prem = calls[calls['strike'] == strike]['lastPrice'].sum() if not calls.empty else 0
            put_prem = puts[puts['strike'] == strike]['lastPrice'].sum() if not puts.empty else 0
            net_prem = call_prem - put_prem
            
            net_premium.append(net_prem)
        
        # Calculate max for net premium normalization
        max_net_premium = max(abs(min(net_premium)), abs(max(net_premium))) if net_premium else 1.0
        if max_net_premium == 0:
            max_net_premium = 1.0
        
        # Apply coloring mode for net values
        net_colors = get_net_colors(net_premium, max_net_premium, call_color, put_color, coloring_mode)
        
        if horizontal:
            fig.add_trace(go.Bar(
                y=all_strikes_list,
                x=net_premium,
                name='Net',
                marker_color=net_colors,
                text=[f"${prem:.2f}" for prem in net_premium],
                textposition='auto',
                orientation='h',
                hovertemplate='Strike: %{y}<br>Net Premium: $%{x:.2f}<extra></extra>',
                marker_line_width=0
            ))
        else:
            fig.add_trace(go.Bar(
                x=all_strikes,
                y=net_premium,
                name='Net',
                marker_color=net_colors,
                text=[f"${prem:.2f}" for prem in net_premium],
                textposition='auto',
                hovertemplate='Strike: %{x}<br>Net Premium: $%{y:.2f}<extra></extra>',
                marker_line_width=0
            ))
    
    if horizontal:
        # Add current price line
        fig.add_hline(
            y=S,
            line_dash="dash",
            line_color="white",
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="right",
            annotation_font_color="white",
            line_width=1
        )
    else:
        # Add current price line
        fig.add_vline(
            x=S,
            line_dash="dash",
            line_color="white",
            opacity=0.5,
            annotation_text=f"{S:.2f}",
            annotation_position="top",
            annotation_font_color="white",
            line_width=1
        )
    
    # Add expiry info to title if multiple expiries are selected
    chart_title = 'Option Premium by Strike'
    if selected_expiries and len(selected_expiries) > 1:
        chart_title = f"Option Premium by Strike ({len(selected_expiries)} expiries)"
    
    xaxis_config = dict(
        title='',
        title_font=dict(color='#CCCCCC'),
        tickfont=dict(color='#CCCCCC'),
        gridcolor='#333333',
        linecolor='#333333',
        showgrid=False,
        zeroline=True,
        zerolinecolor='#333333',
        automargin=True
    )
    
    yaxis_config = dict(
        title='',
        title_font=dict(color='#CCCCCC'),
        tickfont=dict(color='#CCCCCC'),
        gridcolor='#333333',
        linecolor='#333333',
        showgrid=False,
        zeroline=True,
        zerolinecolor='#333333'
    )
    
    if horizontal:
         yaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False
         ))
    else:
        xaxis_config.update(dict(
            range=[min_strike, max_strike],
            autorange=False,
            tickangle=45,
            tickformat='.0f',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor='#CCCCCC'
        ))

    # Update layout
    fig.update_layout(
        title=dict(
            text=chart_title,
            font=dict(color='#CCCCCC', size=16),
            x=0.5,
            xanchor='center'
        ),
        xaxis=xaxis_config,
        yaxis=yaxis_config,
        barmode='relative',
        hovermode='y unified' if horizontal else 'x unified',
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='#CCCCCC'),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color='#CCCCCC'),
            bgcolor=PLOT_THEME['paper_bgcolor']
        ),
        bargap=0.1,
        bargroupgap=0.1,
        margin=dict(l=50, r=50, t=40, b=20),
        hoverlabel=dict(
            bgcolor=PLOT_THEME['paper_bgcolor'],
            font_size=12,
            font_family="Arial"
        ),
        spikedistance=1000,
        hoverdistance=100,
        showlegend=True,
        height=500
    )
    
    # Add hover spikes
    fig.update_xaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    fig.update_yaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    
    # Logic for Highlighting Max Level
    if highlight_max_level:
        try:
            if max_level_mode == 'Net':
                net_trace_idx = next((i for i, t in enumerate(fig.data) if t.type == 'bar' and t.name == 'Net'), None)
                if net_trace_idx is not None:
                    raw = fig.data[net_trace_idx].x if horizontal else fig.data[net_trace_idx].y
                    if raw:
                        vals = list(raw)
                        total_net = sum(vals)
                        if total_net >= 0:
                            max_bar_idx = vals.index(max(vals))
                        else:
                            max_bar_idx = vals.index(min(vals))
                        line_widths = [0] * len(vals)
                        line_widths[max_bar_idx] = 5
                        fig.data[net_trace_idx].update(marker=dict(
                            line=dict(width=line_widths, color=max_level_color)
                        ))
            else:
                max_abs_val = 0
                max_trace_idx = -1
                max_bar_idx = -1
                for i, trace in enumerate(fig.data):
                    if trace.type == 'bar':
                        vals = trace.x if horizontal else trace.y
                        if vals:
                            abs_vals = [abs(v) for v in vals]
                            if abs_vals:
                                local_max = max(abs_vals)
                                if local_max > max_abs_val:
                                    max_abs_val = local_max
                                    max_trace_idx = i
                                    max_bar_idx = abs_vals.index(local_max)
                if max_trace_idx != -1:
                    vals = fig.data[max_trace_idx].x if horizontal else fig.data[max_trace_idx].y
                    line_widths = [0] * len(vals)
                    line_widths[max_bar_idx] = 5
                    fig.data[max_trace_idx].update(marker=dict(
                        line=dict(width=line_widths, color=max_level_color)
                    ))
        except Exception as e:
            print(f"Error highlighting max level in premium chart: {e}")

    apply_plotly_theme(fig)
    return fig.to_json()

def create_centroid_chart(ticker, call_color=CALL_COLOR, put_color=PUT_COLOR, selected_expiries=None):
    """Create a chart showing call and put centroids over time with price line"""
    est = pytz.timezone('US/Eastern')

    def _empty_centroid_chart(title):
        fig = go.Figure()
        fig.update_layout(
            title=dict(text=title, font=dict(color='#CCCCCC', size=16), x=0.5, xanchor='center'),
            plot_bgcolor=PLOT_THEME['plot_bgcolor'],
            paper_bgcolor=PLOT_THEME['paper_bgcolor'],
            font=dict(color='#CCCCCC'),
            xaxis=dict(title='Time', title_font=dict(color='#CCCCCC'), tickfont=dict(color='#CCCCCC')),
            yaxis=dict(title='Price/Strike', title_font=dict(color='#CCCCCC'), tickfont=dict(color='#CCCCCC')),
            autosize=True
        )
        return fig.to_json()

    centroid_data, showing_last_session, current_time_est = _load_centroid_session_rows(ticker)

    if not centroid_data:
        if current_time_est.weekday() >= 5:
            return _empty_centroid_chart('Call vs Put Centroid Map (Market Closed - Weekend)')
        elif current_time_est.hour < 9 or (current_time_est.hour == 9 and current_time_est.minute < 30):
            return _empty_centroid_chart('Call vs Put Centroid Map (Pre-Market)')
        elif current_time_est.hour >= 16:
            return _empty_centroid_chart('Call vs Put Centroid Map (After Hours)')
        return _empty_centroid_chart('Call vs Put Centroid Map (No Data)')
    
    # Convert data to lists for plotting
    timestamps = []
    prices = []
    call_centroids = []
    put_centroids = []
    call_volumes = []
    put_volumes = []
    
    for row in centroid_data:
        timestamp, price, call_centroid, put_centroid, call_volume, put_volume = row
        dt = datetime.fromtimestamp(timestamp)
        timestamps.append(dt)
        prices.append(price)
        call_centroids.append(call_centroid if call_centroid > 0 else None)
        put_centroids.append(put_centroid if put_centroid > 0 else None)
        call_volumes.append(call_volume)
        put_volumes.append(put_volume)
    
    # Create figure
    fig = go.Figure()
    
    # Add call centroid line (top layer)
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=call_centroids,
        mode='lines',
        name='Call Centroid',
        line=dict(color=call_color, width=2),
        hovertemplate='Time: %{x}<br>Call Centroid: $%{y:.2f}<br>Call Volume: %{customdata}<extra></extra>',
        customdata=call_volumes,
        connectgaps=False
    ))

    # Add put centroid line (middle layer)
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=put_centroids,
        mode='lines',
        name='Put Centroid',
        line=dict(color=put_color, width=2),
        hovertemplate='Time: %{x}<br>Put Centroid: $%{y:.2f}<br>Put Volume: %{customdata}<extra></extra>',
        customdata=put_volumes,
        connectgaps=False
    ))

    # Add price line last (bottom layer)
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=prices,
        mode='lines',
        name='Price',
        line=dict(color='gold', width=2),
        hovertemplate='Time: %{x}<br>Price: $%{y:.2f}<extra></extra>'
    ))
    
    # Add expiry info to title if multiple expiries are selected
    chart_title = 'Call vs Put Centroid Map'
    if selected_expiries and len(selected_expiries) > 1:
        chart_title = f"Call vs Put Centroid Map ({len(selected_expiries)} expiries)"
    if showing_last_session:
        chart_title += ' (Last Session)'
    
    # Update layout to match interval map style
    fig.update_layout(
        title=dict(
            text=chart_title,
            font=dict(color='#CCCCCC', size=16),
            x=0.5,
            xanchor='center'
        ),
        xaxis=dict(
            title='Time',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333',
            tickformat='%H:%M',
            showticklabels=True,
            ticks='outside',
            ticklen=5,
            tickwidth=1,
            tickcolor='#CCCCCC',
            automargin=True
        ),
        yaxis=dict(
            title='Price/Strike',
            title_font=dict(color='#CCCCCC'),
            tickfont=dict(color='#CCCCCC'),
            gridcolor='#333333',
            linecolor='#333333',
            showgrid=False,
            zeroline=True,
            zerolinecolor='#333333'
        ),
        plot_bgcolor=PLOT_THEME['plot_bgcolor'],
        paper_bgcolor=PLOT_THEME['paper_bgcolor'],
        font=dict(color='#CCCCCC'),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color='#CCCCCC'),
            bgcolor=PLOT_THEME['paper_bgcolor']
        ),
        margin=dict(l=50, r=50, t=50, b=20),
        showlegend=True,
        autosize=True,
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor=PLOT_THEME['paper_bgcolor'],
            font_size=12,
            font_family="Arial"
        ),
        spikedistance=1000,
        hoverdistance=100
    )

    # Add hover spikes
    fig.update_xaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)
    fig.update_yaxes(showspikes=True, spikecolor='#CCCCCC', spikethickness=1)

    apply_plotly_theme(fig)
    return fig.to_json()

def infer_side(last, bid, ask):
    # If last is closer to ask, it's a buy; if closer to bid, it's a sell
    if abs(last - ask) < abs(last - bid):
        return 1  # buy
    elif abs(last - bid) < abs(last - ask):
        return -1  # sell
    else:
        return 0  # indeterminate

def fetch_options_for_multiple_dates(ticker, dates, exposure_metric="Open Interest", delta_adjusted: bool = False, calculate_in_notional: bool = True):
    """Fetch options for multiple expiration dates and combine them"""
    all_calls = []
    all_puts = []
    last_exception = None
    
    for date in dates:
        try:
            calls, puts = fetch_options_for_date(ticker, date, exposure_metric=exposure_metric, delta_adjusted=delta_adjusted, calculate_in_notional=calculate_in_notional)
            if not calls.empty:
                all_calls.append(calls)
            if not puts.empty:
                all_puts.append(puts)
        except Exception as e:
            msg = f"Error fetching options for {date}: {e}"
            print(msg)
            last_exception = e
            continue
    
    # Combine all dataframes
    combined_calls = pd.concat(all_calls, ignore_index=True) if all_calls else pd.DataFrame()
    combined_puts = pd.concat(all_puts, ignore_index=True) if all_puts else pd.DataFrame()
    # If we couldn't fetch any data and there was an exception, propagate it
    if combined_calls.empty and combined_puts.empty and last_exception is not None:
        raise last_exception

    return combined_calls, combined_puts

@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>EzOptions - Schwab</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        :root {
            --bg-0:#0B0E11; --bg-1:#151A21; --bg-2:#1E242D; --bg-3:#262D38;
            --border:#2A313B; --border-strong:#3A424F;
            --fg-0:#E5E7EB; --fg-1:#9CA3AF; --fg-2:#6B7280;
            --call:#10B981; --put:#EF4444; --accent:#3B82F6;
            --warn:#F59E0B; --info:#3B82F6; --ok:#10B981; --gold:#D4AF37;
            --radius:6px; --radius-lg:10px;
            --font-ui:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;
            --font-mono:"SF Mono","JetBrains Mono",Menlo,monospace;
        }
        .num { font-variant-numeric: tabular-nums; }
        body {
            background-color: var(--bg-0);
            color: var(--fg-0);
            font-family: var(--font-ui);
            margin: 0;
            padding: 0;
            width: 100%;
            overflow-x: hidden;
        }
        /* Token Monitor */
        #token-monitor {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 0;
            flex-wrap: wrap;
        }
        .tm-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            flex-shrink: 0;
        }
        .tm-ok   { background: #4CAF50; }
        .tm-warn { background: #ffb300; }
        .tm-err  { background: #ff4444; }
        .tm-neutral { background: #555; }
        .tm-stats {
            font-size: 11px;
            color: #888;
            font-family: monospace;
            letter-spacing: 0.02em;
        }
        .tm-stats span { color: #ccc; }
        .tm-btn-group {
            display: flex;
            gap: 5px;
        }
        .tm-btn {
            background: none;
            border: 1px solid var(--border);
            color: #777;
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 10px;
            cursor: pointer;
            transition: color 0.15s, border-color 0.15s;
        }
        .tm-btn:hover { color: #ccc; border-color: #777; }
        .tm-btn-del {
            border-color: var(--border);
            color: #666;
        }
        .tm-btn-del:hover { background: #2a1010; border-color: #883333; color: #cc4444; }
        .container {
            width: 100%;
            max-width: none;
            margin: 0 auto;
            padding: 12px;
            box-sizing: border-box;
        }
        /* Drawer/modal-friendly control wrappers (still used by existing event handlers) */
        .controls {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        .control-group {
            display: flex;
            gap: 8px;
            align-items: center;
            background-color: var(--bg-2);
            padding: 8px 12px;
            border-radius: var(--radius);
        }
        /* Inside the drawer, control-groups go full-width and lose their pill background */
        .drawer-content .control-group {
            background: transparent;
            padding: 0;
            border-radius: 0;
            width: 100%;
            flex-wrap: wrap;
        }
        .drawer-content .control-group label { font-size: 12px; color: var(--fg-1); }
        .drawer-content input[type="text"],
        .drawer-content select { width: 100%; min-width: 0; }
        .drawer-content .expiry-dropdown,
        .drawer-content .levels-dropdown { width: 100%; min-width: 0; }
        .drawer-brand {
            font-size: 16px;
            font-weight: 600;
            letter-spacing: 0.02em;
            color: var(--accent);
        }
        .drawer-inline-actions {
            justify-content: space-between;
        }
        .drawer-token-wrap {
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: var(--radius);
            background: var(--bg-0);
        }
        .drawer-token-wrap #token-monitor {
            gap: 6px;
        }
        .expiry-dropdown {
            position: relative;
            min-width: 150px;
        }
        .expiry-display {
            padding: 8px 12px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background-color: var(--bg-2);
            color: white;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            user-select: none;
        }
        .expiry-display:hover {
            border-color: #555;
            background-color: #3a3a3a;
        }
        .expiry-display::after {
            content: '▼';
            font-size: 12px;
            color: #888;
        }
        .expiry-options {
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background-color: var(--bg-2);
            border: 1px solid var(--border);
            border-radius: 6px;
            border-top: none;
            border-top-left-radius: 0;
            border-top-right-radius: 0;
            max-height: 200px;
            overflow-y: auto;
            z-index: 1000;
            display: none;
        }
        .expiry-options.open {
            display: block;
        }
        .expiry-option {
            padding: 8px 12px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: background-color 0.2s;
        }
        .expiry-option:hover {
            background-color: var(--bg-3);
        }
        .expiry-option input[type="checkbox"] {
            width: 16px;
            height: 16px;
            accent-color: var(--call);
        }
        .expiry-buttons {
            padding: 6px 8px;
            border-top: 1px solid var(--border);
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
        }
        .expiry-buttons button {
            padding: 4px 6px;
            font-size: 10px;
            border-radius: 4px;
            border: 1px solid #555;
            background-color: var(--border);
            color: white;
            cursor: pointer;
            flex: 1;
            min-width: 40px;
        }
        .expiry-buttons button:hover {
            background-color: #555;
        }
        .expiry-buttons .expiry-range-btns {
            display: flex;
            gap: 5px;
            width: 100%;
            flex-wrap: wrap;
        }
        .expiry-buttons .expiry-range-btns button {
            flex: 1;
            min-width: 38px;
            background-color: #3a3a5e;
            border-color: #5555aa;
        }
        .expiry-buttons .expiry-range-btns button:hover {
            background-color: #4a4a7e;
        }
        .levels-dropdown {
            position: relative;
            min-width: 150px;
        }
        .levels-display {
            padding: 8px 12px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background-color: var(--bg-2);
            color: white;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            user-select: none;
        }
        .levels-display:hover {
            border-color: #555;
            background-color: #3a3a3a;
        }
        .levels-display::after {
            content: '▼';
            font-size: 12px;
            color: #888;
        }
        .levels-options {
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background-color: var(--bg-2);
            border: 1px solid var(--border);
            border-radius: 6px;
            border-top: none;
            border-top-left-radius: 0;
            border-top-right-radius: 0;
            max-height: 200px;
            overflow-y: auto;
            z-index: 1000;
            display: none;
        }
        .levels-options.open {
            display: block;
        }
        .levels-option {
            padding: 8px 12px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: background-color 0.2s;
        }
        .levels-option:hover {
            background-color: var(--bg-3);
        }
        .levels-option input[type="checkbox"] {
            width: 16px;
            height: 16px;
            accent-color: var(--call);
        }
        .control-group label {
            white-space: nowrap;
        }
        input[type="text"], select {
            padding: 8px 12px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background-color: var(--bg-2);
            color: white;
            min-width: 120px;
        }

        input[type="range"] {
            width: 150px;
            height: 6px;
            background: var(--border);
            border-radius: 3px;
            outline: none;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 16px;
            height: 16px;
            background: var(--call);
            border-radius: 50%;
            cursor: pointer;
        }
        .range-value {
            min-width: 40px;
            text-align: center;
        }
        .chart-grid {
            --gex-col-w: 352px;
            --rail-col-w: 272px;
            --workspace-top-reclaim: 48px;
            --workspace-flow-reclaim: 56px;
            --workspace-pane-h: clamp(804px, calc(74vh + var(--workspace-top-reclaim) + var(--workspace-flow-reclaim)), 944px);
            display: grid;
            grid-template-columns: minmax(0, 1fr) var(--gex-col-w) var(--rail-col-w);
            grid-template-rows: minmax(34px, auto) var(--workspace-pane-h) auto auto auto;
            column-gap: 2px;
            row-gap: 4px;
            width: 100%;
            align-items: stretch;
        }
        .chart-grid.gex-collapsed { --gex-col-w: 28px; }
        /* Row 1: workspace toolbar shell (col 1) + GEX column header (col 2) + rail tabs (col 3). */
        .chart-grid > .workspace-toolbar-shell { grid-column: 1; grid-row: 1; }
        .chart-grid > .gex-col-header       { grid-column: 2; grid-row: 1; }
        .chart-grid > .right-rail-tabs      { grid-column: 3; grid-row: 1; }
        /* Row 2: price chart (col 1) + GEX column (col 2) + rail panels (col 3). */
        .chart-grid > .price-chart-container { grid-column: 1; grid-row: 2; }
        .chart-grid > .gex-column            { grid-column: 2; grid-row: 2; }
        .chart-grid > .right-rail-panels     { grid-column: 3; grid-row: 2; }
        /* Row 3: flow event lane spans all columns. */
        .chart-grid > .flow-event-lane { grid-column: 1 / -1; grid-row: 3; }
        /* Remaining rows span all columns. */
        .chart-grid > #secondary-tabs { grid-column: 1 / -1; grid-row: 4; }
        .chart-grid > .charts-grid    { grid-column: 1 / -1; grid-row: 5; }
        .chart-grid > .gex-resize-handle {
            grid-column: 2;
            grid-row: 1 / span 2;
            justify-self: start;
            align-self: stretch;
            width: 12px;
            margin-left: -6px;
            cursor: ew-resize;
            z-index: 8;
            display: flex;
            align-items: center;
            justify-content: center;
            user-select: none;
            touch-action: none;
        }
        .gex-resize-handle::before {
            content: '↔';
            width: 18px;
            height: 54px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            background: rgba(21, 26, 33, 0.92);
            border: 1px solid var(--border);
            color: var(--fg-2);
            font-size: 11px;
            line-height: 1;
            opacity: 0;
            transform: scale(0.96);
            transition: opacity 0.15s ease, transform 0.15s ease, color 0.15s ease, border-color 0.15s ease;
            pointer-events: none;
        }
        .gex-resize-handle:hover::before,
        .gex-resize-handle.dragging::before {
            opacity: 1;
            transform: scale(1);
            color: var(--fg-0);
            border-color: var(--accent);
        }
        body.gex-resize-active,
        body.gex-resize-active * {
            cursor: ew-resize !important;
            user-select: none !important;
        }
        .workspace-toolbar-shell {
            display: flex;
            align-items: stretch;
            gap: 6px;
            min-width: 0;
        }
        .workspace-drawer-toggle {
            flex: 0 0 38px;
            min-width: 38px;
            min-height: 38px;
            height: auto;
            border-radius: 10px;
            background: linear-gradient(180deg, rgba(21, 26, 33, 0.96), rgba(16, 20, 27, 0.98));
            border-color: var(--border);
        }
        .workspace-toolbar-shell .tv-toolbar-container {
            flex: 1 1 auto;
        }

        .chart-container {
            padding: 5px;
            height: 500px;
            width: 100%;
            min-width: 0;
            position: relative;
            background-color: var(--bg-1);
            border-radius: 10px;
            margin-bottom: 5px;
            display: flex;
            flex-direction: column;
        }

        .chart-container > div {
            flex: 1;
            width: 100%;
            height: 100%;
        }

        /* TradingView-style price chart overrides */
        .price-chart-container {
            background: #1a1a1a;
            border-radius: 0 0 10px 10px;
            overflow: hidden;
            margin-bottom: 5px;
            min-width: 0;
        }

        .flow-event-lane {
            display: grid;
            grid-template-columns: minmax(0, 1.7fr) minmax(280px, 0.9fr);
            gap: 6px;
            min-width: 0;
            align-items: stretch;
        }
        .flow-event-strip {
            background: var(--bg-1);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 10px 12px;
            min-width: 0;
            display: flex;
            flex-direction: column;
        }
        .flow-event-strip-head {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
            min-width: 0;
        }
        .flow-event-strip-title-row {
            display: flex;
            align-items: baseline;
            gap: 8px;
            min-width: 0;
            flex-wrap: wrap;
        }
        .flow-event-strip-title {
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--fg-2);
            white-space: nowrap;
        }
        .flow-event-strip-note {
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .flow-event-list {
            display: flex;
            flex-direction: row;
            gap: 8px;
            overflow-x: auto;
            overflow-y: hidden;
            padding: 0 0 4px 0;
            min-width: 0;
            scrollbar-width: thin;
        }
        .flow-event-list::-webkit-scrollbar {
            height: 8px;
        }
        .flow-event-list::-webkit-scrollbar-thumb {
            background: var(--border-strong);
            border-radius: 999px;
        }
        .flow-event-strip .rail-alerts-list,
        .flow-event-strip .rail-pulse-list {
            display: flex;
            flex-direction: row;
            flex-wrap: nowrap;
            align-items: stretch;
            gap: 8px;
            overflow-x: auto;
            overflow-y: hidden;
            padding: 0 0 4px 0;
            min-width: 0;
        }
        .flow-event-strip .rail-alert-item,
        .flow-event-strip .rail-pulse-item,
        .flow-event-strip .rail-alerts-empty,
        .flow-event-strip .rail-pulse-empty {
            flex: 0 0 clamp(168px, 10vw, 220px);
            width: clamp(168px, 10vw, 220px);
            min-width: clamp(168px, 10vw, 220px);
            max-width: clamp(168px, 10vw, 220px);
            margin: 0;
        }
        .flow-event-strip .rail-alert-item.lead {
            flex-basis: clamp(220px, 18vw, 300px);
            width: clamp(220px, 18vw, 300px);
            min-width: clamp(220px, 18vw, 300px);
            max-width: clamp(220px, 18vw, 300px);
        }
        .flow-event-strip .rail-alert-item.summary {
            justify-content: center;
            background: linear-gradient(180deg, var(--bg-1), var(--bg-0));
        }
        .flow-event-strip .rail-alerts-empty,
        .flow-event-strip .rail-pulse-empty {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 72px;
            padding: 14px 12px;
            border: 1px dashed var(--border);
            border-radius: var(--radius);
            background: var(--bg-0);
            text-align: center;
        }
        .flow-event-strip .rail-alert-item.top {
            padding: 9px 10px;
            background: var(--bg-0);
            box-shadow: none;
        }
        #flow-event-strip-pulse .rail-pulse-item,
        #flow-event-strip-pulse .rail-pulse-empty {
            flex: 0 0 clamp(144px, 9vw, 184px);
            width: clamp(144px, 9vw, 184px);
            min-width: clamp(144px, 9vw, 184px);
            max-width: clamp(144px, 9vw, 184px);
        }
        #flow-event-strip-pulse .rail-pulse-empty {
            width: 100%;
            min-width: 100%;
            max-width: none;
            flex-basis: 100%;
        }

        /* ── Right rail (GEX / Alerts / Levels) ──────────────────────── */
        .right-rail-tabs {
            display: flex;
            align-items: stretch;
            gap: 0;
            background: #1a1a1a;
            border-bottom: 1px solid var(--bg-2);
            border-radius: 10px 10px 0 0;
            padding: 0;
            overflow: hidden;
            min-height: 34px;
        }
        .right-rail-tab {
            flex: 1 1 0;
            background: transparent;
            color: var(--fg-1);
            border: none;
            border-bottom: 2px solid transparent;
            padding: 0 6px;
            font-size: 10px;
            line-height: 1.3;
            font-weight: 500;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            cursor: pointer;
            min-width: 0;
            min-height: 34px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .right-rail-tab:hover { color: var(--fg-0); }
        .right-rail-tab.active {
            color: var(--fg-0);
            border-bottom-color: var(--accent);
        }
        .right-rail-panels {
            position: relative;
            background: var(--bg-0);
            height: var(--workspace-pane-h);
            display: flex;
            flex-direction: column;
            min-width: 0;
        }
        .right-rail-panel {
            display: none;
            flex: 1;
            min-height: 0;
            flex-direction: column;
            overflow-y: auto;
        }
        .right-rail-panel.active { display: flex; }
        .right-rail-tab { position: relative; }
        .right-rail-tab .tab-badge {
            display: none;
            margin-left: 6px;
            padding: 1px 6px;
            background: var(--warn);
            color: var(--bg-0);
            border-radius: 10px;
            font-size: 10px;
            font-weight: 700;
            line-height: 1.3;
            vertical-align: middle;
        }
        .right-rail-tab .tab-badge.visible { display: inline-block; }

        /* Dealer Hedge Impact block (lives above the GEX chart inside the GEX rail panel) */
        .dealer-impact {
            display: flex;
            flex-direction: column;
            gap: 7px;
            padding: 8px 10px;
            border-bottom: 1px solid var(--border);
            font-size: 12px;
            flex: 0 0 auto;
        }
        .dealer-impact-overview {
            padding: 8px 9px;
            border: 1px solid var(--border);
            border-radius: var(--radius);
            background: var(--bg-0);
            margin-bottom: 2px;
        }
        .dealer-impact-overview-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 4px;
        }
        .dealer-impact-overview-label {
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }
        .dealer-impact-overview-chip {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 2px 7px;
            border-radius: 999px;
            background: var(--bg-2);
            color: var(--fg-1);
            font-size: 10px;
            letter-spacing: 0.03em;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .dealer-impact-overview-chip.pos { color: var(--call); }
        .dealer-impact-overview-chip.neg { color: var(--put); }
        .dealer-impact-overview-title {
            color: var(--fg-0);
            font-size: 14px;
            font-weight: 650;
            line-height: 1.25;
        }
        .dealer-impact-overview-title.pos { color: var(--call); }
        .dealer-impact-overview-title.neg { color: var(--put); }
        .dealer-impact-overview-sub {
            margin-top: 3px;
            color: var(--fg-1);
            font-size: 11px;
            line-height: 1.35;
        }
        .dealer-impact-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 2px;
        }
        .dealer-impact-legend span {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 7px;
            border-radius: 999px;
            border: 1px solid var(--border);
            background: var(--bg-2);
            font-size: 10px;
            letter-spacing: 0.03em;
            text-transform: uppercase;
        }
        .dealer-impact-legend .pos { color: var(--call); }
        .dealer-impact-legend .neg { color: var(--put); }
        .dealer-impact-row {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 10px;
            align-items: center;
        }
        .dealer-impact-row + .dealer-impact-row {
            padding-top: 7px;
            border-top: 1px solid var(--border);
        }
        .dealer-impact-copy { min-width: 0; }
        .dealer-impact .label {
            color: var(--fg-1);
            line-height: 1.2;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .dealer-impact .sub {
            color: var(--fg-2);
            font-size: 11px;
            margin-top: 2px;
            line-height: 1.3;
        }
        .dealer-impact-read {
            min-width: 0;
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 2px;
        }
        .dealer-impact .val   {
            font-variant-numeric: tabular-nums;
            text-align: right;
            align-self: center;
            color: var(--fg-0);
            font-size: 13px;
            font-weight: 600;
        }
        .dealer-impact .val.pos { color: var(--call); }
        .dealer-impact .val.neg { color: var(--put); }
        .dealer-impact-cue {
            color: var(--fg-2);
            font-size: 10px;
            line-height: 1.25;
            text-align: right;
        }
        .dealer-impact-cue.pos { color: var(--call); }
        .dealer-impact-cue.neg { color: var(--put); }
        .dealer-impact-summary {
            margin-top: 4px;
            padding-top: 8px;
            border-top: 1px solid var(--border);
            color: var(--fg-1);
            font-size: 10px;
            line-height: 1.45;
        }
        .dealer-impact.compact .dealer-impact-legend,
        .dealer-impact.compact .dealer-impact-cue,
        .dealer-impact.compact .dealer-impact-summary,
        .dealer-impact.compact .sub {
            display: none;
        }
        .dealer-impact.compact .dealer-impact-row + .dealer-impact-row {
            padding-top: 6px;
        }
        .dealer-impact.compact .dealer-impact-read {
            gap: 0;
        }

        /* ── Phase 3 Stage 2 — rail card system ───────────────────────── */
        .rail-card {
            background: var(--bg-1);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 10px 12px;
            margin: 6px 8px 0 8px;
            flex: 0 0 auto;
        }
        .rail-card:last-child { margin-bottom: 6px; }
        .rail-card-header-row {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 8px;
        }
        .rail-card-header {
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--fg-2);
            margin-bottom: 6px;
        }
        .rail-card-header-row .rail-card-header { margin-bottom: 0; }
        .rail-card-note {
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .rail-card-price-big {
            font-size: 22px;
            font-weight: 650;
            color: var(--fg-0);
            font-variant-numeric: tabular-nums;
            line-height: 1.1;
        }
        .rail-card-price-sub {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 4px;
            font-size: 11px;
            color: var(--fg-1);
        }
        .rail-card-price-sub .chg { font-variant-numeric: tabular-nums; }
        .rail-card-price-sub .chg.pos { color: var(--call); }
        .rail-card-price-sub .chg.neg { color: var(--put); }
        .rail-card-chip {
            display: inline-block;
            padding: 2px 7px;
            border-radius: 999px;
            background: var(--bg-2);
            color: var(--fg-1);
            font-size: 10px;
            letter-spacing: 0.04em;
        }
        .rail-metric-pair {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .rail-metric {
            min-width: 0;
        }
        .rail-metric .v {
            font-size: 19px;
            color: var(--fg-0);
            font-variant-numeric: tabular-nums;
            font-weight: 600;
            line-height: 1.15;
        }
        .rail-metric .v.pos { color: var(--call); }
        .rail-metric .v.neg { color: var(--put); }
        .rail-metric .d {
            font-size: 11px;
            color: var(--fg-2);
            font-variant-numeric: tabular-nums;
            margin-top: 3px;
            white-space: nowrap;
        }
        .rail-metric .d.pos { color: var(--call); }
        .rail-metric .d.neg { color: var(--put); }
        .gex-scope-pill {
            display: flex; gap: 4px; margin-top: 8px;
        }
        .gex-scope-pill.hidden { display: none; }
        .gex-scope-btn {
            flex: 1; padding: 3px 0; border-radius: var(--radius);
            border: 1px solid var(--border); background: var(--bg-2);
            color: var(--fg-2); font-size: 11px; cursor: pointer;
            transition: background 0.15s, color 0.15s;
        }
        .gex-scope-btn.active {
            background: var(--accent); color: #fff; border-color: var(--accent);
        }
        .rail-range-track {
            position: relative;
            height: 6px;
            background: var(--bg-2);
            border-radius: 3px;
            margin: 8px 0;
        }
        .rail-range-value {
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
            line-height: 1.35;
        }
        .rail-range-em {
            position: absolute;
            top: 0;
            height: 100%;
            background: linear-gradient(90deg, rgba(239,68,68,0.25), rgba(16,185,129,0.25));
            border-radius: 3px;
        }
        .rail-range-marker {
            position: absolute;
            top: -3px;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: var(--fg-0);
            transform: translateX(-50%);
            box-shadow: 0 0 0 2px var(--bg-1);
        }
        .rail-range-labels {
            display: flex;
            justify-content: space-between;
            font-size: 10px;
            color: var(--fg-2);
            font-variant-numeric: tabular-nums;
        }
        .rail-range-caption {
            margin-top: 7px;
            color: var(--fg-2);
            font-size: 10px;
            line-height: 1.4;
        }
        .rail-profile-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
            vertical-align: middle;
            background: var(--fg-2);
        }
        .rail-profile-dot.pos { background: var(--call); }
        .rail-profile-dot.neg { background: var(--put); }
        .rail-profile-headline {
            font-size: 13px;
            color: var(--fg-0);
            font-weight: 600;
            line-height: 1.3;
        }
        .rail-profile-blurb {
            color: var(--fg-1);
            font-size: 11px;
            margin-top: 5px;
            line-height: 1.35;
        }
        .rail-iv-top {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
        }
        .rail-iv-atm {
            color: var(--fg-0);
            font-size: 20px;
            font-weight: 650;
            font-variant-numeric: tabular-nums;
            line-height: 1.1;
        }
        .rail-iv-headline {
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
            text-align: right;
        }
        .rail-iv-blurb {
            color: var(--fg-1);
            font-size: 11px;
            margin-top: 6px;
            line-height: 1.4;
        }
        .rail-iv-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px 10px;
            margin-top: 10px;
        }
        .rail-iv-stat {
            min-width: 0;
        }
        .rail-iv-stat-label {
            display: block;
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 3px;
        }
        .rail-iv-stat-value {
            display: block;
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
            line-height: 1.25;
        }
        .rail-iv-stat-value.pos { color: var(--call); }
        .rail-iv-stat-value.neg { color: var(--put); }
        .rail-pulse-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .rail-pulse-empty {
            color: var(--fg-2);
            font-size: 11px;
            line-height: 1.45;
        }
        .rail-pulse-item {
            padding: 9px 10px;
            border-radius: var(--radius);
            border: 1px solid var(--border);
            background: var(--bg-0);
        }
        .rail-pulse-item.call { border-left: 3px solid var(--call); }
        .rail-pulse-item.put { border-left: 3px solid var(--put); }
        .rail-pulse-top {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
        }
        .rail-pulse-right {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            flex-shrink: 0;
        }
        .rail-pulse-contract {
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
        }
        .rail-pulse-expiry {
            color: var(--fg-2);
            font-size: 10px;
            margin-left: 6px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .rail-pulse-pace {
            color: var(--accent);
            font-size: 11px;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }
        .rail-pulse-lean {
            display: inline-flex;
            align-items: center;
            padding: 2px 7px;
            border-radius: 999px;
            border: 1px solid var(--border);
            color: var(--fg-1);
            background: var(--bg-1);
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .rail-pulse-lean.bullish {
            color: var(--call);
            border-color: color-mix(in srgb, var(--call) 40%, var(--border));
            background: color-mix(in srgb, var(--call) 12%, var(--bg-1));
        }
        .rail-pulse-lean.bearish {
            color: var(--put);
            border-color: color-mix(in srgb, var(--put) 40%, var(--border));
            background: color-mix(in srgb, var(--put) 12%, var(--bg-1));
        }
        .rail-pulse-lean.hedge {
            color: var(--warn);
            border-color: color-mix(in srgb, var(--warn) 34%, var(--border));
            background: color-mix(in srgb, var(--warn) 11%, var(--bg-1));
        }
        .rail-pulse-lean.mixed {
            color: var(--fg-1);
        }
        .rail-pulse-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 5px;
            color: var(--fg-2);
            font-size: 10px;
            font-variant-numeric: tabular-nums;
        }
        .rail-pulse-meta .emph {
            color: var(--fg-1);
        }
        .rail-activity-bias {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 8px;
        }
        .rail-activity-bias-label {
            color: var(--fg-2);
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        .rail-activity-bias-value {
            color: var(--fg-0);
            font-size: 13px;
            font-weight: 600;
            text-align: right;
        }
        .rail-activity-bias-value.pos { color: var(--call); }
        .rail-activity-bias-value.neg { color: var(--put); }
        .rail-sentiment-labels {
            display: flex;
            justify-content: space-between;
            font-size: 10px;
            color: var(--fg-2);
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-bottom: 5px;
        }
        .rail-sentiment-track {
            position: relative;
            height: 4px;
            background: linear-gradient(90deg, var(--put), var(--bg-2) 50%, var(--call));
            border-radius: 2px;
            margin: 0 0 12px 0;
        }
        .rail-sentiment-marker {
            position: absolute;
            top: -3px;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--fg-0);
            transform: translateX(-50%);
            box-shadow: 0 0 0 2px var(--bg-1);
        }
        .rail-bar {
            display: grid;
            grid-template-columns: 36px 1fr auto;
            gap: 8px;
            align-items: center;
            font-size: 11px;
            margin-top: 6px;
            color: var(--fg-1);
        }
        .rail-bar-track {
            height: 4px;
            background: var(--bg-2);
            border-radius: 2px;
            overflow: hidden;
        }
        .rail-bar-fill {
            height: 100%;
            background: var(--accent);
            transition: width 180ms ease;
        }
        .rail-bar-fill.pos { background: var(--call); }
        .rail-bar-fill.neg { background: var(--put); }
        .rail-bar .num {
            text-align: right;
            color: var(--fg-0);
            font-variant-numeric: tabular-nums;
            min-width: 60px;
        }
        .rail-bar-rich {
            align-items: start;
        }
        .rail-bar-rich > div {
            min-width: 0;
        }
        .rail-bar-split {
            margin-top: 5px;
            font-size: 10px;
            color: var(--fg-2);
            font-variant-numeric: tabular-nums;
        }
        .rail-centroid-meta {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            color: var(--fg-2);
            font-size: 10px;
            font-variant-numeric: tabular-nums;
            margin-bottom: 8px;
        }
        .rail-centroid-sparkline {
            position: relative;
            height: 68px;
            border-radius: var(--radius);
            background: var(--bg-0);
            border: 1px solid var(--border);
            overflow: hidden;
        }
        .rail-centroid-sparkline svg {
            width: 100%;
            height: 100%;
            display: block;
        }
        .rail-centroid-empty {
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--fg-2);
            font-size: 11px;
            text-align: center;
            padding: 0 12px;
        }
        .rail-centroid-legend {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin-top: 8px;
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.03em;
            text-transform: uppercase;
        }
        .rail-centroid-legend span {
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .rail-centroid-legend i {
            width: 10px;
            height: 2px;
            border-radius: 999px;
            background: var(--fg-2);
            display: inline-block;
        }
        .rail-centroid-legend i.call { background: var(--call); }
        .rail-centroid-legend i.put { background: var(--put); }
        .rail-centroid-legend i.price { background: #D4AF37; }
        .rail-centroid-stats {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin-top: 10px;
        }
        .rail-centroid-stat {
            min-width: 0;
        }
        .rail-centroid-stat .label {
            display: block;
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-bottom: 3px;
        }
        .rail-centroid-stat .value {
            display: block;
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }
        .rail-centroid-stat .value.pos { color: var(--call); }
        .rail-centroid-stat .value.neg { color: var(--put); }
        .rail-centroid-stat .subvalue {
            display: block;
            margin-top: 2px;
            color: var(--fg-2);
            font-size: 10px;
            font-variant-numeric: tabular-nums;
            line-height: 1.25;
        }
        .rail-centroid-stat .subvalue.pos { color: var(--call); }
        .rail-centroid-stat .subvalue.neg { color: var(--put); }
        .rail-centroid-drift-row {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin-top: 8px;
            color: var(--fg-1);
            font-size: 10px;
            font-variant-numeric: tabular-nums;
        }
        .rail-centroid-drift-row span {
            min-width: 0;
        }
        .rail-centroid-drift-row .pos { color: var(--call); }
        .rail-centroid-drift-row .neg { color: var(--put); }
        .rail-centroid-reads {
            display: grid;
            gap: 6px;
            margin-top: 10px;
        }
        .rail-centroid-read {
            color: var(--fg-1);
            font-size: 10px;
            line-height: 1.4;
        }
        /* Dealer-impact block nested inside a rail-card — drop redundant
           padding and the divider it carries when standalone. */
        .rail-card .dealer-impact {
            padding: 0;
            border-bottom: none;
        }
        /* Alerts list nested inside a rail-card — same treatment. */
        .rail-card .rail-alerts-list {
            padding: 0;
            overflow-y: visible;
            flex: 0 0 auto;
        }

        /* Alerts panel */
        .rail-alerts-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            min-height: 0;
        }
        .rail-alerts-empty {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--fg-2);
            font-size: 12px;
            text-align: center;
            padding: 18px 12px;
            line-height: 1.4;
        }
        .rail-alert-item {
            display: flex;
            flex-direction: column;
            gap: 5px;
            padding: 9px 10px;
            background: var(--bg-0);
            border: 1px solid var(--border);
            border-left: 3px solid var(--fg-2);
            border-radius: var(--radius);
            font-size: 11px;
            color: var(--fg-0);
            line-height: 1.35;
            transition: opacity 0.15s ease, transform 0.15s ease;
        }
        .rail-alert-item.warn { border-left-color: var(--warn); }
        .rail-alert-item.info { border-left-color: var(--info); }
        .rail-alert-item.flow { border-left-color: var(--accent); }
        .rail-alert-item.top {
            padding: 12px;
            background: var(--bg-1);
            box-shadow: inset 0 0 0 1px var(--border);
        }
        .rail-alert-item.muted {
            opacity: 0.7;
        }
        .rail-alert-item.stale:not(.top) {
            background: var(--bg-0);
        }
        .rail-alert-item.refreshed {
            box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
        }
        .rail-alert-topline {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            color: var(--fg-2);
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        .rail-alert-topline-left {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
        }
        .rail-alert-tag {
            display: inline-flex;
            align-items: center;
            padding: 2px 7px;
            border-radius: 999px;
            background: var(--bg-2);
            color: var(--fg-1);
        }
        .rail-alert-tier {
            display: inline-flex;
            align-items: center;
            padding: 1px 6px;
            border-radius: 999px;
            border: 1px solid var(--border);
            color: var(--fg-2);
        }
        .rail-alert-tier.count-mid,
        .rail-alert-tier.count-strong {
            font-weight: 700;
        }
        .rail-alert-tier.count-mid {
            color: var(--accent);
            border-color: color-mix(in srgb, var(--accent) 28%, var(--border));
            background: color-mix(in srgb, var(--accent) 10%, var(--bg-1));
        }
        .rail-alert-tier.count-strong {
            color: var(--fg-0);
            border-color: color-mix(in srgb, var(--accent) 56%, var(--border));
            background: color-mix(in srgb, var(--accent) 22%, var(--bg-1));
            box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 28%, transparent);
        }
        .rail-alert-direction {
            display: inline-flex;
            align-items: center;
            padding: 1px 6px;
            border-radius: 999px;
            border: 1px solid var(--border);
            color: var(--fg-1);
            background: var(--bg-1);
            font-weight: 700;
        }
        .rail-alert-direction.bullish {
            color: var(--call);
            border-color: color-mix(in srgb, var(--call) 42%, var(--border));
            background: color-mix(in srgb, var(--call) 12%, var(--bg-1));
        }
        .rail-alert-direction.bearish {
            color: var(--put);
            border-color: color-mix(in srgb, var(--put) 42%, var(--border));
            background: color-mix(in srgb, var(--put) 12%, var(--bg-1));
        }
        .rail-alert-direction.hedge {
            color: var(--warn);
            border-color: color-mix(in srgb, var(--warn) 36%, var(--border));
            background: color-mix(in srgb, var(--warn) 11%, var(--bg-1));
        }
        .rail-alert-direction.structural {
            color: var(--info);
            border-color: color-mix(in srgb, var(--info) 36%, var(--border));
            background: color-mix(in srgb, var(--info) 10%, var(--bg-1));
        }
        .rail-alert-direction.mixed {
            color: var(--fg-1);
        }
        .rail-alert-item.warn .rail-alert-tag {
            color: var(--warn);
        }
        .rail-alert-item.info .rail-alert-tag {
            color: var(--info);
        }
        .rail-alert-item.flow .rail-alert-tag {
            color: var(--accent);
        }
        .rail-alert-ago {
            font-variant-numeric: tabular-nums;
        }
        .rail-alert-text {
            color: var(--fg-0);
        }
        .rail-alert-item.top .rail-alert-text {
            font-size: 12px;
            font-weight: 600;
            line-height: 1.4;
        }
        .rail-alert-detail {
            color: var(--fg-2);
            font-size: 10px;
            line-height: 1.4;
            font-variant-numeric: tabular-nums;
        }
        .rail-alert-strongest {
            color: var(--fg-1);
            font-size: 10px;
            line-height: 1.35;
        }
        .rail-alert-strongest-value {
            color: var(--fg-0);
            font-weight: 700;
        }
        .rail-alert-summary-count {
            font-size: 18px;
            font-weight: 700;
            color: var(--fg-0);
        }
        .rail-alert-summary-text {
            color: var(--fg-1);
            line-height: 1.45;
        }

        /* Key Levels table */
        .rail-levels-table {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            min-height: 0;
            font-variant-numeric: tabular-nums;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .rail-levels-summary {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            background: var(--bg-1);
        }
        .rail-levels-summary .spot-label {
            color: var(--fg-2);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            font-size: 10px;
        }
        .rail-levels-summary .spot-price {
            color: var(--fg-0);
            font-size: 18px;
            font-weight: 650;
            line-height: 1.15;
        }
        .rail-levels-summary .spot-regime {
            font-size: 12px;
            font-weight: 600;
            text-align: right;
        }
        .rail-levels-summary .spot-regime.pos { color: var(--call); }
        .rail-levels-summary .spot-regime.neg { color: var(--put); }
        .rail-level-item {
            --level-tone: var(--fg-2);
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            background: var(--bg-1);
            color: var(--fg-0);
        }
        .rail-level-item.nearest {
            background: var(--bg-0);
            box-shadow: inset 0 0 0 1px var(--accent);
        }
        .rail-level-item.call { --level-tone: var(--call); }
        .rail-level-item.put  { --level-tone: var(--put); }
        .rail-level-item.flip { --level-tone: var(--warn); }
        .rail-level-item.em   { --level-tone: var(--fg-2); }
        .rail-level-main {
            min-width: 0;
        }
        .rail-level-top {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 10px;
            align-items: start;
        }
        .rail-level-title-row {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }
        .rail-level-swatch {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--level-tone);
            flex: 0 0 auto;
        }
        .rail-level-name {
            min-width: 0;
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
        }
        .rail-level-chip {
            display: inline-flex;
            align-items: center;
            padding: 2px 7px;
            border-radius: 999px;
            background: var(--bg-2);
            color: var(--fg-1);
            font-size: 10px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .rail-level-price {
            text-align: right;
            min-width: 0;
        }
        .rail-level-price .primary,
        .rail-level-stat .primary {
            display: block;
            color: var(--fg-0);
            font-size: 12px;
            font-weight: 600;
            line-height: 1.25;
        }
        .rail-level-price .secondary,
        .rail-level-stat .secondary {
            display: block;
            margin-top: 2px;
            color: var(--fg-2);
            font-size: 10px;
            line-height: 1.2;
        }
        .rail-level-metrics {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid var(--border);
        }
        .rail-level-stat {
            min-width: 0;
        }
        .rail-level-stat-label {
            display: block;
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 3px;
        }
        .rail-level-stat.pos .primary,
        .rail-level-stat.pos .secondary { color: var(--call); }
        .rail-level-stat.neg .primary,
        .rail-level-stat.neg .secondary { color: var(--put); }
        .rail-levels-table .lvl-empty {
            color: var(--fg-2);
            text-align: center;
            padding: 20px 8px;
            font-size: 12px;
        }

        /* Scenario GEX table (Stage 3) */
        .scenario-table-wrap {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            min-height: 0;
        }
        .scenario-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            font-variant-numeric: tabular-nums;
        }
        .scenario-table th {
            color: var(--fg-2);
            font-weight: 500;
            text-align: left;
            padding: 6px 4px;
            border-bottom: 1px solid var(--border);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            font-size: 10px;
        }
        .scenario-table th.num { text-align: right; }
        .scenario-table td {
            padding: 8px 4px;
            border-bottom: 1px solid var(--bg-2);
            color: var(--fg-0);
        }
        .scenario-table td.num { text-align: right; }
        .scenario-table td.num.pos { color: var(--call); }
        .scenario-table td.num.neg { color: var(--put); }
        .scenario-table td .mag {
            color: var(--fg-2);
            font-size: 10px;
            margin-left: 6px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .scenario-table tr.current td { background: var(--bg-1); }
        .scenario-table .scn-empty {
            color: var(--fg-2);
            text-align: center;
            padding: 16px 4px;
            font-size: 11px;
        }

        .gex-side-panel-wrap {
            background: var(--bg-0);
            border-radius: 0;
            height: 100%;
            display: flex;
            flex-direction: column;
            min-height: 0;
        }
        #gex-side-panel {
            flex: 1;
            min-height: 0;
            display: flex;
            flex-direction: column;
            width: 100%;
        }
        #gex-side-panel > .js-plotly-plot,
        #gex-side-panel > .plot-container,
        #gex-side-panel .plotly,
        #gex-side-panel .svg-container {
            flex: 1 1 auto;
            width: 100% !important;
            height: 100% !important;
            min-height: 0;
        }

        /* Strike rail (always-on, collapsible) — lives between chart and rail */
        .gex-col-header {
            display: flex;
            align-items: stretch;
            gap: 6px;
            background: #1a1a1a;
            border-bottom: 1px solid var(--bg-2);
            border-radius: 10px 10px 0 0;
            padding: 4px 8px;
            overflow: hidden;
            min-width: 0;
            min-height: 34px;
            container-type: inline-size;
        }
        .strike-rail-header-main {
            flex: 1;
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .gex-col-header .gex-col-title {
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--fg-0);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            flex: 0 0 auto;
        }
        .strike-rail-tabs {
            display: flex;
            align-items: center;
            flex: 1 1 auto;
            min-width: 0;
        }
        .strike-rail-tab-list {
            display: none;
            align-items: center;
            flex: 1 1 auto;
            flex-wrap: nowrap;
            gap: 3px;
            min-width: 0;
            overflow-x: auto;
            overflow-y: hidden;
            scrollbar-width: none;
        }
        .strike-rail-tab-list::-webkit-scrollbar { display: none; }
        .strike-rail-tab {
            background: var(--bg-2);
            color: var(--fg-2);
            border: 1px solid var(--border);
            border-radius: 999px;
            padding: 2px 7px;
            font-size: 10px;
            line-height: 1.2;
            letter-spacing: 0.04em;
            cursor: pointer;
            white-space: nowrap;
            transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        }
        .strike-rail-tab:hover { color: var(--fg-0); border-color: var(--fg-2); }
        .strike-rail-tab.active {
            background: var(--accent);
            color: #fff;
            border-color: var(--accent);
        }
        .strike-rail-select-wrap {
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 1 1 auto;
            min-width: 0;
            padding-left: 4px;
        }
        .strike-rail-select-icon {
            color: var(--fg-1);
            font-size: 12px;
            line-height: 1;
            flex: 0 0 auto;
        }
        .strike-rail-select {
            width: 100%;
            min-width: 0;
            min-height: 26px;
            padding: 3px 8px;
            border-radius: 999px;
            border: 1px solid var(--border);
            background: var(--bg-2);
            color: var(--fg-0);
            font-size: 11px;
            letter-spacing: 0.02em;
        }
        .gex-col-toggle {
            background: transparent;
            color: var(--fg-1);
            border: none;
            padding: 2px 6px;
            font-size: 12px;
            line-height: 1;
            cursor: pointer;
            border-radius: 4px;
        }
        .gex-col-toggle:hover { color: var(--fg-0); background: var(--bg-2); }
        .gex-column {
            position: relative;
            background: var(--bg-0);
            height: var(--workspace-pane-h);
            display: flex;
            flex-direction: column;
            min-width: 0;
            overflow: hidden;
        }
        .strike-rail-empty {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            padding: 18px 14px;
            text-align: center;
            color: var(--fg-2);
            font-size: 12px;
            line-height: 1.45;
        }
        .chart-grid.gex-collapsed .gex-col-header .gex-col-title,
        .chart-grid.gex-collapsed .gex-col-header .strike-rail-tabs,
        .chart-grid.gex-collapsed .gex-column > .gex-side-panel-wrap {
            display: none;
        }
        .chart-grid.gex-collapsed .gex-resize-handle { display: none; }
        .chart-grid.gex-collapsed .gex-col-header { padding: 0 2px; justify-content: center; }

        /* ── Secondary chart tab bar ──────────────────────────────── */
        .secondary-tabs {
            display: flex;
            gap: 2px;
            margin: 6px 0 6px 0;
            flex-wrap: wrap;
            border-bottom: 1px solid #2A2A2A;
            padding-bottom: 0;
        }
        .secondary-tab {
            background: transparent;
            color: #888;
            border: none;
            border-bottom: 2px solid transparent;
            padding: 6px 12px;
            font-size: 12px;
            cursor: pointer;
            letter-spacing: 0.02em;
        }
        .secondary-tab:hover { color: #ddd; }
        .secondary-tab.active {
            color: #e5e5e5;
            border-bottom-color: #3E82F1;
        }
        /* When tabs are active we stack the grid as a single column and
           show only the active chart via .tab-hidden. */
        .charts-grid.tabbed {
            display: block !important;
        }
        .charts-grid .chart-container.tab-hidden { display: none !important; }
        #price-chart {
            padding: 0 !important;
            background-color: var(--bg-0) !important;
            height: var(--workspace-pane-h) !important;
            border-radius: 0 0 0 0;
            overflow: hidden;
            /* override .chart-container defaults that conflict */
            margin-bottom: 0 !important;
        }
        .tv-historical-overlay {
            position: absolute;
            inset: 0;
            z-index: 4;
            pointer-events: none;
            overflow: hidden;
        }
        .tv-eth-overlay {
            position: absolute;
            inset: 0;
            z-index: 2;
            pointer-events: none;
        }
        .tv-session-cloud-overlay {
            position: absolute;
            inset: 0;
            z-index: 3;
            pointer-events: none;
            overflow: hidden;
        }
        .tv-session-cloud-overlay svg {
            width: 100%;
            height: 100%;
            overflow: visible;
        }
        .tv-drawing-overlay {
            position: absolute;
            inset: 0;
            z-index: 6;
            pointer-events: none;
            overflow: hidden;
        }
        .tv-drawing-overlay svg {
            width: 100%;
            height: 100%;
            overflow: visible;
        }
        .tv-drawing-layer {
            pointer-events: none;
        }
        .tv-drawing-hitbox {
            fill: transparent;
            stroke: transparent;
            pointer-events: auto;
            cursor: pointer;
        }
        .tv-drawing-shape {
            pointer-events: none;
        }
        .tv-drawing-preview .tv-drawing-shape {
            opacity: 0.72;
        }
        .tv-drawing-selected .tv-drawing-shape {
            filter: drop-shadow(0 0 6px rgba(255, 255, 255, 0.55));
        }
        .tv-drawing-text-bg {
            fill: rgba(15, 23, 42, 0.9);
            stroke: rgba(255, 255, 255, 0.14);
            stroke-width: 1;
        }
        .tv-drawing-text {
            fill: #f8fafc;
            font-size: 11px;
            font-weight: 600;
            dominant-baseline: middle;
            pointer-events: none;
        }
        .tv-drawing-anchor {
            fill: #f8fafc;
            stroke: rgba(15, 23, 42, 0.85);
            stroke-width: 2;
            pointer-events: none;
        }
        .tv-drawing-editor {
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 58;
            width: auto !important;
            height: auto !important;
            min-width: 190px;
            max-width: 220px;
            min-height: 0;
            display: none;
            gap: 8px;
            padding: 10px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            background: linear-gradient(180deg, rgba(18, 22, 30, 0.95), rgba(10, 12, 17, 0.98));
            box-shadow: 0 18px 38px rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(10px);
            pointer-events: auto;
            flex: none !important;
            align-self: flex-start;
            justify-self: auto;
            overflow: hidden;
        }
        .tv-drawing-editor.visible {
            display: grid;
            grid-auto-rows: min-content;
        }
        .tv-drawing-editor-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            color: var(--fg-0);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }
        .tv-drawing-editor-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }
        .tv-drawing-editor-row label {
            color: var(--fg-1);
            font-size: 11px;
        }
        .tv-drawing-editor-row input[type="color"] {
            width: 34px;
            height: 24px;
            padding: 0;
            border: none;
            background: none;
            cursor: pointer;
        }
        .tv-drawing-editor-row input[type="text"],
        .tv-drawing-editor-row select {
            min-width: 92px;
            background: var(--bg-2);
            color: var(--fg-0);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 4px 6px;
            font-size: 11px;
        }
        .tv-drawing-editor-row input[type="text"]:disabled,
        .tv-drawing-editor-row select:disabled {
            opacity: 0.55;
            cursor: not-allowed;
        }
        .tv-drawing-editor-row input[type="checkbox"] {
            width: 14px;
            height: 14px;
            margin: 0;
            accent-color: var(--accent);
            cursor: pointer;
        }
        .tv-drawing-editor-actions {
            display: flex;
            justify-content: flex-end;
            gap: 6px;
        }
        .tv-historical-bubble {
            position: absolute;
            border-radius: 999px;
            transform: translate(-50%, -50%);
            box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.25);
            opacity: 0.95;
            pointer-events: auto;
            cursor: pointer;
        }
        .tv-historical-tooltip {
            position: absolute;
            z-index: 55;
            display: none;
            width: auto !important;
            height: auto !important;
            min-width: 0;
            max-width: min(240px, calc(100% - 16px));
            padding: 8px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            background: linear-gradient(180deg, rgba(30, 34, 41, 0.96), rgba(16, 18, 23, 0.98));
            color: #eef2f7;
            font-size: 10px;
            line-height: 1.25;
            pointer-events: none;
            box-shadow: 0 14px 36px rgba(0, 0, 0, 0.38);
            backdrop-filter: blur(10px);
            flex: none !important;
            align-self: flex-start;
            overflow: hidden;
            white-space: normal;
        }
        .tv-historical-tooltip .tt-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 6px;
        }
        .tv-historical-tooltip .tt-badge {
            padding: 2px 6px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.08);
            color: #c9d1db;
            font-size: 9px;
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }
        .tv-historical-tooltip .tt-time {
            color: #8f9baa;
            font-size: 9px;
            margin-bottom: 0;
        }
        .tv-historical-tooltip .tt-list {
            display: grid;
            gap: 4px;
        }
        .tv-historical-tooltip .tt-row {
            display: flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
        }
        .tv-historical-tooltip .tt-dot {
            width: 7px;
            height: 7px;
            border-radius: 999px;
            box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.12);
            flex: 0 0 auto;
        }
        .tv-historical-tooltip .tt-main {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 8px;
            min-width: 0;
            width: 100%;
        }
        .tv-historical-tooltip .tt-name {
            color: #f4f7fb;
            font-weight: 600;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .tv-historical-tooltip .tt-value {
            color: #9fb0c4;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
            flex: 0 0 auto;
        }
        .tv-historical-tooltip .tt-more {
            color: #8190a3;
            margin-top: 4px;
            padding-top: 4px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            font-size: 9px;
        }
        .tv-chart-title {
            display: inline-block;
            color: #CCCCCC;
            font-size: 12px;
            font-weight: bold;
            padding: 0 6px 0 2px;
            pointer-events: none;
            flex: 0 0 auto;
            white-space: nowrap;
        }
        /* Chart toolbar — sits ABOVE the canvas, normal document flow */
        .tv-toolbar-container {
            background: linear-gradient(180deg, rgba(21, 26, 33, 0.96), rgba(16, 20, 27, 0.98));
            border: 1px solid var(--border);
            border-bottom-color: var(--bg-2);
            border-radius: 10px 10px 0 0;
            padding: 4px 6px;
            display: flex;
            flex-wrap: nowrap;
            gap: 6px;
            align-items: center;
            min-height: 0;
            min-width: 0;
            overflow: hidden;
            min-height: 38px;
        }
        .tv-toolbar {
            display: contents; /* children flow directly into container */
        }
        .tv-toolbar-main {
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 1 1 auto;
            min-width: 0;
            overflow-x: auto;
            overflow-y: hidden;
            scrollbar-width: thin;
        }
        .tv-toolbar-right {
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 0 0 auto;
            margin-left: auto;
            white-space: nowrap;
        }
        .tv-toolbar-group {
            display: inline-flex;
            align-items: center;
            gap: 3px;
            padding: 2px;
            border: 1px solid var(--border);
            border-radius: 9px;
            background: rgba(30, 36, 45, 0.7);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
            flex: 0 0 auto;
        }
        .tv-toolbar-group[data-group="draw"] {
            background: rgba(38, 45, 56, 0.5);
        }
        .tv-toolbar-group[data-group="actions"] {
            background: rgba(59, 130, 246, 0.06);
        }
        .tv-draw-dropdown {
            position: relative;
            display: inline-flex;
            align-items: center;
            gap: 2px;
        }
        .tv-draw-dropdown-menu {
            position: fixed;
            top: 0;
            left: 0;
            z-index: 128;
            min-width: 154px;
            padding: 4px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: linear-gradient(180deg, rgba(18, 22, 30, 0.98), rgba(10, 12, 17, 0.98));
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.38);
            display: none;
        }
        .tv-draw-dropdown.open .tv-draw-dropdown-menu {
            display: grid;
            gap: 2px;
        }
        .tv-draw-menu-item {
            display: flex;
            align-items: center;
            gap: 8px;
            width: 100%;
            background: transparent;
            border: 1px solid transparent;
            color: var(--fg-1);
            border-radius: 7px;
            padding: 6px 8px;
            font-size: 11px;
            cursor: pointer;
            text-align: left;
        }
        .tv-draw-menu-item:hover,
        .tv-draw-menu-item.active {
            color: var(--fg-0);
            background: rgba(255, 255, 255, 0.06);
            border-color: rgba(255, 255, 255, 0.08);
        }
        .tv-draw-pill-text {
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .tv-draw-pill-swatch {
            width: 8px;
            height: 8px;
            border-radius: 999px;
            box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.18);
            flex: 0 0 auto;
        }
        .tv-toolbar-sep {
            width: 1px;
            height: 16px;
            background: var(--border);
            margin: 0 2px;
            flex: 0 0 auto;
            display: none;
        }
        .tv-tb-btn {
            background: transparent;
            border: 1px solid transparent;
            color: var(--fg-1);
            border-radius: 7px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: 500;
            line-height: 1.35;
            cursor: pointer;
            white-space: nowrap;
            transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
            user-select: none;
        }
        .tv-tb-btn:hover  {
            background: rgba(255, 255, 255, 0.06);
            color: var(--fg-0);
            border-color: rgba(255, 255, 255, 0.06);
        }
        .tv-tb-btn.active {
            background: linear-gradient(180deg, rgba(59, 130, 246, 0.92), rgba(37, 99, 235, 0.92));
            border-color: rgba(96, 165, 250, 0.65);
            color: #fff;
            box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2);
        }
        .tv-tb-btn.danger {
            color: #fda4af;
            background: rgba(127, 29, 29, 0.28);
            border-color: rgba(220, 38, 38, 0.26);
        }
        .tv-tb-btn.danger:hover {
            background: rgba(127, 29, 29, 0.4);
            border-color: rgba(248, 113, 113, 0.32);
            color: #fecdd3;
        }
        .tv-tb-btn.icon {
            padding: 3px 6px;
            min-width: 30px;
            text-align: center;
        }
        .tv-tb-btn.pill {
            padding: 3px 7px;
            min-width: 0;
            border-radius: 999px;
            font-size: 10px;
            letter-spacing: 0.02em;
        }
        .tv-draw-inline {
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .tv-toolbar-status {
            font-size: 10px;
            letter-spacing: 0.03em;
            color: var(--warn);
            border: 1px solid rgba(245, 158, 11, 0.28);
            background: rgba(245, 158, 11, 0.12);
            border-radius: 999px;
            padding: 4px 8px;
            white-space: nowrap;
            user-select: none;
        }
        /* Indicator legend — inside canvas, pointer-events none so it doesn't block */
        .tv-indicator-legend {
            position: absolute;
            bottom: 8px;
            left: 8px;
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            z-index: 15;
            pointer-events: none;
        }
        .tv-legend-item {
            font-size: 10px;
            color: #ccc;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .tv-legend-swatch {
            width: 14px;
            height: 3px;
            border-radius: 2px;
        }
        /* RSI / MACD sub-panes */
        .tv-sub-pane {
            background: var(--bg-0);
            border-top: 1px solid var(--bg-2);
            position: relative;
            overflow: hidden;
        }
        .tv-sub-pane-header {
            position: absolute;
            top: 4px;
            left: 8px;
            z-index: 5;
            font-size: 10px;
            color: #888;
            font-weight: bold;
            pointer-events: none;
        }
        /* Drawing mode cursor */
        #price-chart.draw-mode > canvas { cursor: crosshair !important; }
        /* OHLC hover tooltip */
        .tv-ohlc-tooltip {
            position: absolute;
            top: 8px;
            left: 8px;
            z-index: 50;
            font-size: 11px;
            font-family: 'Courier New', monospace;
            color: #ccc;
            pointer-events: none;
            white-space: nowrap;
            width: auto !important;
            height: auto !important;
            max-width: none !important;
            flex: none !important;
            display: none;
            line-height: 1.6;
        }
        .tv-ohlc-tooltip .tt-time { color: #aaa; font-size: 10px; margin-bottom: 2px; }
        .tv-ohlc-tooltip .tt-up   { color: var(--call); }
        .tv-ohlc-tooltip .tt-dn   { color: var(--put); }
        /* Candle close timer */
        .candle-close-timer {
            font-size: 11px;
            font-family: 'Courier New', monospace;
            padding: 3px 6px;
            border-radius: 4px;
            background: #2a2a2a;
            border: 1px solid var(--border);
            color: #ccc;
            white-space: nowrap;
            user-select: none;
            letter-spacing: 0.5px;
        }
        .green {
            color: var(--call);
        }
        .red {
            color: var(--put);
        }
        button {
            padding: 8px 16px;
            border-radius: 6px;
            border: none;
            background-color: var(--border);
            color: white;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        button:hover {
            background-color: #555;
        }
        .title {
            font-size: 1.05em;
            font-weight: 600;
            color: var(--accent);
            letter-spacing: 0.02em;
            margin-right: 4px;
            white-space: nowrap;
        }
        /* Icon button — hamburger, gear, etc. */
        .btn-icon {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--fg-0);
            min-width: 32px;
            height: 28px;
            padding: 0 6px;
            border-radius: var(--radius);
            cursor: pointer;
            font-size: 15px;
            line-height: 1;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .btn-icon:hover { background: var(--bg-2); border-color: var(--border-strong); color: var(--fg-0); }
        /* Stream toggle pill (replaces .stream-control button) */
        .stream-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            height: 28px;
            border-radius: var(--radius);
            border: 1px solid var(--border);
            background: var(--bg-2);
            color: var(--fg-0);
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            transition: background-color 0.15s, border-color 0.15s;
        }
        .stream-pill::before {
            content: '';
            display: inline-block;
            width: 8px; height: 8px;
            border-radius: 50%;
            background: var(--call);
            box-shadow: 0 0 6px var(--call);
            transition: background-color 0.15s, box-shadow 0.15s;
        }
        .stream-pill:hover { background: var(--bg-3); border-color: var(--border-strong); }
        .stream-pill.paused { color: var(--put); }
        .stream-pill.paused::before { background: var(--put); box-shadow: 0 0 6px var(--put); }
        /* Ghost buttons — drawer footer Save/Load and modal Done */
        .btn-ghost {
            padding: 6px 12px;
            border-radius: var(--radius);
            border: 1px solid var(--border);
            background: var(--bg-2);
            color: var(--fg-0);
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: background-color 0.15s, border-color 0.15s;
        }
        .btn-ghost:hover { background: var(--bg-3); border-color: var(--border-strong); }
        .btn-ghost.success { background: var(--ok); border-color: var(--ok); color: var(--bg-0); }
        /* Slide-in settings drawer */
        .drawer-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.5);
            z-index: 199;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease;
        }
        .drawer-backdrop.open { opacity: 1; pointer-events: auto; }
        .drawer {
            position: fixed;
            top: 0;
            left: 0;
            height: 100vh;
            width: 320px;
            max-width: 86vw;
            background: var(--bg-1);
            border-right: 1px solid var(--border);
            transform: translateX(-100%);
            transition: transform 0.25s ease;
            z-index: 200;
            display: flex;
            flex-direction: column;
            box-shadow: 4px 0 16px rgba(0,0,0,0.5);
        }
        .drawer.open { transform: translateX(0); }
        .drawer-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
        }
        .drawer-header h3 {
            margin: 0;
            font-size: 12px;
            font-weight: 600;
            color: var(--fg-1);
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        .drawer-body {
            flex: 1;
            overflow-y: auto;
            padding: 4px 0;
        }
        .drawer-footer {
            padding: 12px 16px;
            border-top: 1px solid var(--border);
            display: flex;
            gap: 8px;
        }
        .drawer-section {
            border-bottom: 1px solid var(--border);
        }
        .drawer-section > summary {
            cursor: pointer;
            padding: 10px 16px;
            font-size: 11px;
            font-weight: 600;
            color: var(--fg-1);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            user-select: none;
            list-style: none;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .drawer-section > summary::-webkit-details-marker { display: none; }
        .drawer-section > summary::after {
            content: '▸';
            font-size: 10px;
            color: var(--fg-2);
            transition: transform 0.15s ease;
        }
        .drawer-section[open] > summary::after { transform: rotate(90deg); }
        .drawer-section > summary:hover { background: var(--bg-2); color: var(--fg-0); }
        .drawer-content {
            padding: 6px 16px 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        /* Chart visibility toggles inside the drawer's "Sections" group */
        .visibility-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px 12px;
        }
        .visibility-toggle {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: var(--fg-0);
            cursor: pointer;
        }
        .visibility-toggle input { accent-color: var(--accent); }
        .visibility-group-sep {
            grid-column: 1 / -1;
            margin-top: 4px;
            padding-top: 8px;
            border-top: 1px solid var(--border);
            font-size: 11px;
            color: var(--fg-2);
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        /* Settings modal (gear icon) — color pickers and coloring mode */
        .settings-modal {
            border: 1px solid var(--border);
            background: var(--bg-1);
            color: var(--fg-0);
            border-radius: var(--radius-lg);
            padding: 20px 22px;
            max-width: 380px;
            width: 92vw;
        }
        .settings-modal::backdrop { background: rgba(0,0,0,0.55); }
        .settings-modal h3 {
            margin: 0 0 12px 0;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--fg-1);
        }
        .settings-modal .modal-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid var(--bg-2);
            font-size: 13px;
        }
        .settings-modal .modal-row:last-of-type { border-bottom: none; }
        .settings-modal .modal-row label { color: var(--fg-1); }
        .settings-modal .modal-row select { min-width: 160px; }
        .settings-modal .modal-actions {
            display: flex;
            justify-content: flex-end;
            margin-top: 14px;
        }
        .indicator-modal {
            max-width: 640px;
        }
        .price-level-modal {
            max-width: 760px;
        }
        .indicator-modal-head,
        .indicator-modal-row {
            display: grid;
            grid-template-columns: minmax(132px, 1fr) 56px 84px 84px 108px;
            gap: 10px;
            align-items: center;
        }
        .indicator-modal-head {
            margin-bottom: 8px;
            padding: 0 0 8px;
            border-bottom: 1px solid var(--border);
            font-size: 10px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--fg-2);
        }
        .indicator-modal-grid {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .price-level-modal .indicator-modal-grid {
            max-height: min(68vh, 720px);
            overflow-y: auto;
            padding-right: 4px;
        }
        .price-level-modal-sep {
            padding: 12px 0 4px;
            border-top: 1px solid var(--border);
            color: var(--fg-2);
            font-size: 10px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .indicator-modal-row {
            padding: 10px 0;
            border-bottom: 1px solid var(--bg-2);
        }
        .indicator-modal-row.is-target {
            margin: 0 -10px;
            padding: 10px;
            border-color: var(--accent);
            border-radius: 10px;
            background: var(--bg-2);
        }
        .indicator-modal-row:last-child {
            border-bottom: none;
        }
        .indicator-modal-name {
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
        }
        .indicator-modal-swatch {
            width: 18px;
            height: 3px;
            border-radius: 999px;
            flex: 0 0 auto;
        }
        .indicator-modal-name label {
            color: var(--fg-0);
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
        }
        .indicator-modal-toggle {
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .indicator-modal-toggle input {
            width: 16px;
            height: 16px;
            accent-color: var(--accent);
        }
        .indicator-modal-row input[type="color"] {
            width: 40px;
            height: 28px;
            padding: 0;
            border: none;
            background: transparent;
            cursor: pointer;
        }
        .indicator-modal-row select {
            min-width: 0;
            width: 100%;
        }

        /* Add new CSS for the responsive grid layout */
        .charts-grid {
            display: grid;
            gap: 4px;
            width: 100%;
        }
        .charts-grid.tabbed .chart-container {
            height: 430px;
        }
        
        .charts-grid.one-chart {
            grid-template-columns: 1fr;
        }
        
        .charts-grid.two-charts {
            grid-template-columns: repeat(2, 1fr);
        }
        
        .charts-grid.three-charts {
            grid-template-columns: repeat(2, 1fr);
        }
        
        .charts-grid.four-charts {
            grid-template-columns: repeat(2, 1fr);
        }
        
        .charts-grid.many-charts {
            grid-template-columns: repeat(2, 1fr);
        }
        #error-notification {
            position: fixed;
            top: 20px;
            right: 20px;
            background-color: #ff4444;
            color: white;
            padding: 15px 25px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 10000;
            display: none;
            animation: slideIn 0.3s ease-out;
            max-width: 400px;
        }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .error-close {
            position: absolute;
            top: 5px;
            right: 5px;
            cursor: pointer;
            font-weight: bold;
            font-size: 18px;
        }
        
        /* Drop the strike rail below price on laptop widths before going single-column. */
        @media screen and (max-width: 1400px) {
            .chart-grid {
                --workspace-top-reclaim: 28px;
                --workspace-flow-reclaim: 28px;
                grid-template-columns: minmax(0, 1fr) var(--rail-col-w);
                grid-template-rows: minmax(34px, auto) var(--workspace-pane-h) auto minmax(34px, auto) 420px auto auto;
            }
            .chart-grid > .workspace-toolbar-shell { grid-column: 1; grid-row: 1; }
            .chart-grid > .right-rail-tabs      { grid-column: 2; grid-row: 1; }
            .chart-grid > .price-chart-container { grid-column: 1; grid-row: 2; }
            .chart-grid > .right-rail-panels     { grid-column: 2; grid-row: 2; }
            .chart-grid > .flow-event-lane       { grid-column: 1 / -1; grid-row: 3; }
            .chart-grid > .gex-col-header        { grid-column: 1; grid-row: 4; }
            .chart-grid > .gex-column            { grid-column: 1; grid-row: 5; height: 420px; }
            .chart-grid > .gex-resize-handle     { display: none; }
            .chart-grid > #secondary-tabs,
            .chart-grid > .charts-grid {
                grid-column: 1 / -1;
            }
            .chart-grid > #secondary-tabs { grid-row: 6; }
            .chart-grid > .charts-grid    { grid-row: 7; }
        }

        /* Collapse right rail below the main chart on narrow widths */
        @media screen and (max-width: 1024px) {
            .chart-grid {
                --workspace-top-reclaim: 0px;
                --workspace-flow-reclaim: 0px;
                grid-template-columns: 1fr;
                grid-template-rows: minmax(34px, auto) var(--workspace-pane-h) auto minmax(34px, auto) 420px minmax(34px, auto) 420px auto auto;
            }
            .chart-grid > .workspace-toolbar-shell { grid-column: 1; grid-row: 1; }
            .chart-grid > .price-chart-container { grid-column: 1; grid-row: 2; }
            .chart-grid > .flow-event-lane { grid-column: 1; grid-row: 3; }
            .chart-grid > .gex-col-header { grid-column: 1; grid-row: 4; }
            .chart-grid > .gex-column { grid-column: 1; grid-row: 5; }
            .chart-grid > .right-rail-tabs { grid-column: 1; grid-row: 6; }
            .chart-grid > .right-rail-panels { grid-column: 1; grid-row: 7; }
            .chart-grid > #secondary-tabs { grid-column: 1; grid-row: 8; }
            .chart-grid > .charts-grid { grid-column: 1; grid-row: 9; }
            .chart-grid > .gex-resize-handle { display: none; }
            .gex-column { height: 420px; }
            .right-rail-panels { height: 420px; }
        }
        @media screen and (max-width: 1280px) {
            .flow-event-lane {
                grid-template-columns: minmax(0, 1fr);
            }
            #flow-event-strip-pulse .rail-pulse-item,
            #flow-event-strip-pulse .rail-pulse-empty {
                flex-basis: clamp(160px, 20vw, 220px);
                width: clamp(160px, 20vw, 220px);
                min-width: clamp(160px, 20vw, 220px);
                max-width: clamp(160px, 20vw, 220px);
            }
            #flow-event-strip-pulse .rail-pulse-empty {
                width: 100%;
                min-width: 100%;
                max-width: none;
                flex-basis: 100%;
            }
        }

        /* Mobile responsive styles */
        @media screen and (max-width: 768px) {
            .container {
                width: 100%;
                padding: 10px;
            }
            .workspace-toolbar-shell {
                gap: 6px;
            }
            .workspace-drawer-toggle {
                flex-basis: 36px;
                min-width: 36px;
            }
            .drawer { width: 86vw; }
            .controls {
                flex-direction: column;
                width: 100%;
            }
            .control-group {
                width: 100%;
                justify-content: space-between;
                min-height: 44px;
            }
            .drawer-content .control-group { min-height: 0; }
            .control-group label { font-size: 14px; }
            input[type="text"], select {
                min-width: 100px;
                font-size: 16px;
                min-height: 44px;
            }
            input[type="range"] { width: 100px; }
            .expiry-dropdown, .levels-dropdown { width: 100%; }
            .expiry-display, .levels-display {
                min-height: 44px;
                display: flex;
                align-items: center;
            }
            .charts-grid.two-charts,
            .charts-grid.three-charts,
            .charts-grid.four-charts,
            .charts-grid.many-charts {
                grid-template-columns: 1fr;
            }
            .chart-container { height: 350px; }
            /* .price-info is already a vertical column in the rail; no override needed. */
            .stream-pill, .btn-ghost { min-height: 36px; }
            .btn-icon { min-height: 36px; }
            button { min-height: 44px; }
        }
        
        @media screen and (max-width: 480px) {
            .title {
                font-size: 1.2em;
            }
            .chart-container {
                height: 300px;
            }
        }

        /* Fullscreen chart overlay */
        .chart-fullscreen-btn {
            position: absolute;
            top: 8px;
            left: 8px;
            z-index: 200;
            background: rgba(45, 45, 45, 0.85);
            border: 1px solid #555;
            color: #ccc;
            width: 30px;
            height: 30px;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.2s;
            padding: 0;
            line-height: 1;
        }
        .chart-container:hover .chart-fullscreen-btn,
        .chart-fullscreen-btn:focus {
            opacity: 1;
        }
        .chart-fullscreen-btn:hover {
            background: rgba(80, 80, 80, 0.95);
            color: #fff;
            border-color: #777;
        }
        .chart-container.fullscreen {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            width: 100vw !important;
            height: 100vh !important;
            max-height: 100vh !important;
            z-index: 9999 !important;
            border-radius: 0 !important;
            margin: 0 !important;
            padding: 10px !important;
            background-color: var(--bg-0) !important;
            box-sizing: border-box !important;
            overflow: visible !important;
        }
        .chart-container.fullscreen > div {
            width: 100% !important;
            height: 100% !important;
            overflow: visible !important;
        }
        .chart-container.fullscreen .chart-fullscreen-btn {
            opacity: 1;
            position: fixed;
            top: 14px;
            left: 14px;
            z-index: 10001;
        }
        /* Pop-out button */
        .chart-popout-btn {
            position: absolute;
            top: 8px;
            left: 42px;
            z-index: 200;
            background: rgba(45, 45, 45, 0.85);
            border: 1px solid #555;
            color: #ccc;
            width: 30px;
            height: 30px;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.2s;
            padding: 0;
            line-height: 1;
        }
        .chart-container:hover .chart-popout-btn,
        .chart-popout-btn:focus {
            opacity: 1;
        }
        .chart-popout-btn:hover {
            background: rgba(80, 80, 80, 0.95);
            color: #fff;
            border-color: #777;
        }
        .chart-container.fullscreen .chart-popout-btn {
            opacity: 1;
            position: fixed;
            top: 14px;
            left: 50px;
            z-index: 10001;
        }
    </style>
</head>
<body>
    <div id="error-notification">
        <span class="error-close" onclick="hideError()">&times;</span>
        <div id="error-message"></div>
    </div>
    <div class="container">
        <div class="drawer-backdrop" id="drawer-backdrop"></div>
        <aside class="drawer" id="settings-drawer" aria-hidden="true">
            <div class="drawer-header">
                <h3>Settings</h3>
                <button id="drawerClose" class="btn-icon" title="Close" aria-label="Close drawer">&times;</button>
            </div>
            <div class="drawer-body">
                <details class="drawer-section" open>
                    <summary>Workspace</summary>
                    <div class="drawer-content">
                        <div class="drawer-brand">EzDuz1t Options</div>
                        <div class="control-group">
                            <label for="ticker">Ticker</label>
                            <input type="text" id="ticker" placeholder="Ticker" value="SPY" title="Enter a ticker symbol (e.g., SPY, AAPL) or special aggregate tickers: 'MARKET' (SPX base) or 'MARKET2' (SPY base)">
                        </div>
                        <div class="control-group">
                            <label for="timeframe">Timeframe</label>
                            <select id="timeframe" title="Candle timeframe">
                                <option value="1">1 min</option>
                                <option value="2">2 min</option>
                                <option value="3">3 min</option>
                                <option value="5">5 min</option>
                                <option value="10">10 min</option>
                                <option value="15">15 min</option>
                                <option value="30">30 min</option>
                                <option value="60">1 hour</option>
                                <option value="240">4 hour</option>
                                <option value="1440">Daily</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>Expiries</label>
                            <div class="expiry-dropdown">
                                <div class="expiry-display" id="expiry-display">
                                    <span id="expiry-text">Select expiry dates...</span>
                                </div>
                                <div class="expiry-options" id="expiry-options">
                                    <div class="expiry-buttons">
                                        <div class="expiry-range-btns">
                                            <button type="button" id="expiryToday">Today</button>
                                            <button type="button" id="expiryThisWk">This Wk</button>
                                            <button type="button" id="expiry2Wks">+1 Wk</button>
                                            <button type="button" id="expiry4Wks">+2 Wks</button>
                                            <button type="button" id="expiry1Mo">+1 Mo</button>
                                        </div>
                                        <button type="button" id="selectAllExpiry">All</button>
                                        <button type="button" id="clearAllExpiry">Clear</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="control-group drawer-inline-actions">
                            <button id="streamToggle" class="stream-pill">Auto-Update</button>
                            <button id="settingsToggle" class="btn-icon" title="Color &amp; coloring settings" aria-label="Color settings">&#9881;</button>
                        </div>
                        <div class="drawer-token-wrap">
                            <div id="token-monitor">
                                <span class="tm-dot tm-neutral" id="tm-dot"></span>
                                <span class="tm-stats" style="color:var(--fg-2);font-size:10px;">SCHWAB API</span>
                                <span class="tm-stats" id="tm-access-stat" title="">…</span>
                                <span class="tm-stats" style="color:var(--fg-2);">·</span>
                                <span class="tm-stats" id="tm-refresh-stat" title="">…</span>
                                <div class="tm-btn-group">
                                    <button class="tm-btn" onclick="fetchTokenHealth()" title="Refresh token status">&#8635;</button>
                                    <button class="tm-btn tm-btn-del" onclick="forceDeleteToken()" title="Clear stored tokens">&#128465; reset</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </details>
                <details class="drawer-section" open>
                    <summary>Sections</summary>
                    <div class="drawer-content">
                        <div class="visibility-grid" id="chart-visibility-list"><!-- populated by renderChartVisibilitySection() --></div>
                    </div>
                </details>
                <details class="drawer-section" open>
                    <summary>Strike Range</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <label for="strike_range">Strike Range (%):</label>
                            <input type="range" id="strike_range" min="0.5" max="20" value="2" step="0.5">
                            <span class="range-value" id="strike_range_value">2%</span>
                            <button id="match_em_range" class="btn-ghost" title="Toggle: auto-sync strike range to Expected Move (ATM straddle) + 0.5% wiggle room" style="padding:2px 8px;font-size:11px;">&#128208; EM</button>
                        </div>
                    </div>
                </details>
                <details class="drawer-section" open>
                    <summary>Exposure</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <label for="exposure_metric">Exposure Metric:</label>
                            <select id="exposure_metric" title="Select the metric used to weight exposure formulas (GEX/DEX/VEX etc)">
                                <option value="Open Interest" selected>Open Interest</option>
                                <option value="Volume">Volume</option>
                                <option value="Max OI vs Volume">Max OI vs Volume</option>
                                <option value="OI + Volume">OI + Volume</option>
                            </select>
                        </div>
                        <div class="control-group" title="Number of top call and put OI strikes to draw on the price chart.">
                            <label for="top_oi_count">Top OI lines / side:</label>
                            <input type="number" id="top_oi_count" min="1" max="10" value="5" style="width: 60px;">
                        </div>
                        <div class="control-group" title="When enabled, exposure formulas are adjusted by delta.">
                            <input type="checkbox" id="delta_adjusted_exposures">
                            <label for="delta_adjusted_exposures">Delta-Adjusted Exposures</label>
                        </div>
                        <div class="control-group" title="When enabled, exposures are calculated in notional value (Dollars). When disabled, in share equivalents.">
                            <input type="checkbox" id="calculate_in_notional" checked>
                            <label for="calculate_in_notional">Notional Calc</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section" open>
                    <summary>Series</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <input type="checkbox" id="show_calls">
                            <label for="show_calls">Calls</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="show_puts">
                            <label for="show_puts">Puts</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="show_net" checked>
                            <label for="show_net">Net</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section" open>
                    <summary>Options Volume</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <input type="checkbox" id="ov_show_calls" checked>
                            <label for="ov_show_calls">Show call profile</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="ov_show_puts" checked>
                            <label for="ov_show_puts">Show put profile</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="ov_show_net">
                            <label for="ov_show_net">Show net overlay</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="ov_show_totals" checked>
                            <label for="ov_show_totals">Show bar labels</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section">
                    <summary>Price Levels</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <label>Price Levels:</label>
                            <div class="levels-dropdown">
                                <div class="levels-display" id="levels-display">
                                    <span id="levels-text">None</span>
                                </div>
                                <div class="levels-options" id="levels-options">
                                    <div class="levels-option"><input type="checkbox" value="GEX" id="lvl-GEX"><label for="lvl-GEX">GEX</label></div>
                                    <div class="levels-option"><input type="checkbox" value="AbsGEX" id="lvl-AbsGEX"><label for="lvl-AbsGEX">Abs GEX</label></div>
                                    <div class="levels-option"><input type="checkbox" value="DEX" id="lvl-DEX"><label for="lvl-DEX">DEX</label></div>
                                    <div class="levels-option"><input type="checkbox" value="VEX" id="lvl-VEX"><label for="lvl-VEX">Vanna</label></div>
                                    <div class="levels-option"><input type="checkbox" value="Charm" id="lvl-Charm"><label for="lvl-Charm">Charm</label></div>
                                    <div class="levels-option"><input type="checkbox" value="Volume" id="lvl-Volume"><label for="lvl-Volume">Volume</label></div>
                                    <div class="levels-option"><input type="checkbox" value="Speed" id="lvl-Speed"><label for="lvl-Speed">Speed</label></div>
                                    <div class="levels-option"><input type="checkbox" value="Vomma" id="lvl-Vomma"><label for="lvl-Vomma">Vomma</label></div>
                                    <div class="levels-option"><input type="checkbox" value="Color" id="lvl-Color"><label for="lvl-Color">Color</label></div>
                                    <div class="levels-option"><input type="checkbox" value="Expected Move" id="lvl-ExpectedMove"><label for="lvl-ExpectedMove">Expected Move</label></div>
                                </div>
                            </div>
                        </div>
                        <div class="control-group">
                            <label for="levels_count">Top #:</label>
                            <input type="number" id="levels_count" min="1" max="10" value="3" style="width: 60px;">
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="use_heikin_ashi">
                            <label for="use_heikin_ashi">Heikin-Ashi</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="horizontal_bars">
                            <label for="horizontal_bars">Horizontal Bars</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section">
                    <summary>Absolute GEX</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <input type="checkbox" id="show_abs_gex">
                            <label for="show_abs_gex">Show Abs GEX Area</label>
                        </div>
                        <div class="control-group">
                            <label for="abs_gex_opacity">Abs GEX Opacity:</label>
                            <input type="range" id="abs_gex_opacity" min="0" max="100" value="20" style="width: 100px;">
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="use_range">
                            <label for="use_range">% Range Volume</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section">
                    <summary>Session Levels</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <input type="checkbox" id="session_levels_enabled">
                            <label for="session_levels_enabled">Show Session Levels</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_today">
                            <label for="session_today">Today RTH (TDH / TDL / TDO)</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_yesterday">
                            <label for="session_yesterday">Yesterday RTH (YDH / YDL / YDO / YDC)</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_near_open">
                            <label for="session_near_open">Near Open (NOH / NOL)</label>
                        </div>
                        <div class="control-group">
                            <label for="session_near_open_minutes">Near Open Minutes:</label>
                            <input type="number" id="session_near_open_minutes" min="0" max="330" value="60" style="width: 72px;">
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_premarket">
                            <label for="session_premarket">Premarket (PMH / PML)</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_after_hours">
                            <label for="session_after_hours">After Hours (AHH / AHL)</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_opening_range">
                            <label for="session_opening_range">Opening Range (ORH / ORL / ORM)</label>
                        </div>
                        <div class="control-group">
                            <label for="session_opening_range_minutes">Opening Range Minutes:</label>
                            <input type="number" id="session_opening_range_minutes" min="1" max="60" value="15" style="width: 72px;">
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_show_or_mid">
                            <label for="session_show_or_mid">Show ORM (50%)</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_show_or_cloud">
                            <label for="session_show_or_cloud">Opening Range Cloud</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_initial_balance">
                            <label for="session_initial_balance">Initial Balance</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_show_ib_mid">
                            <label for="session_show_ib_mid">Show IBM (50%)</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_show_ib_cloud">
                            <label for="session_show_ib_cloud">Initial Balance Cloud</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_show_ib_extensions">
                            <label for="session_show_ib_extensions">Show IB Extensions</label>
                        </div>
                        <div class="control-group">
                            <label for="session_ib_start">IB Start:</label>
                            <input type="time" id="session_ib_start" value="09:30">
                        </div>
                        <div class="control-group">
                            <label for="session_ib_end">IB End:</label>
                            <input type="time" id="session_ib_end" value="10:30">
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_abbreviate_labels">
                            <label for="session_abbreviate_labels">Abbreviate Labels</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="session_append_price">
                            <label for="session_append_price">Append Price To Labels</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section" open>
                    <summary>Alerts</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <input type="checkbox" id="gate_alerts" checked>
                            <label for="gate_alerts">Only alert near key levels</label>
                        </div>
                        <div class="control-group">
                            <input type="checkbox" id="dealer_impact_verbose">
                            <label for="dealer_impact_verbose">Verbose dealer cues</label>
                        </div>
                    </div>
                </details>
                <details class="drawer-section">
                    <summary>Max Level</summary>
                    <div class="drawer-content">
                        <div class="control-group">
                            <input type="checkbox" id="highlight_max_level">
                            <label for="highlight_max_level">Highlight Max Level</label>
                        </div>
                        <div class="control-group">
                            <label for="max_level_mode">Max Level Mode:</label>
                            <select id="max_level_mode" title="Absolute: highlights the single bar with the largest magnitude | Net: highlights the strike where the net (calls minus puts) is largest">
                                <option value="Absolute" selected>Absolute</option>
                                <option value="Net">Net</option>
                            </select>
                        </div>
                    </div>
                </details>
            </div>
            <div class="drawer-footer">
                <button id="saveSettings" class="btn-ghost" title="Save current settings to file">&#128190; Save</button>
                <button id="loadSettings" class="btn-ghost" title="Load settings from file">&#128194; Load</button>
            </div>
        </aside>

        <dialog class="settings-modal" id="settings-modal">
            <h3>Color &amp; Coloring</h3>
            <div class="modal-row">
                <label for="coloring_mode">Coloring Mode</label>
                <select id="coloring_mode" title="Solid: All bars same color | Linear: Gradual fade by value | Ranked: Only highest exposures are bright, others heavily muted">
                    <option value="Solid" selected>Solid</option>
                    <option value="Linear Intensity">Linear Intensity</option>
                    <option value="Ranked Intensity">Ranked Intensity</option>
                </select>
            </div>
            <div class="modal-row">
                <label for="call_color">Call Color</label>
                <input type="color" id="call_color" value="#10B981">
            </div>
            <div class="modal-row">
                <label for="put_color">Put Color</label>
                <input type="color" id="put_color" value="#EF4444">
            </div>
            <div class="modal-row">
                <label for="max_level_color">Max Level Color</label>
                <input type="color" id="max_level_color" value="#800080">
            </div>
            <div class="modal-actions">
                <button id="modalClose" class="btn-ghost">Done</button>
            </div>
        </dialog>
        <dialog class="settings-modal indicator-modal" id="indicator-settings-modal">
            <h3>Indicator Styles</h3>
            <div class="indicator-modal-head">
                <span>Indicator</span>
                <span>Show</span>
                <span>Color</span>
                <span>Width</span>
                <span>Style</span>
            </div>
            <div class="indicator-modal-grid" id="indicator-settings-grid"></div>
            <div class="modal-actions">
                <button id="indicatorModalClose" class="btn-ghost">Done</button>
            </div>
        </dialog>
        <dialog class="settings-modal indicator-modal price-level-modal" id="price-level-settings-modal">
            <h3>Key Level Styles</h3>
            <div class="indicator-modal-head">
                <span>Level</span>
                <span>Show</span>
                <span>Color</span>
                <span>Width</span>
                <span>Style</span>
            </div>
            <div class="indicator-modal-grid" id="price-level-settings-grid"></div>
            <div class="modal-actions">
                <button id="priceLevelModalClose" class="btn-ghost">Done</button>
            </div>
        </dialog>
        
        <div class="chart-grid" id="chart-grid">
            <div class="workspace-toolbar-shell" id="workspace-toolbar-shell">
                <button id="drawerToggle" class="btn-icon workspace-drawer-toggle" title="Open settings drawer" aria-label="Open settings">&#9776;</button>
                <div class="tv-toolbar-container" id="tv-toolbar-container"></div>
            </div>
            <div class="gex-col-header" id="gex-col-header">
                <div class="strike-rail-header-main">
                    <div class="gex-col-title">Strike Rail</div>
                    <div class="strike-rail-tabs" id="strike-rail-tabs"></div>
                </div>
                <button type="button" class="gex-col-toggle" id="gex-col-toggle" title="Collapse">‹</button>
            </div>
            <div class="gex-resize-handle" id="gex-resize-handle" role="separator" aria-label="Resize strike rail" aria-orientation="vertical"></div>
            <div class="right-rail-tabs" id="right-rail-tabs">
                <button type="button" class="right-rail-tab active" data-rail-tab="overview">Overview<span class="tab-badge" id="right-rail-alerts-badge"></span></button>
                <button type="button" class="right-rail-tab" data-rail-tab="levels">Levels</button>
                <button type="button" class="right-rail-tab" data-rail-tab="scenarios">Scenarios</button>
            </div>
            <div class="price-chart-container">
                <div class="chart-container" id="price-chart"></div>
                <div class="tv-sub-pane" id="rsi-pane" style="display:none">
                    <div class="tv-sub-pane-header">RSI 14</div>
                    <div id="rsi-chart" style="height:110px"></div>
                </div>
                <div class="tv-sub-pane" id="macd-pane" style="display:none">
                    <div class="tv-sub-pane-header">MACD (12,26,9)</div>
                    <div id="macd-chart" style="height:120px"></div>
                </div>
            </div>
            <div class="gex-column" id="gex-column">
                <div class="gex-side-panel-wrap">
                    <div id="gex-side-panel"></div>
                </div>
            </div>
            <div class="right-rail-panels" id="right-rail-panels">
                <div class="right-rail-panel active" data-rail-panel="overview">
                    <div class="rail-card" id="rail-card-price">
                        <div class="rail-card-price-big" data-live-price>—</div>
                        <div class="rail-card-price-sub">
                            <span class="chg" data-met="price_change">—</span>
                            <span class="rail-card-chip" data-met="expiry_chip">—</span>
                        </div>
                    </div>
                    <div class="rail-card" id="rail-card-metrics">
                        <div class="rail-metric-pair">
                            <div class="rail-metric">
                                <div class="rail-card-header">Net GEX</div>
                                <div class="v" data-met="net_gex">—</div>
                                <div class="d" data-met="net_gex_delta"></div>
                            </div>
                            <div class="rail-metric">
                                <div class="rail-card-header">Net DEX</div>
                                <div class="v" data-met="net_dex">—</div>
                                <div class="d" data-met="net_dex_delta"></div>
                            </div>
                        </div>
                        <div class="gex-scope-pill" id="gex-scope-pill">
                            <button class="gex-scope-btn" data-scope="all">All</button>
                            <button class="gex-scope-btn" data-scope="0dte">0DTE</button>
                        </div>
                    </div>
                    <div class="rail-card" id="rail-card-range">
                        <div class="rail-card-header-row">
                            <div class="rail-card-header">Expected Move <span data-met="em_pct"></span></div>
                            <div class="rail-card-note" data-met="em_type">ATM straddle</div>
                        </div>
                        <div class="rail-range-value" data-met="em_band_label">—</div>
                        <div class="rail-range-track">
                            <div class="rail-range-em" data-met="em_band"></div>
                            <div class="rail-range-marker" data-met="price_marker"></div>
                        </div>
                        <div class="rail-range-labels">
                            <span data-met="range_low">—</span>
                            <span data-met="range_high">—</span>
                        </div>
                        <div class="rail-range-caption" data-met="em_context">Uses the current ATM straddle, not flow alone.</div>
                    </div>
                    <div class="rail-card" id="rail-card-profile">
                        <div class="rail-card-header">Gamma Profile</div>
                        <div class="rail-profile-headline">
                            <span class="rail-profile-dot" data-met="profile_dot"></span>
                            <span data-met="profile_headline">—</span>
                        </div>
                        <div class="rail-profile-blurb" data-met="profile_blurb">—</div>
                    </div>
                    <div class="rail-card" id="rail-card-dealer">
                        <div class="rail-card-header-row">
                            <div class="rail-card-header">Dealer Impact</div>
                            <div class="rail-card-note">Hedge response</div>
                        </div>
                        <div class="dealer-impact" id="dealer-impact">
                            <div class="dealer-impact-overview">
                                <div class="dealer-impact-overview-head">
                                    <div class="dealer-impact-overview-label">Combined read</div>
                                    <div class="dealer-impact-overview-chip" data-met="dealer_conviction">—</div>
                                </div>
                                <div class="dealer-impact-overview-title" data-met="dealer_headline">—</div>
                                <div class="dealer-impact-overview-sub" data-met="dealer_subhead">—</div>
                            </div>
                            <div class="dealer-impact-legend">
                                <span class="pos">+ buy to hedge</span>
                                <span class="neg">- sell to hedge</span>
                            </div>
                            <div class="dealer-impact-row">
                                <div class="dealer-impact-copy"><div class="label">Spot +1%</div><div class="sub">hedge flow if spot lifts 1%</div></div>
                                <div class="dealer-impact-read">
                                    <div class="val" data-di="hedge_on_up_1pct">—</div>
                                    <div class="dealer-impact-cue" data-di-cue="hedge_on_up_1pct">—</div>
                                </div>
                            </div>
                            <div class="dealer-impact-row">
                                <div class="dealer-impact-copy"><div class="label">Spot −1%</div><div class="sub">hedge flow if spot drops 1%</div></div>
                                <div class="dealer-impact-read">
                                    <div class="val" data-di="hedge_on_down_1pct">—</div>
                                    <div class="dealer-impact-cue" data-di-cue="hedge_on_down_1pct">—</div>
                                </div>
                            </div>
                            <div class="dealer-impact-row">
                                <div class="dealer-impact-copy"><div class="label">Vol +1 pt</div><div class="sub">delta shift from a 1-point IV rise</div></div>
                                <div class="dealer-impact-read">
                                    <div class="val" data-di="vanna_up_1">—</div>
                                    <div class="dealer-impact-cue" data-di-cue="vanna_up_1">—</div>
                                </div>
                            </div>
                            <div class="dealer-impact-row">
                                <div class="dealer-impact-copy"><div class="label">Vol −1 pt</div><div class="sub">delta shift from a 1-point IV drop</div></div>
                                <div class="dealer-impact-read">
                                    <div class="val" data-di="vanna_down_1">—</div>
                                    <div class="dealer-impact-cue" data-di-cue="vanna_down_1">—</div>
                                </div>
                            </div>
                            <div class="dealer-impact-row">
                                <div class="dealer-impact-copy"><div class="label">Charm by close</div><div class="sub">delta bleed projected into 16:00 ET</div></div>
                                <div class="dealer-impact-read">
                                    <div class="val" data-di="charm_by_close">—</div>
                                    <div class="dealer-impact-cue" data-di-cue="charm_by_close">—</div>
                                </div>
                            </div>
                            <div class="dealer-impact-summary" data-met="dealer_takeaway">Positive values indicate dealer buying to hedge; negative values indicate dealer selling to hedge.</div>
                        </div>
                    </div>
                    <div class="rail-card" id="rail-card-activity">
                        <div class="rail-card-header">Chain Activity</div>
                        <div class="rail-activity-bias">
                            <span class="rail-activity-bias-label">Bias</span>
                            <span class="rail-activity-bias-value" data-met="activity_bias">—</span>
                        </div>
                        <div class="rail-sentiment-labels"><span>bearish</span><span>bullish</span></div>
                        <div class="rail-sentiment-track">
                            <div class="rail-sentiment-marker" data-met="sentiment_marker"></div>
                        </div>
                        <div class="rail-bar rail-bar-rich">
                            <span>OI</span>
                            <div>
                                <div class="rail-bar-track"><div class="rail-bar-fill" data-met="oi_fill"></div></div>
                                <div class="rail-bar-split" data-met="oi_split">—</div>
                            </div>
                            <span class="num" data-met="oi_cp">—</span>
                        </div>
                        <div class="rail-bar rail-bar-rich">
                            <span>VOL</span>
                            <div>
                                <div class="rail-bar-track"><div class="rail-bar-fill" data-met="vol_fill"></div></div>
                                <div class="rail-bar-split" data-met="vol_split">—</div>
                            </div>
                            <span class="num" data-met="vol_cp">—</span>
                        </div>
                    </div>
                    <div class="rail-card" id="rail-card-iv">
                        <div class="rail-card-header-row">
                            <div class="rail-card-header">Skew / IV</div>
                            <div class="rail-card-note" data-met="iv_expiry">Near expiry</div>
                        </div>
                        <div class="rail-iv-top">
                            <div class="rail-iv-atm" data-met="iv_atm">—</div>
                            <div class="rail-iv-headline" data-met="iv_headline">IV context unavailable</div>
                        </div>
                        <div class="rail-iv-blurb" data-met="iv_blurb">Need implied volatility on the near expiry to build a skew read.</div>
                        <div class="rail-iv-grid">
                            <div class="rail-iv-stat"><span class="rail-iv-stat-label">ATM Call</span><span class="rail-iv-stat-value" data-met="iv_atm_call">—</span></div>
                            <div class="rail-iv-stat"><span class="rail-iv-stat-label">ATM Put</span><span class="rail-iv-stat-value" data-met="iv_atm_put">—</span></div>
                            <div class="rail-iv-stat"><span class="rail-iv-stat-label">Put Wing</span><span class="rail-iv-stat-value" data-met="iv_put_wing">—</span></div>
                            <div class="rail-iv-stat"><span class="rail-iv-stat-label">Call Wing</span><span class="rail-iv-stat-value" data-met="iv_call_wing">—</span></div>
                            <div class="rail-iv-stat"><span class="rail-iv-stat-label">Put-Call</span><span class="rail-iv-stat-value" data-met="iv_skew_spread">—</span></div>
                            <div class="rail-iv-stat"><span class="rail-iv-stat-label">Since Open</span><span class="rail-iv-stat-value" data-met="iv_skew_change">—</span></div>
                        </div>
                    </div>
                    <div class="rail-card" id="rail-card-centroid">
                        <div class="rail-card-header-row">
                            <div class="rail-card-header">Centroid Drift</div>
                            <div class="rail-card-note" data-met="centroid_status">Current session</div>
                        </div>
                        <div class="rail-centroid-meta">
                            <span data-met="centroid_time">—</span>
                            <span data-met="centroid_spread">—</span>
                        </div>
                        <div class="rail-centroid-sparkline" data-centroid-sparkline>
                            <div class="rail-centroid-empty">Centroid data loads with stream data.</div>
                        </div>
                        <div class="rail-centroid-legend">
                            <span><i class="call"></i>Call</span>
                            <span><i class="price"></i>Spot</span>
                            <span><i class="put"></i>Put</span>
                        </div>
                        <div class="rail-centroid-stats">
                            <div class="rail-centroid-stat">
                                <span class="label">Call centroid</span>
                                <span class="value" data-met="centroid_call_strike">—</span>
                                <span class="subvalue" data-met="centroid_call_delta">—</span>
                            </div>
                            <div class="rail-centroid-stat">
                                <span class="label">Put centroid</span>
                                <span class="value" data-met="centroid_put_strike">—</span>
                                <span class="subvalue" data-met="centroid_put_delta">—</span>
                            </div>
                        </div>
                        <div class="rail-centroid-drift-row">
                            <span data-met="centroid_call_drift">—</span>
                            <span data-met="centroid_put_drift">—</span>
                        </div>
                        <div class="rail-centroid-reads">
                            <div class="rail-centroid-read" data-met="centroid_structure">—</div>
                            <div class="rail-centroid-read" data-met="centroid_drift_read">—</div>
                        </div>
                    </div>
                </div>
                <div class="right-rail-panel" data-rail-panel="levels">
                    <div class="rail-levels-table" id="right-rail-levels">
                        <div class="lvl-empty">Key levels load with stream data.</div>
                    </div>
                </div>
                <div class="right-rail-panel" data-rail-panel="scenarios">
                    <div class="scenario-table-wrap">
                        <table class="scenario-table" id="scenario-table">
                            <thead><tr><th>Scenario</th><th class="num">Net GEX</th><th>Regime</th></tr></thead>
                            <tbody><tr><td colspan="3" class="scn-empty">Scenarios load with stream data.</td></tr></tbody>
                        </table>
                        </div>
                    </div>
                </div>
            </div>
            <div class="flow-event-lane" id="flow-event-lane">
                <div class="flow-event-strip" id="flow-event-strip-alerts">
                    <div class="flow-event-strip-head">
                        <div class="flow-event-strip-title-row">
                            <div class="flow-event-strip-title">Live Alerts</div>
                            <div class="flow-event-strip-note" id="rail-alerts-title-note">Mixed Lean</div>
                        </div>
                    </div>
                    <div class="rail-alerts-list flow-event-list" id="right-rail-alerts">
                        <div class="rail-alerts-empty">No active alerts.</div>
                    </div>
                </div>
                <div class="flow-event-strip" id="flow-event-strip-pulse">
                    <div class="flow-event-strip-head">
                        <div class="flow-event-strip-title-row">
                            <div class="flow-event-strip-title">Flow Pulse</div>
                            <div class="flow-event-strip-note" id="rail-flow-pulse-note">Mixed Lean</div>
                        </div>
                    </div>
                    <div class="rail-pulse-list flow-event-list" id="rail-flow-pulse">
                        <div class="rail-pulse-empty">Pulse data builds after a minute of live flow history.</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let charts = {};
        let updateInterval;
        let lastUpdateTime = 0;
        let callColor = '#10B981';
        let putColor = '#EF4444';
        let maxLevelColor = '#800080';
        let lastData = {}; // Store last received data
        let lastPriceData = null; // Price chart data stored separately (fetched via /update_price)
        let updateInProgress = false;
        let isStreaming = true;
        let savedScrollPosition = 0; // Track scroll position
        let chartContainerCache = {}; // Cache for chart containers to prevent recreation

        // TradingView Lightweight Charts instances for the price chart
        let tvPriceChart = null;
        let tvCandleSeries = null;
        let tvVolumeSeries = null;
        let tvResizeObserver = null;
        // Indicator series references
        let tvIndicatorSeries = {};
        // Sub-pane charts for RSI and MACD
        let tvRsiChart = null, tvRsiSeries = null;
        let tvMacdChart = null, tvMacdSeries = {};
        // Persist active indicators across data refreshes
        let tvActiveInds = new Set();
        const TV_INDICATOR_DEFS = [
            { key:'sma20',  label:'SMA20',  title:'Simple Moving Average (20)', editable:true },
            { key:'sma50',  label:'SMA50',  title:'Simple Moving Average (50)', editable:true },
            { key:'sma200', label:'SMA200', title:'Simple Moving Average (200)', editable:true },
            { key:'ema9',   label:'EMA9',   title:'Exponential Moving Average (9)', editable:true },
            { key:'ema21',  label:'EMA21',  title:'Exponential Moving Average (21)', editable:true },
            { key:'vwap',   label:'VWAP',   title:'Volume Weighted Average Price', editable:true },
            { key:'bb',     label:'BB',     title:'Bollinger Bands (20, 2)', editable:true },
            { key:'rsi',    label:'RSI',    title:'Relative Strength Index (14) — sub-pane', editable:false },
            { key:'macd',   label:'MACD',   title:'MACD (12, 26, 9) — sub-pane', editable:false },
            { key:'atr',    label:'ATR',    title:'Average True Range (14) — sub-pane', editable:false },
            { key:'oi',     label:'OI',     title:'Top OI strikes (nearest expiry)', editable:false },
        ];
        const EDITABLE_TV_INDICATOR_KEYS = TV_INDICATOR_DEFS.filter(def => def.editable).map(def => def.key);
        const DEFAULT_TV_INDICATOR_PREFS = {
            sma20:  { color: '#f0c040', lineWidth: 1, lineStyle: 'solid' },
            sma50:  { color: '#40a0f0', lineWidth: 1, lineStyle: 'solid' },
            sma200: { color: '#e040fb', lineWidth: 1, lineStyle: 'solid' },
            ema9:   { color: '#ff9900', lineWidth: 1, lineStyle: 'solid' },
            ema21:  { color: '#00e5ff', lineWidth: 1, lineStyle: 'solid' },
            vwap:   { color: '#ffffff', lineWidth: 1, lineStyle: 'solid' },
            bb:     { color: '#64b4ff', lineWidth: 1, lineStyle: 'solid' }
        };
        let tvIndicatorPrefs = {};
        const PRICE_LEVEL_GROUPS = [
            {
                label: 'Dealer Flow',
                keys: ['call_wall', 'put_wall', 'gamma_flip', 'em_upper', 'em_lower']
            },
            {
                label: 'Dealer Flow Secondary',
                keys: ['call_wall_2', 'put_wall_2', 'hvl', 'max_positive_gex', 'max_negative_gex', 'em_upper_2', 'em_lower_2']
            },
            {
                label: 'Session Levels',
                keys: [
                    'today_high', 'today_low', 'today_open',
                    'yesterday_high', 'yesterday_low', 'yesterday_open', 'yesterday_close',
                    'near_open_high', 'near_open_low',
                    'premarket_high', 'premarket_low',
                    'after_hours_high', 'after_hours_low',
                    'opening_range_high', 'opening_range_low', 'opening_range_mid',
                    'ib_high', 'ib_low', 'ib_mid',
                    'ib_high_x2', 'ib_low_x2', 'ib_high_x3', 'ib_low_x3'
                ]
            }
        ];
        const DEFAULT_PRICE_LEVEL_PREFS = {
            call_wall: { label: 'Call Wall', visible: true, color: 'var(--call)', lineWidth: 2, lineStyle: 'solid' },
            put_wall: { label: 'Put Wall', visible: true, color: 'var(--put)', lineWidth: 2, lineStyle: 'solid' },
            gamma_flip: { label: 'Gamma Flip', visible: true, color: 'var(--warn)', lineWidth: 2, lineStyle: 'dashed' },
            em_upper: { label: '+1σ EM', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dotted' },
            em_lower: { label: '-1σ EM', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dotted' },
            call_wall_2: { label: 'Call Wall 2', visible: true, color: 'var(--call)', lineWidth: 1, lineStyle: 'dashed' },
            put_wall_2: { label: 'Put Wall 2', visible: true, color: 'var(--put)', lineWidth: 1, lineStyle: 'dashed' },
            hvl: { label: 'HVL', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dotted' },
            max_positive_gex: { label: 'Max +GEX', visible: true, color: 'var(--call)', lineWidth: 1, lineStyle: 'large-dashed' },
            max_negative_gex: { label: 'Max -GEX', visible: true, color: 'var(--put)', lineWidth: 1, lineStyle: 'large-dashed' },
            em_upper_2: { label: '+2σ EM', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dotted' },
            em_lower_2: { label: '-2σ EM', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dotted' },
            today_high: { label: 'TDH', visible: true, color: 'var(--call)', lineWidth: 1, lineStyle: 'solid' },
            today_low: { label: 'TDL', visible: true, color: 'var(--put)', lineWidth: 1, lineStyle: 'solid' },
            today_open: { label: 'TDO', visible: true, color: 'var(--warn)', lineWidth: 1, lineStyle: 'dashed' },
            yesterday_high: { label: 'YDH', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'solid' },
            yesterday_low: { label: 'YDL', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'solid' },
            yesterday_open: { label: 'YDO', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dashed' },
            yesterday_close: { label: 'YDC', visible: true, color: 'var(--fg-1)', lineWidth: 3, lineStyle: 'solid' },
            near_open_high: { label: 'NOH', visible: true, color: 'var(--warn)', lineWidth: 1, lineStyle: 'dotted' },
            near_open_low: { label: 'NOL', visible: true, color: 'var(--warn)', lineWidth: 1, lineStyle: 'dotted' },
            premarket_high: { label: 'PMH', visible: true, color: 'var(--info)', lineWidth: 1, lineStyle: 'dotted' },
            premarket_low: { label: 'PML', visible: true, color: 'var(--info)', lineWidth: 1, lineStyle: 'dotted' },
            after_hours_high: { label: 'AHH', visible: true, color: 'var(--warn)', lineWidth: 1, lineStyle: 'dashed' },
            after_hours_low: { label: 'AHL', visible: true, color: 'var(--warn)', lineWidth: 1, lineStyle: 'dashed' },
            opening_range_high: { label: 'ORH', visible: true, color: 'var(--accent)', lineWidth: 1, lineStyle: 'solid' },
            opening_range_low: { label: 'ORL', visible: true, color: 'var(--accent)', lineWidth: 1, lineStyle: 'solid' },
            opening_range_mid: { label: 'ORM', visible: true, color: 'var(--fg-1)', lineWidth: 1, lineStyle: 'dotted' },
            ib_high: { label: 'IBH', visible: true, color: 'var(--call)', lineWidth: 2, lineStyle: 'solid' },
            ib_low: { label: 'IBL', visible: true, color: 'var(--put)', lineWidth: 2, lineStyle: 'solid' },
            ib_mid: { label: 'IBM', visible: true, color: 'var(--warn)', lineWidth: 1, lineStyle: 'dotted' },
            ib_high_x2: { label: 'IBHx2', visible: true, color: 'var(--call)', lineWidth: 1, lineStyle: 'dotted' },
            ib_low_x2: { label: 'IBLx2', visible: true, color: 'var(--put)', lineWidth: 1, lineStyle: 'dotted' },
            ib_high_x3: { label: 'IBHx3', visible: true, color: 'var(--call)', lineWidth: 1, lineStyle: 'dotted' },
            ib_low_x3: { label: 'IBLx3', visible: true, color: 'var(--put)', lineWidth: 1, lineStyle: 'dotted' },
        };
        const LEGACY_SESSION_LEVEL_COLORS = {
            yesterday_high: '#10B981',
            yesterday_low: '#EF4444',
            premarket_high: '#10B981',
            premarket_low: '#EF4444',
            after_hours_high: '#10B981',
            after_hours_low: '#EF4444',
            opening_range_high: '#10B981',
            opening_range_low: '#EF4444',
        };
        let priceLevelPrefs = {};
        // Auto-range: when true, chart fits all data on every update; when false, zoom/pan is preserved
        let tvAutoRange = false;
        // Time-scale sync state
        let tvSyncHandlers = [], tvSyncingTimeScale = false;
        // Drawing state
        let tvDrawMode = null;          // null | 'hline' | 'trendline' | 'channel' | 'rect' | 'text'
        let tvDrawStart = null;         // {price, time, x, y} of first click
        let tvDrawingDefs = [];         // serializable drawing definitions — survive full re-renders
        let tvDrawingPreviewDef = null; // transient preview while placing a multi-click drawing
        let tvDrawingScopeKey = '';
        let tvSelectedDrawingId = null;
        let tvDrawingOverlayPending = false;
        let tvSessionCloudOverlayPending = false;
        let tvDrawingIdCounter = 0;
        let tvOpenDrawMenuRoot = null;
        let tvOpenDrawMenuAnchor = null;
        let tvOpenDrawMenuPanel = null;
        let tvToolbarMenuDismissBound = false;
        let tvLastCandles = [];         // current-day display candles (for streaming OHLCV updates)
        let tvIndicatorCandles = [];    // multi-day candles for indicator warmup (SMA200, EMA, etc.)
        let tvIndicatorDataCache = {};  // rendered indicator datapoints for click-hit testing
        let tvIndicatorEditorTargetKey = '';
        let tvCurrentDayStartTime = 0;  // unix seconds of current day's first candle (for daily VWAP)
        let tvLastPriceData = null;     // cache of full priceData for redraw
        // All overlay level prices (exposure, EM, drawn H-lines) — used by autoscaleInfoProvider
        let tvAllLevelPrices = [];
        // Session focus keeps the Y-axis tighter; Reset / auto-range can still fit everything.
        let tvYAxisMode = 'session';
        // References to dynamically-added price lines (exposure levels, expected moves)
        // kept so they can be removed without a full chart rebuild
        let tvExposurePriceLines = [];
        let tvExpectedMovePriceLines = [];
        let tvKeyLevelLines = [];
        let tvSessionLevelLines = [];
        let tvTopOILines   = [];
        let tvUserHLinePriceLines = new Map();
        let _lastTopOI     = null;
        let _lastTopOIContextKey = '';
        let _lastKeyLevels     = null;
        let _lastSessionLevels = null;
        let _lastSessionLevelsMeta = null;
        let _lastKeyLevels0dte = null;
        let _lastStats0dte     = null;
        let tvKeyLevelPrices = [];
        let tvSessionLevelPrices = [];
        let tvTopOIPrices = [];
        let gexScope = (() => { try { return localStorage.getItem('gexScope') || 'all'; } catch(e) { return 'all'; } })();
        let tvHistoricalPoints = [];
        let tvHistoricalExpectedMoveSeries = [];
        let tvHistoricalOverlayPending = false;
        let tvHistoricalOverlayDomEventsBound = false;
        let tvHistoricalRenderedPoints = [];
        const tvHistoricalOverlayMaxVisible = 1200;
        // Track the active ticker so we can reset chart state on ticker change
        let tvLastTicker = null;
        // When true, the next render will call fitContent() regardless of tvAutoRange
        let tvForceFit = false;
        // When true, the next render re-centers on the active trading session.
        let tvForceSessionFocus = false;
        // EventSource for real-time price streaming from /price_stream/<ticker>
        let priceEventSource = null;
        let priceStreamTicker = null;
        // Debounce timer for indicator refresh on intra-minute quote ticks
        let tvIndicatorRefreshTimer = null;
        // Candle close countdown timer
        let candleCloseTimerInterval = null;
        // Live price from the streamer (null until first quote arrives)
        let livePrice = null;
        // Debounce timer for Plotly price-line updates (avoid flooding relayout calls)
        let plotlyPriceUpdateTimer = null;

        // ── Chart visibility (replaces the deleted .chart-selector checkbox row) ──
        // Source of truth for which secondary charts render. Defaults below mirror the
        // legacy checked/unchecked chart state so a fresh browser keeps the same
        // surfaces visible before any saved settings are loaded.
        const CHART_IDS = [
            'price','gamma','delta','vanna','charm','speed','vomma','color',
            'options_volume','open_interest','large_trades','premium'
        ];
        // TradingView overlays on the price chart (not Plotly containers).
        // Share the chart-visibility store but render under a separate drawer group.
        const LINE_OVERLAY_IDS = ['hvl', 'em_2s', 'walls_2', 'live_gex_extrema', 'historical_dots'];
        const ALERT_GATE_KEY = 'gex.gateAlerts';
        const DEALER_DETAIL_KEY = 'gex.dealerImpactVerbose';
        const CHART_VISIBILITY_DEFAULTS = {
            price: true, gamma: true, delta: true, vanna: true, charm: true,
            speed: false, vomma: false, color: false,
            options_volume: true, open_interest: true,
            large_trades: true, premium: true,
            hvl: true, em_2s: true, walls_2: true, live_gex_extrema: true, historical_dots: true
        };
        const CHART_VISIBILITY_KEY = 'gex.chartVisibility';
        const SECONDARY_TAB_KEY = 'gex.secondaryActiveTab';
        const STRIKE_RAIL_TAB_KEY = 'gex.strikeRailTab';
        const GEX_COL_WIDTH_KEY = 'gex.sidePanelWidthPx';
        const TIMEFRAME_STORAGE_KEY = 'gex.selectedTimeframe';
        const TV_DRAWING_STORE_KEY = 'gex.tvDrawingStore.v2';
        const TV_DRAWING_TOOL_PREFS_KEY = 'gex.tvDrawingToolPrefs.v1';
        const TV_INDICATOR_STATE_KEY = 'gex.tvIndicatorState.v1';
        const PRICE_LEVEL_PREFS_KEY = 'gex.priceLevelPrefs.v1';
        const TV_HLINE_PRESETS = {
            support: { label: 'Support', shortLabel: 'Sup', color: '#10B981' },
            resistance: { label: 'Resistance', shortLabel: 'Res', color: '#EF4444' },
            neutral: { label: 'Neutral', shortLabel: 'Neutral', color: '#94A3B8' },
            custom: { label: 'Custom', shortLabel: 'Custom', color: null },
        };
        const STRIKE_RAIL_CHART_IDS = ['gamma', 'delta', 'vanna', 'charm', 'open_interest', 'options_volume', 'premium'];
        const STRIKE_RAIL_LABELS = {
            gex: 'GEX',
            gamma: 'Gamma',
            delta: 'Delta',
            vanna: 'Vanna',
            charm: 'Charm',
            open_interest: 'OI',
            options_volume: 'Options Vol',
            premium: 'Premium',
        };
        const STRIKE_RAIL_PREF_VERSION_KEY = 'gex.strikeRailTabPrefVersion';
        let activeStrikeRailTab = (() => {
            try {
                const saved = localStorage.getItem(STRIKE_RAIL_TAB_KEY);
                const prefVersion = localStorage.getItem(STRIKE_RAIL_PREF_VERSION_KEY);
                if (saved === 'open_interest' && prefVersion !== '2') {
                    localStorage.setItem(STRIKE_RAIL_TAB_KEY, 'gex');
                    localStorage.setItem(STRIKE_RAIL_PREF_VERSION_KEY, '2');
                    return 'gex';
                }
                if (!prefVersion) {
                    localStorage.setItem(STRIKE_RAIL_PREF_VERSION_KEY, '2');
                }
                return (saved && (saved === 'gex' || STRIKE_RAIL_CHART_IDS.includes(saved))) ? saved : 'gex';
            } catch (e) {
                return 'gex';
            }
        })();
        function getChartVisibility() {
            let stored = {};
            try { stored = JSON.parse(localStorage.getItem(CHART_VISIBILITY_KEY) || '{}'); } catch(e) {}
            const out = {};
            CHART_IDS.concat(LINE_OVERLAY_IDS).forEach(id => {
                out[id] = (id in stored) ? !!stored[id] : CHART_VISIBILITY_DEFAULTS[id];
            });
            return out;
        }
        function setAllChartVisibility(map) {
            const merged = getChartVisibility();
            Object.keys(map || {}).forEach(k => {
                if (CHART_IDS.includes(k) || LINE_OVERLAY_IDS.includes(k)) merged[k] = !!map[k];
            });
            try { localStorage.setItem(CHART_VISIBILITY_KEY, JSON.stringify(merged)); } catch(e) {}
        }
        function isChartVisible(id) { return !!getChartVisibility()[id]; }
        function getSelectedExpiryValues() {
            return Array.from(
                document.querySelectorAll('.expiry-option input[type="checkbox"]:checked')
            ).map(cb => cb.value);
        }
        function getTopOICountSetting() {
            const input = document.getElementById('top_oi_count');
            const parsed = input ? parseInt(input.value, 10) : 5;
            const count = Number.isFinite(parsed) ? parsed : 5;
            return Math.min(10, Math.max(1, count));
        }
        function normalizeTopOICountInput() {
            const input = document.getElementById('top_oi_count');
            if (!input) return 5;
            const clamped = getTopOICountSetting();
            input.value = String(clamped);
            return clamped;
        }
        function buildTopOIContextKey(ticker, expiryValues, topOiCount = getTopOICountSetting()) {
            const symbol = (ticker || '').trim().toUpperCase();
            const expiries = (Array.isArray(expiryValues) ? expiryValues : [])
                .slice()
                .sort()
                .join('|');
            return symbol + '::' + expiries + '::' + String(topOiCount);
        }
        function getTopOIContextKey() {
            const tickerEl = document.getElementById('ticker');
            return buildTopOIContextKey(tickerEl ? tickerEl.value : '', getSelectedExpiryValues(), getTopOICountSetting());
        }
        let gateAlertsNearKeyLevels = (() => {
            try {
                const raw = localStorage.getItem(ALERT_GATE_KEY);
                return raw == null ? true : raw === '1';
            } catch (e) {
                return true;
            }
        })();
        let dealerImpactVerbose = (() => {
            try {
                return localStorage.getItem(DEALER_DETAIL_KEY) === '1';
            } catch (e) {
                return false;
            }
        })();
        function syncAlertGateCheckbox() {
            const cb = document.getElementById('gate_alerts');
            if (cb) cb.checked = !!gateAlertsNearKeyLevels;
        }
        function syncDealerDetailCheckbox() {
            const cb = document.getElementById('dealer_impact_verbose');
            if (cb) cb.checked = !!dealerImpactVerbose;
        }
        function setAlertGateSetting(next, persist = true) {
            gateAlertsNearKeyLevels = !!next;
            syncAlertGateCheckbox();
            if (!persist) return;
            try { localStorage.setItem(ALERT_GATE_KEY, gateAlertsNearKeyLevels ? '1' : '0'); } catch (e) {}
        }
        function setDealerDetailSetting(next, persist = true) {
            dealerImpactVerbose = !!next;
            syncDealerDetailCheckbox();
            const el = document.getElementById('dealer-impact');
            if (el) el.classList.toggle('compact', !dealerImpactVerbose);
            if (!persist) return;
            try { localStorage.setItem(DEALER_DETAIL_KEY, dealerImpactVerbose ? '1' : '0'); } catch (e) {}
        }

        const DEFAULT_SESSION_LEVEL_SETTINGS = {
            enabled: false,
            today: true,
            yesterday: true,
            near_open: false,
            premarket: true,
            after_hours: true,
            opening_range: false,
            initial_balance: true,
            show_or_mid: true,
            show_or_cloud: false,
            show_ib_mid: true,
            show_ib_cloud: false,
            show_ib_extensions: true,
            near_open_minutes: 60,
            opening_range_minutes: 15,
            ib_start: '09:30',
            ib_end: '10:30',
            abbreviate_labels: true,
            append_price: true,
        };

        function normalizeSessionLevelSettings(raw = {}) {
            const base = Object.assign({}, DEFAULT_SESSION_LEVEL_SETTINGS, raw || {});
            const timeToMinutes = value => {
                const match = String(value || '').match(/^(\d{2}):(\d{2})$/);
                if (!match) return null;
                return (parseInt(match[1], 10) * 60) + parseInt(match[2], 10);
            };
            const settings = {
                enabled: !!base.enabled,
                today: !!base.today,
                yesterday: !!base.yesterday,
                near_open: !!base.near_open,
                premarket: !!base.premarket,
                after_hours: !!base.after_hours,
                opening_range: !!base.opening_range,
                initial_balance: !!base.initial_balance,
                show_or_mid: !!base.show_or_mid,
                show_or_cloud: !!base.show_or_cloud,
                show_ib_mid: !!base.show_ib_mid,
                show_ib_cloud: !!base.show_ib_cloud,
                show_ib_extensions: !!base.show_ib_extensions,
                near_open_minutes: Math.max(0, Math.min(330, parseInt(base.near_open_minutes, 10) || 0)),
                opening_range_minutes: Math.max(1, Math.min(60, parseInt(base.opening_range_minutes, 10) || DEFAULT_SESSION_LEVEL_SETTINGS.opening_range_minutes)),
                ib_start: /^\d{2}:\d{2}$/.test(String(base.ib_start || '')) ? String(base.ib_start) : DEFAULT_SESSION_LEVEL_SETTINGS.ib_start,
                ib_end: /^\d{2}:\d{2}$/.test(String(base.ib_end || '')) ? String(base.ib_end) : DEFAULT_SESSION_LEVEL_SETTINGS.ib_end,
                abbreviate_labels: !!base.abbreviate_labels,
                append_price: !!base.append_price,
            };
            if ((timeToMinutes(settings.ib_end) ?? 0) <= (timeToMinutes(settings.ib_start) ?? 0)) {
                settings.ib_start = DEFAULT_SESSION_LEVEL_SETTINGS.ib_start;
                settings.ib_end = DEFAULT_SESSION_LEVEL_SETTINGS.ib_end;
            }
            return settings;
        }

        function getSessionLevelSettingsFromDom() {
            const readChecked = (id, fallback) => {
                const el = document.getElementById(id);
                return el ? !!el.checked : fallback;
            };
            const readValue = (id, fallback) => {
                const el = document.getElementById(id);
                return el ? el.value : fallback;
            };
            return normalizeSessionLevelSettings({
                enabled: readChecked('session_levels_enabled', DEFAULT_SESSION_LEVEL_SETTINGS.enabled),
                today: readChecked('session_today', DEFAULT_SESSION_LEVEL_SETTINGS.today),
                yesterday: readChecked('session_yesterday', DEFAULT_SESSION_LEVEL_SETTINGS.yesterday),
                near_open: readChecked('session_near_open', DEFAULT_SESSION_LEVEL_SETTINGS.near_open),
                premarket: readChecked('session_premarket', DEFAULT_SESSION_LEVEL_SETTINGS.premarket),
                after_hours: readChecked('session_after_hours', DEFAULT_SESSION_LEVEL_SETTINGS.after_hours),
                opening_range: readChecked('session_opening_range', DEFAULT_SESSION_LEVEL_SETTINGS.opening_range),
                initial_balance: readChecked('session_initial_balance', DEFAULT_SESSION_LEVEL_SETTINGS.initial_balance),
                show_or_mid: readChecked('session_show_or_mid', DEFAULT_SESSION_LEVEL_SETTINGS.show_or_mid),
                show_or_cloud: readChecked('session_show_or_cloud', DEFAULT_SESSION_LEVEL_SETTINGS.show_or_cloud),
                show_ib_mid: readChecked('session_show_ib_mid', DEFAULT_SESSION_LEVEL_SETTINGS.show_ib_mid),
                show_ib_cloud: readChecked('session_show_ib_cloud', DEFAULT_SESSION_LEVEL_SETTINGS.show_ib_cloud),
                show_ib_extensions: readChecked('session_show_ib_extensions', DEFAULT_SESSION_LEVEL_SETTINGS.show_ib_extensions),
                near_open_minutes: readValue('session_near_open_minutes', DEFAULT_SESSION_LEVEL_SETTINGS.near_open_minutes),
                opening_range_minutes: readValue('session_opening_range_minutes', DEFAULT_SESSION_LEVEL_SETTINGS.opening_range_minutes),
                ib_start: readValue('session_ib_start', DEFAULT_SESSION_LEVEL_SETTINGS.ib_start),
                ib_end: readValue('session_ib_end', DEFAULT_SESSION_LEVEL_SETTINGS.ib_end),
                abbreviate_labels: readChecked('session_abbreviate_labels', DEFAULT_SESSION_LEVEL_SETTINGS.abbreviate_labels),
                append_price: readChecked('session_append_price', DEFAULT_SESSION_LEVEL_SETTINGS.append_price),
            });
        }

        function applySessionLevelSettingsToDom(raw = {}) {
            const settings = normalizeSessionLevelSettings(raw);
            const setChecked = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.checked = !!value;
            };
            const setValue = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.value = value;
            };
            setChecked('session_levels_enabled', settings.enabled);
            setChecked('session_today', settings.today);
            setChecked('session_yesterday', settings.yesterday);
            setChecked('session_near_open', settings.near_open);
            setChecked('session_premarket', settings.premarket);
            setChecked('session_after_hours', settings.after_hours);
            setChecked('session_opening_range', settings.opening_range);
            setChecked('session_initial_balance', settings.initial_balance);
            setChecked('session_show_or_mid', settings.show_or_mid);
            setChecked('session_show_or_cloud', settings.show_or_cloud);
            setChecked('session_show_ib_mid', settings.show_ib_mid);
            setChecked('session_show_ib_cloud', settings.show_ib_cloud);
            setChecked('session_show_ib_extensions', settings.show_ib_extensions);
            setValue('session_near_open_minutes', settings.near_open_minutes);
            setValue('session_opening_range_minutes', settings.opening_range_minutes);
            setValue('session_ib_start', settings.ib_start);
            setValue('session_ib_end', settings.ib_end);
            setChecked('session_abbreviate_labels', settings.abbreviate_labels);
            setChecked('session_append_price', settings.append_price);
            syncSessionLevelToolbarButton();
            return settings;
        }

        function syncSessionLevelToolbarButton() {
            const settings = getSessionLevelSettingsFromDom();
            document.querySelectorAll('[data-session-toggle]').forEach(btn => {
                btn.classList.toggle('active', settings.enabled);
            });
        }

        function wireSessionLevelControls() {
            const ids = [
                'session_levels_enabled',
                'session_today',
                'session_yesterday',
                'session_near_open',
                'session_premarket',
                'session_after_hours',
                'session_opening_range',
                'session_initial_balance',
                'session_show_or_mid',
                'session_show_or_cloud',
                'session_show_ib_mid',
                'session_show_ib_cloud',
                'session_show_ib_extensions',
                'session_near_open_minutes',
                'session_opening_range_minutes',
                'session_ib_start',
                'session_ib_end',
                'session_abbreviate_labels',
                'session_append_price',
            ];
            ids.forEach(id => {
                const el = document.getElementById(id);
                if (!el || el.__sessionLevelsWired) return;
                el.__sessionLevelsWired = true;
                el.addEventListener('change', () => {
                    syncSessionLevelToolbarButton();
                    renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
                    if (getSessionLevelSettingsFromDom().enabled) {
                        _priceHistoryLastKey = '';
                        fetchPriceHistory(true);
                    }
                });
            });
        }

        function getPersistedTimeframe() {
            try {
                const value = localStorage.getItem(TIMEFRAME_STORAGE_KEY);
                return value ? String(value) : null;
            } catch (e) {
                return null;
            }
        }

        function persistSelectedTimeframe(value) {
            try {
                localStorage.setItem(TIMEFRAME_STORAGE_KEY, String(value || '1'));
            } catch (e) {}
        }

        function applyPersistedTimeframePreference() {
            const select = document.getElementById('timeframe');
            const persisted = getPersistedTimeframe();
            if (!select || !persisted) return;
            const hasOption = Array.from(select.options || []).some(option => option.value === persisted);
            if (hasOption) select.value = persisted;
        }

        // List of Plotly chart div IDs that carry a current-price line shape
        const PLOTLY_PRICE_LINE_CHARTS = [
            'gamma-chart', 'delta-chart', 'vanna-chart', 'charm-chart',
            'speed-chart', 'vomma-chart', 'color-chart',
            'options_volume-chart', 'open_interest-chart', 'premium-chart'
        ];

        /**
         * Update the current-price line (shape + annotation) on all visible Plotly charts
         * and refresh the "Current Price" text in the price-info panel.
         */
        function updateAllPlotlyPriceLines(price) {
            const priceStr = price.toFixed(2);
            const numericAnnotationRe = /^-?\\d+(?:\\.\\d+)?$/;

            const plotIds = PLOTLY_PRICE_LINE_CHARTS.concat(['gex-side-panel']);

            plotIds.forEach(function(id) {
                const div = document.getElementById(id);
                if (!div || !div._fullLayout) return;

                const shapes = div._fullLayout.shapes || [];
                const annotations = div._fullLayout.annotations || [];
                const update = {};

                // Identify and update the price line shape.
                // add_vline produces: xref='x', yref='paper', x0===x1
                // add_hline produces: xref='paper', yref='y', y0===y1
                for (let i = 0; i < shapes.length; i++) {
                    const sh = shapes[i];
                    if (sh.xref === 'x' && sh.yref === 'paper' && sh.x0 === sh.x1) {
                        update['shapes[' + i + '].x0'] = price;
                        update['shapes[' + i + '].x1'] = price;
                        break;
                    } else if (sh.xref === 'paper' && sh.yref === 'y' && sh.y0 === sh.y1) {
                        update['shapes[' + i + '].y0'] = price;
                        update['shapes[' + i + '].y1'] = price;
                        break;
                    }
                }

                // Identify and update the price line annotation.
                // add_vline annotation: xref='x', yref='paper'
                // add_hline annotation: xref='paper', yref='y'
                for (let i = 0; i < annotations.length; i++) {
                    const ann = annotations[i];
                    if (ann.xref === 'x' && ann.yref === 'paper') {
                        update['annotations[' + i + '].x'] = price;
                        update['annotations[' + i + '].text'] = priceStr;
                        break;
                    } else if (
                        ann.xref === 'paper' &&
                        ann.yref === 'y' &&
                        typeof ann.text === 'string' &&
                        numericAnnotationRe.test(ann.text.trim())
                    ) {
                        update['annotations[' + i + '].y'] = price;
                        update['annotations[' + i + '].text'] = priceStr;
                        break;
                    }
                }

                if (Object.keys(update).length > 0) {
                    try { Plotly.relayout(div, update); } catch(e) {}
                }
            });

            // Live-update the price card big number in the alerts rail.
            // Phase 3: the price-info div has been replaced with a rail-card;
            // the [data-live-price] hook moved into .rail-card-price-big.
            const cpLine = document.querySelector('[data-live-price]');
            if (cpLine) {
                cpLine.textContent = '$' + priceStr;
            }
        }

        function tvGetVisibleCandlePriceRange(trimLeftBars = 0) {
            if (!tvLastCandles.length) return null;
            let fromIndex = 0;
            let toIndex = tvLastCandles.length - 1;
            try {
                const visibleRange = tvPriceChart && tvPriceChart.timeScale
                    ? tvPriceChart.timeScale().getVisibleLogicalRange()
                    : null;
                if (visibleRange) {
                    fromIndex = Math.max(0, Math.floor(visibleRange.from));
                    toIndex = Math.min(tvLastCandles.length - 1, Math.ceil(visibleRange.to));
                }
            } catch (e) {}
            if (trimLeftBars > 0) {
                fromIndex = Math.min(toIndex, fromIndex + trimLeftBars);
            }

            let minValue = Infinity;
            let maxValue = -Infinity;
            for (let index = fromIndex; index <= toIndex; index += 1) {
                const candle = tvLastCandles[index];
                if (!candle) continue;
                if (Number.isFinite(candle.low)) minValue = Math.min(minValue, candle.low);
                if (Number.isFinite(candle.high)) maxValue = Math.max(maxValue, candle.high);
            }
            if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) return null;
            return { minValue, maxValue };
        }

        // Apply (or re-apply) the autoscaleInfoProvider so the Y-axis always fits levels
        function tvApplyAutoscale() {
            if (!tvCandleSeries) return;
            const levelPrices = tvAllLevelPrices.slice(); // snapshot
            const yAxisMode = tvYAxisMode;
            tvCandleSeries.applyOptions({
                autoscaleInfoProvider: (original) => {
                    const res = original();
                    if (!res) return res;
                    let minVal = res.priceRange.minValue;
                    let maxVal = res.priceRange.maxValue;
                    if (yAxisMode !== 'fit-all') {
                        let trimLeftBars = 0;
                        try {
                            const visibleRange = tvPriceChart && tvPriceChart.timeScale
                                ? tvPriceChart.timeScale().getVisibleLogicalRange()
                                : null;
                            if (visibleRange) {
                                const visibleBars = Math.max(1, Math.ceil(visibleRange.to) - Math.floor(visibleRange.from) + 1);
                                trimLeftBars = Math.min(48, Math.max(0, Math.round(visibleBars * 0.12)));
                            }
                        } catch (e) {}
                        const candleRange = tvGetVisibleCandlePriceRange(trimLeftBars);
                        if (candleRange) {
                            const span = Math.max(0.01, candleRange.maxValue - candleRange.minValue);
                            const focusPad = Math.min(1.0, Math.max(span * 0.28, candleRange.maxValue * 0.0009, 0.18));
                            const focusMin = candleRange.minValue - focusPad;
                            const focusMax = candleRange.maxValue + focusPad;
                            minVal = Math.max(minVal, focusMin);
                            maxVal = Math.min(maxVal, focusMax);
                            levelPrices.forEach(price => {
                                if (!Number.isFinite(price)) return;
                                if (price >= focusMin && price <= focusMax) {
                                    minVal = Math.min(minVal, price);
                                    maxVal = Math.max(maxVal, price);
                                }
                            });
                        } else if (levelPrices.length > 0) {
                            minVal = Math.min(minVal, ...levelPrices);
                            maxVal = Math.max(maxVal, ...levelPrices);
                        }
                    } else if (levelPrices.length > 0) {
                        minVal = Math.min(minVal, ...levelPrices);
                        maxVal = Math.max(maxVal, ...levelPrices);
                    }
                    const pad = Math.max(0.01, (maxVal - minVal) * 0.05);
                    minVal -= pad;
                    maxVal += pad;
                    return { priceRange: { minValue: minVal, maxValue: maxVal }, margins: res.margins };
                }
            });
        }

        function tvRefreshPriceScale() {
            if (!tvPriceChart) return;
            try { tvPriceChart.priceScale('right').applyOptions({ autoScale: true }); } catch (e) {}
        }

        function tvFitAll() {
            if (!tvPriceChart) return;
            tvYAxisMode = 'fit-all';
            // Use setTimeout so this fires after LightweightCharts finishes its own internal layout pass
            setTimeout(() => {
                try {
                    // Reset X-axis (time scale)
                    tvPriceChart.timeScale().fitContent();
                    // Reset Y-axis: re-enable auto-scaling (user dragging the price axis locks it to manual mode)
                    tvPriceChart.priceScale('right').applyOptions({ autoScale: true });
                    // Re-arm the autoscaleInfoProvider so level lines are included in the Y range
                    tvApplyAutoscale();
                    // Sub-pane charts also need their price axes reset
                    if (tvRsiChart)  tvRsiChart.priceScale('right').applyOptions({ autoScale: true });
                    if (tvMacdChart) tvMacdChart.priceScale('right').applyOptions({ autoScale: true });
                } catch(e) {}
            }, 50);
        }

        function tvGetCurrentSessionStartIndex() {
            if (!tvLastCandles.length) return 0;
            if (tvCurrentDayStartTime) {
                const idx = tvLastCandles.findIndex(candle => candle.time >= tvCurrentDayStartTime);
                if (idx >= 0) return idx;
            }
            return Math.max(0, tvLastCandles.length - 80);
        }

        function tvGetSessionFocusLogicalRange() {
            if (!tvLastCandles.length) return null;
            const startIndex = tvGetCurrentSessionStartIndex();
            const sessionBars = Math.max(1, tvLastCandles.length - startIndex);
            const leftPaddingBars = tvCurrentDayStartTime
                ? Math.max(24, Math.min(120, Math.round(sessionBars * 0.45)))
                : Math.max(12, Math.min(64, Math.round(sessionBars * 0.20)));
            const rightPaddingBars = Math.max(3, Math.min(12, Math.round(sessionBars * 0.06)));
            return {
                from: Math.max(0, startIndex - leftPaddingBars),
                to: (tvLastCandles.length - 1) + rightPaddingBars,
            };
        }

        function tvFocusCurrentSession() {
            if (!tvPriceChart || !tvLastCandles.length) return;
            tvYAxisMode = 'session';
            setTimeout(() => {
                try {
                    const range = tvGetSessionFocusLogicalRange();
                    if (!range) return;
                    tvPriceChart.timeScale().setVisibleLogicalRange(range);
                    tvPriceChart.priceScale('right').applyOptions({ autoScale: true });
                    tvApplyAutoscale();
                    if (tvRsiChart)  tvRsiChart.priceScale('right').applyOptions({ autoScale: true });
                    if (tvMacdChart) tvMacdChart.priceScale('right').applyOptions({ autoScale: true });
                    scheduleTVHistoricalOverlayDraw();
                    scheduleGexPanelSync();
                } catch(e) {}
            }, 50);
        }

        // ── Real-time price streaming via Server-Sent Events ─────────────────
        function disconnectPriceStream() {
            if (priceEventSource) {
                priceEventSource.close();
                priceEventSource = null;
                priceStreamTicker = null;
            }
        }

        function connectPriceStream(ticker) {
            if (!ticker) return;
            const upperTicker = ticker.toUpperCase();
            // Already connected to the right ticker – nothing to do
            if (priceEventSource && priceStreamTicker === upperTicker &&
                priceEventSource.readyState !== EventSource.CLOSED) {
                return;
            }
            // Disconnect any existing connection first
            disconnectPriceStream();

            priceEventSource = new EventSource('/price_stream/' + encodeURIComponent(upperTicker));
            priceStreamTicker = upperTicker;

            priceEventSource.onmessage = function(event) {
                try {
                    const msg = JSON.parse(event.data);
                    if (!tvCandleSeries || !tvLastCandles.length) return;
                    if (msg.type === 'quote' && typeof msg.last === 'number') {
                        applyRealtimeQuote(msg.last);
                    } else if (msg.type === 'candle' && msg.time) {
                        applyRealtimeCandle(msg);
                    }
                } catch(e) {}
            };

            priceEventSource.onerror = function() {
                // Browser will auto-reconnect on error; just log it quietly
                console.debug('[PriceStream] Connection error – browser will retry.');
            };
        }

        /**
         * Bucket size in seconds for the currently selected chart timeframe.
         * The SSE stream is always 1-minute; we aggregate into the displayed bucket.
         */
        function tvBucketSec() {
            const el = document.getElementById('timeframe');
            const m = parseInt(el && el.value, 10);
            return (isFinite(m) && m > 0 ? m : 1) * 60;
        }

        function tvBucketStartForUnixSec(unixSec) {
            if (!Number.isFinite(unixSec)) return null;
            const bucketSec = tvBucketSec();
            if (tvCurrentDayStartTime > 0 && bucketSec >= 4 * 60 * 60) {
                return tvCurrentDayStartTime
                    + (Math.floor((unixSec - tvCurrentDayStartTime) / bucketSec) * bucketSec);
            }
            return Math.floor(unixSec / bucketSec) * bucketSec;
        }

        function tvToSeriesBar(bar) {
            return {
                time: bar.time,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close,
                volume: bar.volume || 0,
            };
        }

        /**
         * Update the current bucket's high/low/close from a real-time last price.
         * Bucket boundaries follow the selected timeframe.
         */
        function applyRealtimeQuote(last) {
            // Track live price and debounce Plotly chart updates
            livePrice = last;
            clearTimeout(plotlyPriceUpdateTimer);
            plotlyPriceUpdateTimer = setTimeout(function() { updateAllPlotlyPriceLines(last); }, 500);

            if (!tvCandleSeries || !tvLastCandles.length) return;
            const nowSec = Math.floor(Date.now() / 1000);
            const bucketStart = tvBucketStartForUnixSec(nowSec);
            const lastCandle = tvLastCandles[tvLastCandles.length - 1];

            if (lastCandle.time === bucketStart) {
                // Update the existing in-progress bucket
                const updated = {
                    time:   lastCandle.time,
                    open:   lastCandle.open,
                    high:   Math.max(lastCandle.high, last),
                    low:    Math.min(lastCandle.low,  last),
                    close:  last,
                    volume: lastCandle.volume || 0,
                    __quoteSeeded: !!lastCandle.__quoteSeeded,
                    __quoteDirty: true,
                };
                try { tvCandleSeries.update(tvToSeriesBar(updated)); } catch(e) {}
                tvLastCandles[tvLastCandles.length - 1] = updated;
                // Keep multi-day indicator candles in sync
                const icLast = tvIndicatorCandles[tvIndicatorCandles.length - 1];
                if (icLast && icLast.time === updated.time) {
                    tvIndicatorCandles[tvIndicatorCandles.length - 1] = updated;
                }
                // Debounce indicator refresh to at most once every 2 seconds on tick updates
                if (tvActiveInds.size > 0) {
                    clearTimeout(tvIndicatorRefreshTimer);
                    tvIndicatorRefreshTimer = setTimeout(() => applyIndicators(tvIndicatorCandles, tvActiveInds), 2000);
                }
            } else if (bucketStart > lastCandle.time) {
                // Clock has rolled past the bucket boundary but CHART_EQUITY hasn't
                // pushed the new bar yet. Seed a provisional bucket from the last
                // confirmed close so the candle can keep wiggling without implying
                // any live volume; the 1-min stream merges into this bucket later.
                const anchor = Number.isFinite(lastCandle.close) ? lastCandle.close : last;
                const fresh = {
                    time:   bucketStart,
                    open:   anchor,
                    high:   Math.max(anchor, last),
                    low:    Math.min(anchor, last),
                    close:  last,
                    volume: 0,
                    __quoteSeeded: true,
                    __quoteDirty: false,
                };
                try { tvCandleSeries.update(tvToSeriesBar(fresh)); } catch(e) {}
                tvLastCandles.push(fresh);
                const icLast = tvIndicatorCandles[tvIndicatorCandles.length - 1];
                if (!icLast || icLast.time < bucketStart) tvIndicatorCandles.push(fresh);
            }
        }

        /**
         * Apply a 1-minute candle from CHART_EQUITY streaming, rolled into the
         * currently selected timeframe bucket. Schwab always pushes 1-min bars;
         * on a 5-min/15-min/etc chart we merge into the containing bucket instead
         * of appending the raw 1-min bar (which would render as a tiny sliver).
         */
        function applyRealtimeCandle(candle) {
            if (!tvCandleSeries) return;
            const bucketStart = tvBucketStartForUnixSec(candle.time);

            function rollInto(arr) {
                const idx = arr.findIndex(x => x.time === bucketStart);
                if (idx >= 0) {
                    const b = arr[idx];
                    arr[idx] = {
                        time:   b.time,
                        open:   b.__quoteSeeded ? candle.open : b.open,
                        high:   Math.max(b.high, candle.high),
                        low:    Math.min(b.low,  candle.low),
                        close:  b.__quoteDirty ? b.close : candle.close,
                        volume: (b.volume || 0) + (candle.volume || 0),
                        __quoteSeeded: false,
                        __quoteDirty: false,
                    };
                    return arr[idx];
                }
                // First 1-min candle of a new bucket — open the bucket with it.
                const fresh = {
                    time:   bucketStart,
                    open:   candle.open,
                    high:   candle.high,
                    low:    candle.low,
                    close:  candle.close,
                    volume: candle.volume || 0,
                    __quoteSeeded: false,
                    __quoteDirty: false,
                };
                arr.push(fresh);
                arr.sort((a, b) => a.time - b.time);
                return fresh;
            }

            const displayBar = rollInto(tvLastCandles);
            rollInto(tvIndicatorCandles);
            try { tvCandleSeries.update(tvToSeriesBar(displayBar)); } catch(e) {}
            try {
                tvVolumeSeries.update({
                    time:  displayBar.time,
                    value: displayBar.volume || 0,
                    color: displayBar.close >= displayBar.open ? callColor : putColor,
                });
            } catch(e) {}
            if (tvActiveInds.size > 0) applyIndicators(tvIndicatorCandles, tvActiveInds);
        }

        // --- Fullscreen chart support ---
        const fsExpandSvg = '<svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M1 5V1h4M9 1h4v4M13 9v4H9M5 13H1V9"/></svg>';
        const fsCollapseSvg = '<svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 1v4H1M9 5h4V1M9 13V9h4M1 9h4v4"/></svg>';

        function toggleChartFullscreen(container) {
            const isFullscreen = container.classList.contains('fullscreen');

            // Exit any other fullscreen chart first
            document.querySelectorAll('.chart-container.fullscreen').forEach(el => {
                el.classList.remove('fullscreen');
                const b = el.querySelector('.chart-fullscreen-btn');
                if (b) b.innerHTML = fsExpandSvg;
            });

            if (!isFullscreen) {
                container.classList.add('fullscreen');
                document.body.style.overflow = 'hidden';
                const b = container.querySelector('.chart-fullscreen-btn');
                if (b) b.innerHTML = fsCollapseSvg;
            } else {
                document.body.style.overflow = '';
            }

            // Let Plotly know about the size change; also trigger TV chart resize
            requestAnimationFrame(() => {
                document.querySelectorAll('.chart-container').forEach(el => {
                    const plot = el.querySelector('.js-plotly-plot');
                    if (plot) { try { Plotly.Plots.resize(plot); } catch(e) {} }
                });
                // Resize TradingView price chart
                const tvContainer = document.getElementById('price-chart');
                if (tvPriceChart && tvContainer) {
                    tvPriceChart.applyOptions({ width: tvContainer.clientWidth });
                }
            });
        }

        function addFullscreenButton(container) {
            if (!container || container.querySelector('.chart-fullscreen-btn')) return;
            const btn = document.createElement('button');
            btn.className = 'chart-fullscreen-btn';
            btn.innerHTML = container.classList.contains('fullscreen') ? fsCollapseSvg : fsExpandSvg;
            btn.title = 'Toggle fullscreen (Esc to exit)';
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                toggleChartFullscreen(container);
            });
            container.appendChild(btn);
        }

        // ESC key exits fullscreen chart
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const fs = document.querySelector('.chart-container.fullscreen');
                if (fs) {
                    fs.classList.remove('fullscreen');
                    document.body.style.overflow = '';
                    const b = fs.querySelector('.chart-fullscreen-btn');
                    if (b) b.innerHTML = fsExpandSvg;
                    requestAnimationFrame(() => {
                        document.querySelectorAll('.chart-container').forEach(el => {
                            const plot = el.querySelector('.js-plotly-plot');
                            if (plot) { try { Plotly.Plots.resize(plot); } catch(e) {} }
                        });
                        const tvContainer = document.getElementById('price-chart');
                        if (tvPriceChart && tvContainer) {
                            tvPriceChart.applyOptions({ width: tvContainer.clientWidth });
                        }
                    });
                }
            }
        });
        // Helper: returns appropriate Plotly margins depending on whether chart is fullscreen
        function getChartMargins(containerId, defaultMargins) {
            const container = document.getElementById(containerId);
            if (container && container.classList.contains('fullscreen')) {
                return {
                    l: Math.max(defaultMargins.l || 50, 60),
                    r: Math.max(defaultMargins.r || 50, 130),
                    t: Math.max(defaultMargins.t || 40, 60),
                    b: Math.max(defaultMargins.b || 20, 40)
                };
            }
            return defaultMargins;
        }
        // --- End fullscreen support ---

        // --- Pop-out (Picture-in-Picture) chart support ---
        const popoutWindows = {}; // Map of chartId -> Window reference
        const popoutSvg = '<svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 1h3v3M13 1L8 6M5 2H2v10h10V9"/></svg>';

        function openPopoutChart(chartId) {
            // If already open and not closed, focus it
            if (popoutWindows[chartId] && !popoutWindows[chartId].closed) {
                popoutWindows[chartId].focus();
                return;
            }

            // Derive a display name from the chart id
            const displayName = chartId.replace('-chart', '').replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());

            const popup = window.open('', 'popout_' + chartId, 'width=900,height=650,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=no');
            if (!popup) {
                showError('Pop-up blocked! Please allow pop-ups for this site.');
                return;
            }

            // Price chart uses TradingView Lightweight Charts — needs a different template
            if (chartId === 'price-chart') {
                popup.document.write(`<!DOCTYPE html>
<html><head><title>Price Chart - EzOptions</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"><\\/script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#1E1E1E; color:#ccc; font-family:Arial,sans-serif; display:flex; flex-direction:column; height:100vh; overflow:hidden; }
  #popout-logo { position:fixed; top:6px; left:10px; z-index:200; font-size:11px; font-weight:bold; color:#800080; opacity:0.7; pointer-events:none; letter-spacing:0.5px; }
  #toolbar { background:#1a1a1a; border-bottom:1px solid #333; padding:4px 8px; display:flex; flex-wrap:wrap; gap:4px; align-items:center; flex-shrink:0; z-index:100; }
  .tv-tb-sep { width:1px; height:20px; background:#444; margin:0 2px; }
  .tb-btn { background:#2a2a2a; border:1px solid #444; color:#ccc; border-radius:4px; padding:3px 7px; font-size:11px; cursor:pointer; white-space:nowrap; transition:background 0.15s; user-select:none; }
  .tb-btn:hover  { background:#3a3a3a; color:#fff; }
  .tb-btn.active { background:#1a5fac; border-color:#4b90e2; color:#fff; }
  .tb-btn.danger { background:#5c1a1a; border-color:#c0392b; color:#f88; }
  #chart-area { flex:1; display:flex; flex-direction:column; min-height:0; position:relative; }
  #price-chart { flex:1; min-height:0; position:relative; }
  .tv-sub-pane { background:#1E1E1E; border-top:1px solid #333; flex-shrink:0; position:relative; }
  .tv-sub-pane-hdr { position:absolute; top:4px; left:8px; z-index:5; font-size:10px; color:#888; font-weight:bold; pointer-events:none; }
  .ind-legend { position:absolute; bottom:8px; left:8px; display:flex; flex-wrap:wrap; gap:6px; z-index:15; pointer-events:none; }
  .ind-item { font-size:10px; color:#ccc; display:flex; align-items:center; gap:4px; }
  .ind-swatch { width:14px; height:3px; border-radius:2px; }
  .title-el { display:inline-block; color:#ccc; font-size:13px; font-weight:bold; padding:2px 8px; pointer-events:none; }
  .candle-close-timer { font-size:11px; font-family:'Courier New',monospace; padding:3px 7px; border-radius:4px; background:#2a2a2a; border:1px solid #444; color:#ccc; white-space:nowrap; user-select:none; letter-spacing:0.5px; }
  .tv-ohlc-tooltip { position:absolute; top:8px; left:8px; z-index:50; font-size:11px; font-family:'Courier New',monospace; color:#ccc; pointer-events:none; white-space:nowrap; width:max-content; display:none; line-height:1.6; }
  .tv-ohlc-tooltip .tt-time { color:#aaa; font-size:10px; margin-bottom:2px; }
  .tv-ohlc-tooltip .tt-up { color:#10B981; }
  .tv-ohlc-tooltip .tt-dn { color:#EF4444; }
    .tv-historical-overlay { position:absolute; inset:0; z-index:4; pointer-events:none; overflow:hidden; }
    .tv-historical-bubble { position:absolute; border-radius:999px; transform:translate(-50%,-50%); box-shadow:0 0 0 1px rgba(0,0,0,0.25); opacity:0.95; pointer-events:auto; cursor:pointer; }
    .tv-historical-tooltip { position:absolute; z-index:55; display:none; width:auto !important; height:auto !important; min-width:0; max-width:min(240px,calc(100% - 16px)); padding:8px; border:1px solid rgba(255,255,255,0.08); border-radius:10px; background:linear-gradient(180deg,rgba(30,34,41,0.96),rgba(16,18,23,0.98)); color:#eef2f7; font-size:10px; line-height:1.25; pointer-events:none; box-shadow:0 14px 36px rgba(0,0,0,0.38); backdrop-filter:blur(10px); flex:none !important; align-self:flex-start; overflow:hidden; white-space:normal; }
    .tv-historical-tooltip .tt-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:6px; }
    .tv-historical-tooltip .tt-badge { padding:2px 6px; border-radius:999px; background:rgba(255,255,255,0.08); color:#c9d1db; font-size:9px; letter-spacing:0.02em; text-transform:uppercase; }
    .tv-historical-tooltip .tt-time { color:#8f9baa; font-size:9px; margin-bottom:0; }
    .tv-historical-tooltip .tt-list { display:grid; gap:4px; }
    .tv-historical-tooltip .tt-row { display:flex; align-items:center; gap:6px; min-width:0; }
    .tv-historical-tooltip .tt-dot { width:7px; height:7px; border-radius:999px; box-shadow:0 0 0 1px rgba(255,255,255,0.12); flex:0 0 auto; }
    .tv-historical-tooltip .tt-main { display:flex; justify-content:space-between; align-items:baseline; gap:8px; min-width:0; width:100%; }
    .tv-historical-tooltip .tt-name { color:#f4f7fb; font-weight:600; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .tv-historical-tooltip .tt-value { color:#9fb0c4; font-variant-numeric:tabular-nums; white-space:nowrap; flex:0 0 auto; }
    .tv-historical-tooltip .tt-more { color:#8190a3; margin-top:4px; padding-top:4px; border-top:1px solid rgba(255,255,255,0.06); font-size:9px; }
</style></head><body>
<div id="popout-logo">EzDuz1t Options</div>
<div id="toolbar">
  <span class="title-el" id="chart-title">Price Chart</span>
  <div class="tv-tb-sep"></div>
</div>
<div id="chart-area">
  <div id="price-chart"></div>
  <div class="tv-sub-pane" id="rsi-pane" style="display:none"><div class="tv-sub-pane-hdr">RSI 14</div><div id="rsi-chart" style="height:110px"></div></div>
  <div class="tv-sub-pane" id="macd-pane" style="display:none"><div class="tv-sub-pane-hdr">MACD (12,26,9)</div><div id="macd-chart" style="height:120px"></div></div>
</div>
<script>
  // ── State ──────────────────────────────────────────────────────────────────
  var tvChart=null, tvCandle=null, tvVol=null;
  var tvRsiChart=null, tvRsiSeries=null;
  var tvMacdChart=null, tvMacdSeries={};
  var tvIndSeries={};
  var activeInds=new Set();
  var tvPriceLines=[], tvDrawings=[], tvDrawingDefs=[];
  var tvAllLevelPrices=[];
    var tvHistoricalPoints=[];
  var tvLastCandles=[];
  var tvDrawMode=null, tvDrawStart=null;
  var tvAutoRange=false;
  var tvSyncHandlers=[], tvSyncingTS=false;
  var drawColor='#FFD700';
  var lineStyleMap={};
  var popoutTimeframe=1;
  var popoutCandleTimerInterval=null;
  var popoutUpColor='#10B981', popoutDownColor='#EF4444';
    var historicalDomBound=false;
    var tvHistoricalRenderedPoints=[];
        var historicalBubbleDrawPending=false;
        var historicalBubbleMaxVisible=1200;

  // ── Candle close timer (popout) ────────────────────────────────────────────
  function startCandleCloseTimer(){
    if(popoutCandleTimerInterval)clearInterval(popoutCandleTimerInterval);
    function upd(){
      var el=document.getElementById('candle-close-timer');if(!el){clearInterval(popoutCandleTimerInterval);return;}
      var tfSecs=popoutTimeframe*60;
      var now=new Date();
      var fmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'numeric',second:'numeric',hour12:false});
      var h=0,m2=0,s2=0;
      fmt.formatToParts(now).forEach(function(p){if(p.type==='hour')h=parseInt(p.value);else if(p.type==='minute')m2=parseInt(p.value);else if(p.type==='second')s2=parseInt(p.value);});
      var sec=h*3600+m2*60+s2;
      var rem=tfSecs-(sec%tfSecs);
      var mm=Math.floor(rem/60),ss=rem%60;
      el.textContent='\u23F1 '+mm+':'+(ss<10?'0':'')+ss;
      el.className='candle-close-timer';
    }
    upd();
    popoutCandleTimerInterval=setInterval(upd,1000);
  }

  // ── Math helpers ───────────────────────────────────────────────────────────
  function calcSMA(c,p){return c.map(function(_,i){if(i<p-1)return null;var s=c.slice(i-p+1,i+1);return s.reduce(function(a,b){return a+b;},0)/p;});}
  function calcEMA(c,p){var k=2/(p+1),r=[],e=null;for(var i=0;i<c.length;i++){if(i<p-1){r.push(null);continue;}if(e===null){e=c.slice(0,p).reduce(function(a,b){return a+b;},0)/p;}else{e=c[i]*k+e*(1-k);}r.push(e);}return r;}
  function calcVWAP(cs){var cp=0,cv=0;return cs.map(function(c){var t=(c.high+c.low+c.close)/3;cp+=t*c.volume;cv+=c.volume;return cv>0?cp/cv:c.close;});}
  function calcBB(c,p,m){p=p||20;m=m||2;var s=calcSMA(c,p);return s.map(function(mid,i){if(mid===null)return{upper:null,mid:null,lower:null};var sl=c.slice(Math.max(0,i-p+1),i+1),v=sl.reduce(function(a,b){return a+(b-mid)*(b-mid);},0)/sl.length,sd=Math.sqrt(v);return{upper:mid+m*sd,mid:mid,lower:mid-m*sd};});}
  function calcRSI(c,p){p=p||14;var r=[];for(var i=0;i<c.length;i++){if(i<p){r.push(null);continue;}var g=0,l=0;for(var j=i-p+1;j<=i;j++){var d=c[j]-c[j-1];if(d>0)g+=d;else l-=d;}var ag=g/p,al=l/p;r.push(al===0?100:100-100/(1+ag/al));}return r;}
  function calcATR(candles,p){p=p||14;var r=[];for(var i=0;i<candles.length;i++){var tr;if(i===0){tr=candles[i].high-candles[i].low;}else{tr=Math.max(candles[i].high-candles[i].low,Math.abs(candles[i].high-candles[i-1].close),Math.abs(candles[i].low-candles[i-1].close));}if(i<p-1){r.push(null);continue;}if(r.length===0||r[r.length-1]===null){var sum=0;for(var j=i-p+1;j<=i;j++){var t2;if(j===0){t2=candles[j].high-candles[j].low;}else{t2=Math.max(candles[j].high-candles[j].low,Math.abs(candles[j].high-candles[j-1].close),Math.abs(candles[j].low-candles[j-1].close));}sum+=t2;}r.push(sum/p);}else{r.push((r[r.length-1]*(p-1)+tr)/p);}}return r;}
  function calcMACD(c,fast,slow,sig){fast=fast||12;slow=slow||26;sig=sig||9;var ef=calcEMA(c,fast),es=calcEMA(c,slow);var ml=ef.map(function(v,i){return(v!==null&&es[i]!==null)?v-es[i]:null;});var sl=[],es2=null,vi=0,k=2/(sig+1);for(var i=0;i<ml.length;i++){if(ml[i]===null){sl.push(null);continue;}if(vi<sig-1){sl.push(null);vi++;continue;}if(es2===null){var piece=ml.filter(function(v){return v!==null;}).slice(0,sig);es2=piece.reduce(function(a,b){return a+b;},0)/sig;}else{es2=ml[i]*k+es2*(1-k);}sl.push(es2);vi++;}return{macd:ml,signal:sl,histogram:ml.map(function(v,i){return(v!==null&&sl[i]!==null)?v-sl[i]:null;})};}

  // ── Sub-pane chart factory ─────────────────────────────────────────────────
  function mkSubChart(el,h){return LightweightCharts.createChart(el,{autoSize:true,height:h,layout:{background:{color:'#1E1E1E'},textColor:'#CCCCCC',fontFamily:'Arial,sans-serif'},grid:{vertLines:{color:'#2A2A2A'},horzLines:{color:'#2A2A2A'}},crosshair:{mode:LightweightCharts.CrosshairMode.Normal,vertLine:{color:'#555',labelBackgroundColor:'#2D2D2D'},horzLine:{color:'#555',labelBackgroundColor:'#2D2D2D'}},rightPriceScale:{borderColor:'#333',scaleMargins:{top:0.1,bottom:0.1}},timeScale:{borderColor:'#333',timeVisible:false,secondsVisible:false,fixLeftEdge:true,fixRightEdge:false},handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true},handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false}});}

  // ── Time-scale sync ────────────────────────────────────────────────────────
  function setupSync(){tvSyncHandlers.forEach(function(h){try{h.chart.timeScale().unsubscribeVisibleLogicalRangeChange(h.handler);}catch(e){}});tvSyncHandlers=[];var all=[tvChart,tvRsiChart,tvMacdChart].filter(Boolean);if(all.length<2)return;all.forEach(function(src){var others=all.filter(function(c){return c!==src;});var h=function(range){if(tvSyncingTS||!range)return;tvSyncingTS=true;others.forEach(function(c){try{c.timeScale().setVisibleLogicalRange(range);}catch(e){}});tvSyncingTS=false;};try{src.timeScale().subscribeVisibleLogicalRangeChange(h);}catch(e){}tvSyncHandlers.push({chart:src,handler:h});});if(tvChart){try{var r=tvChart.timeScale().getVisibleLogicalRange();if(r)[tvRsiChart,tvMacdChart].filter(Boolean).forEach(function(c){try{c.timeScale().setVisibleLogicalRange(r);}catch(e){}});}catch(e){}}}

  // ── Indicators ─────────────────────────────────────────────────────────────
  function applyIndicators(candles){
    if(!tvChart||!tvCandle)return;
    var times=candles.map(function(c){return c.time;}),closes=candles.map(function(c){return c.close;});
    function mkLine(col,lw,title){return tvChart.addLineSeries({color:col,lineWidth:lw||1,priceScaleId:'right',lastValueVisible:true,priceLineVisible:false,title:title||''});}
    // Remove deactivated
    Object.keys(tvIndSeries).forEach(function(k){if(!activeInds.has(k)){var s=tvIndSeries[k];if(Array.isArray(s))s.forEach(function(x){try{tvChart.removeSeries(x);}catch(e){}});else{try{tvChart.removeSeries(s);}catch(e){};}delete tvIndSeries[k];}});
    // Add activated
    if(activeInds.has('sma20')&&!tvIndSeries['sma20']){var s=mkLine('#f0c040',1,'SMA20');s.setData(calcSMA(closes,20).map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean));tvIndSeries['sma20']=s;}
    if(activeInds.has('sma50')&&!tvIndSeries['sma50']){var s=mkLine('#40a0f0',1,'SMA50');s.setData(calcSMA(closes,50).map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean));tvIndSeries['sma50']=s;}
    if(activeInds.has('sma200')&&!tvIndSeries['sma200']){var s=mkLine('#e040fb',1,'SMA200');s.setData(calcSMA(closes,200).map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean));tvIndSeries['sma200']=s;}
    if(activeInds.has('ema9')&&!tvIndSeries['ema9']){var s=mkLine('#ff9900',1,'EMA9');s.setData(calcEMA(closes,9).map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean));tvIndSeries['ema9']=s;}
    if(activeInds.has('ema21')&&!tvIndSeries['ema21']){var s=mkLine('#00e5ff',1,'EMA21');s.setData(calcEMA(closes,21).map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean));tvIndSeries['ema21']=s;}
    if(activeInds.has('vwap')&&!tvIndSeries['vwap']){var vv=calcVWAP(candles.map(function(c,i){return{high:candles[i].high,low:candles[i].low,close:candles[i].close,volume:c.volume||0};}));var s=mkLine('#ffffff',1,'VWAP');s.setData(vv.map(function(v,i){return{time:times[i],value:v};}));tvIndSeries['vwap']=s;}
    if(activeInds.has('bb')&&!tvIndSeries['bb']){var bb=calcBB(closes);var u=mkLine('rgba(100,180,255,0.8)',1,'BB U'),m=mkLine('rgba(100,180,255,0.5)',1,'BB M'),l=mkLine('rgba(100,180,255,0.8)',1,'BB L');u.setData(bb.map(function(v,i){return v.upper!==null?{time:times[i],value:v.upper}:null;}).filter(Boolean));m.setData(bb.map(function(v,i){return v.mid!==null?{time:times[i],value:v.mid}:null;}).filter(Boolean));l.setData(bb.map(function(v,i){return v.lower!==null?{time:times[i],value:v.lower}:null;}).filter(Boolean));tvIndSeries['bb']=[u,m,l];}
    if(activeInds.has('atr')&&!tvIndSeries['atr']){var atrV=calcATR(candles),e20=calcEMA(closes,20),mult=1.5;var au=mkLine('rgba(255,152,0,0.8)',1,'ATR U'),al=mkLine('rgba(255,152,0,0.8)',1,'ATR L');au.setData(e20.map(function(v,i){return(v!==null&&atrV[i]!==null)?{time:times[i],value:v+mult*atrV[i]}:null;}).filter(Boolean));al.setData(e20.map(function(v,i){return(v!==null&&atrV[i]!==null)?{time:times[i],value:v-mult*atrV[i]}:null;}).filter(Boolean));tvIndSeries['atr']=[au,al];}
    if(activeInds.has('rsi'))applyRsiPane(candles,times);else destroyRsiPane();
    if(activeInds.has('macd'))applyMacdPane(candles,times);else destroyMacdPane();
    updateLegend();
  }
  function applyRsiPane(candles,times){
    var pane=document.getElementById('rsi-pane');if(!pane)return;pane.style.display='block';
    var rsiVals=calcRSI(candles.map(function(c){return c.close;}));
    var rsiData=rsiVals.map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean);
    if(!tvRsiChart){var el=document.getElementById('rsi-chart');if(!el)return;tvRsiChart=mkSubChart(el,110);tvRsiSeries=tvRsiChart.addLineSeries({color:'#e91e63',lineWidth:1.5,lastValueVisible:true,priceLineVisible:false,title:'RSI14'});tvRsiSeries.createPriceLine({price:70,color:'rgba(255,100,100,0.7)',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:true,title:'70'});tvRsiSeries.createPriceLine({price:30,color:'rgba(100,200,100,0.7)',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:true,title:'30'});}
    if(rsiData.length)tvRsiSeries.setData(rsiData);
    setupSync();
  }
  function destroyRsiPane(){var pane=document.getElementById('rsi-pane');if(pane)pane.style.display='none';if(tvRsiChart){tvSyncHandlers=tvSyncHandlers.filter(function(h){return h.chart!==tvRsiChart;});try{tvRsiChart.remove();}catch(e){}tvRsiChart=null;tvRsiSeries=null;}}
  function applyMacdPane(candles,times){
    var pane=document.getElementById('macd-pane');if(!pane)return;pane.style.display='block';
    var md=calcMACD(candles.map(function(c){return c.close;}));
    var hd=md.histogram.map(function(v,i){return v!==null?{time:times[i],value:v,color:v>=0?'rgba(76,175,80,0.8)':'rgba(244,67,54,0.8)'}:null;}).filter(Boolean);
    var ld=md.macd.map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean);
    var sd=md.signal.map(function(v,i){return v!==null?{time:times[i],value:v}:null;}).filter(Boolean);
    if(!tvMacdChart){var el=document.getElementById('macd-chart');if(!el)return;tvMacdChart=mkSubChart(el,120);tvMacdSeries.hist=tvMacdChart.addHistogramSeries({lastValueVisible:false,priceLineVisible:false});tvMacdSeries.line=tvMacdChart.addLineSeries({color:'#2196f3',lineWidth:1.5,lastValueVisible:true,priceLineVisible:false,title:'MACD'});tvMacdSeries.signal=tvMacdChart.addLineSeries({color:'#ff9800',lineWidth:1,lastValueVisible:true,priceLineVisible:false,title:'Signal'});}
    if(hd.length)tvMacdSeries.hist.setData(hd);if(ld.length)tvMacdSeries.line.setData(ld);if(sd.length)tvMacdSeries.signal.setData(sd);
    setupSync();
  }
  function destroyMacdPane(){var pane=document.getElementById('macd-pane');if(pane)pane.style.display='none';if(tvMacdChart){tvSyncHandlers=tvSyncHandlers.filter(function(h){return h.chart!==tvMacdChart;});try{tvMacdChart.remove();}catch(e){}tvMacdChart=null;tvMacdSeries={
};}}
  function updateLegend(){
    var cont=document.getElementById('price-chart');if(!cont)return;
    var leg=cont.querySelector('.ind-legend');if(!leg){leg=document.createElement('div');leg.className='ind-legend';cont.appendChild(leg);}
    var cols={sma20:'#f0c040',sma50:'#40a0f0',sma200:'#e040fb',ema9:'#ff9900',ema21:'#00e5ff',vwap:'#ffffff',bb:'rgba(100,180,255,0.8)',rsi:'#e91e63',macd:'#2196f3',atr:'rgba(255,152,0,0.8)'};
    var lbls={sma20:'SMA20',sma50:'SMA50',sma200:'SMA200',ema9:'EMA9',ema21:'EMA21',vwap:'VWAP',bb:'BB(20,2)',rsi:'RSI14',macd:'MACD',atr:'ATR Bands'};
    leg.innerHTML=Object.keys(tvIndSeries).map(function(k){return '<div class="ind-item"><div class="ind-swatch" style="background:'+( cols[k]||'#888')+'"></div>'+(lbls[k]||k)+'</div>';}).join('');
  }

  // ── Drawing tools ──────────────────────────────────────────────────────────
  function setDrawMode(mode){tvDrawMode=(tvDrawMode===mode)?null:mode;tvDrawStart=null;document.querySelectorAll('.tb-btn[data-draw]').forEach(function(b){b.classList.toggle('active',b.dataset.draw===tvDrawMode);});}
  function doUndo(){if(!tvChart||tvDrawings.length===0)return;var last=tvDrawings.pop();tvDrawingDefs.pop();if(Array.isArray(last))last.forEach(function(s){try{tvChart.removeSeries(s);}catch(e){}});else if(last&&last._isLine){try{tvCandle.removePriceLine(last);}catch(e){};}else{try{tvChart.removeSeries(last);}catch(e){}}}
  function doClear(){if(!tvChart)return;while(tvDrawings.length>0){var last=tvDrawings.pop();if(Array.isArray(last))last.forEach(function(s){try{tvChart.removeSeries(s);}catch(e){}});else if(last&&last._isLine){try{tvCandle.removePriceLine(last);}catch(e){};}else{try{tvChart.removeSeries(last);}catch(e){}}}tvDrawingDefs=[];}
  function handleClick(param){
    if(!tvDrawMode||!param||!param.point)return;
    var price=tvCandle?tvCandle.coordinateToPrice(param.point.y):null;
    if(price===null||price===undefined)return;
    var LS=LightweightCharts.LineStyle;
    if(tvDrawMode==='hline'){var l=tvCandle.createPriceLine({price:price,color:drawColor,lineWidth:1,lineStyle:LS.Solid,axisLabelVisible:true,title:''});l._isLine=true;tvDrawings.push(l);tvDrawingDefs.push({type:'hline',price:price,color:drawColor});return;}
    var clickTime=param.time;
    if(!clickTime&&tvLastCandles.length){try{clickTime=tvChart.timeScale().coordinateToTime(param.point.x);}catch(e){}if(!clickTime){var idx=Math.max(0,Math.min(Math.round(param.logical!=null?param.logical:tvLastCandles.length-1),tvLastCandles.length-1));clickTime=tvLastCandles[idx].time;}}
    if(tvDrawMode==='trendline'||tvDrawMode==='rect'){if(!clickTime)return;if(!tvDrawStart){tvDrawStart={price:price,time:clickTime};}else{if(tvDrawMode==='trendline'){var t1=tvDrawStart.time,p1=tvDrawStart.price,t2=clickTime,p2=price,tMin=Math.min(t1,t2),tMax=Math.max(t1,t2),vMin=t1<=t2?p1:p2,vMax=t1<=t2?p2:p1;var s=tvChart.addLineSeries({color:drawColor,lineWidth:1,priceScaleId:'right',lastValueVisible:false,priceLineVisible:false});s.setData([{time:tMin,value:vMin},{time:tMax,value:vMax}]);tvDrawings.push(s);tvDrawingDefs.push({type:'trendline',t1:t1,p1:p1,t2:t2,p2:p2,color:drawColor});}else{var top=Math.max(tvDrawStart.price,price),bot=Math.min(tvDrawStart.price,price);var tl=tvCandle.createPriceLine({price:top,color:drawColor,lineWidth:1,lineStyle:LS.Solid,axisLabelVisible:false,title:''});var bl=tvCandle.createPriceLine({price:bot,color:drawColor,lineWidth:1,lineStyle:LS.Solid,axisLabelVisible:false,title:''});tl._isLine=true;bl._isLine=true;tvDrawings.push([tl,bl]);tvDrawingDefs.push({type:'rect',top:top,bot:bot,color:drawColor});}tvDrawStart=null;}return;}
    if(tvDrawMode==='text'){var txt=prompt('Enter label text:');if(!txt)return;var l=tvCandle.createPriceLine({price:price,color:drawColor,lineWidth:0,lineStyle:LS.Solid,axisLabelVisible:true,title:txt});l._isLine=true;tvDrawings.push(l);tvDrawingDefs.push({type:'text',price:price,text:txt,color:drawColor});}
  }

  // ── Toolbar ────────────────────────────────────────────────────────────────
  function buildToolbar(candles,upColor,downColor){
    var tb=document.getElementById('toolbar');
    // Remove everything except the title and sep (first 2 children)
    while(tb.children.length>2)tb.removeChild(tb.lastChild);
    function btn(text,title,onClick,extra){var b=document.createElement('button');b.className='tb-btn'+(extra?' '+extra:'');b.textContent=text;b.title=title;b.addEventListener('click',onClick);return b;}
    function sep(){var d=document.createElement('div');d.className='tv-tb-sep';return d;}
    // Indicator toggles
    var inds=[{k:'sma20',l:'SMA20',t:'SMA 20'},{k:'sma50',l:'SMA50',t:'SMA 50'},{k:'sma200',l:'SMA200',t:'SMA 200'},{k:'ema9',l:'EMA9',t:'EMA 9'},{k:'ema21',l:'EMA21',t:'EMA 21'},{k:'vwap',l:'VWAP',t:'VWAP'},{k:'bb',l:'BB',t:'Bollinger Bands (20,2)'},{k:'rsi',l:'RSI',t:'RSI 14 — sub-pane'},{k:'macd',l:'MACD',t:'MACD (12,26,9) — sub-pane'},{k:'atr',l:'ATR',t:'Average True Range 14 — sub-pane'}];
    inds.forEach(function(def){var b=btn(def.l,def.t,function(){if(activeInds.has(def.k))activeInds.delete(def.k);else activeInds.add(def.k);b.classList.toggle('active',activeInds.has(def.k));applyIndicators(tvLastCandles);});if(activeInds.has(def.k))b.classList.add('active');tb.appendChild(b);});
    tb.appendChild(sep());
    // Drawing tools
    var draws=[{k:'hline',l:'— H-Line',t:'Horizontal price line'},{k:'trendline',l:'↗ Trend',t:'Trend line'},{k:'rect',l:'▭ Box',t:'Rectangle'},{k:'text',l:'T Label',t:'Price label'}];
    draws.forEach(function(def){var b=btn(def.l,def.t,function(){setDrawMode(def.k);});b.dataset.draw=def.k;if(tvDrawMode===def.k)b.classList.add('active');tb.appendChild(b);});
    // Color picker
    var cw=document.createElement('span');cw.style.cssText='display:flex;align-items:center;gap:3px;';
    var cp=document.createElement('input');cp.type='color';cp.value=drawColor;cp.style.cssText='width:24px;height:22px;border:none;background:none;cursor:pointer;padding:0;';cp.title='Drawing color';cp.addEventListener('input',function(){drawColor=cp.value;});
    cw.appendChild(cp);tb.appendChild(cw);
    tb.appendChild(sep());
    tb.appendChild(btn('↩ Undo','Undo last drawing',doUndo));
    tb.appendChild(btn('✕ Clear','Clear all drawings',doClear,'danger'));
    var spacer=document.createElement('div');spacer.style.flex='1';tb.appendChild(spacer);
    // Auto-range
    var arBtn=btn(tvAutoRange?'⤢ AR ON':'⤢ AR OFF','Toggle auto-range',function(){tvAutoRange=!tvAutoRange;arBtn.textContent=tvAutoRange?'⤢ AR ON':'⤢ AR OFF';arBtn.classList.toggle('active',tvAutoRange);if(tvChart)fitAll();},tvAutoRange?'active':'');
    tb.appendChild(arBtn);
    tb.appendChild(btn('⟳ Reset','Fit all data',fitAll));
    var timerEl=document.createElement('span');timerEl.id='candle-close-timer';timerEl.className='candle-close-timer';timerEl.title='Time remaining until the current candle closes';timerEl.textContent='\u23F1 --:--';tb.appendChild(timerEl);
    startCandleCloseTimer();
    if(tvChart)tvChart.subscribeClick(handleClick);
  }
  function tvApplyAutoscale(){if(!tvCandle)return;var lp=tvAllLevelPrices.slice();tvCandle.applyOptions({autoscaleInfoProvider:function(original){var res=original();if(!res)return res;if(lp.length===0)return res;var pad=(res.priceRange.maxValue-res.priceRange.minValue)*0.05;var minV=Math.min.apply(null,[res.priceRange.minValue].concat(lp))-pad;var maxV=Math.max.apply(null,[res.priceRange.maxValue].concat(lp))+pad;return{priceRange:{minValue:minV,maxValue:maxV},margins:res.margins};}});}
  function fitAll(){if(!tvChart)return;setTimeout(function(){try{tvChart.timeScale().fitContent();tvChart.priceScale('right').applyOptions({autoScale:true});tvApplyAutoscale();if(tvRsiChart)tvRsiChart.priceScale('right').applyOptions({autoScale:true});if(tvMacdChart)tvMacdChart.priceScale('right').applyOptions({autoScale:true});}catch(e){}},50);}
    function ensureHistOverlay(){var c=document.getElementById('price-chart');if(!c)return null;var o=c.querySelector('.tv-historical-overlay');if(!o){o=document.createElement('div');o.className='tv-historical-overlay';c.appendChild(o);}return o;}
    function ensureHistTip(){var c=document.getElementById('price-chart');if(!c)return null;var t=c.querySelector('.tv-historical-tooltip');if(!t){t=document.createElement('div');t.className='tv-historical-tooltip';c.appendChild(t);}return t;}
    function fmtHistTime(ts){return new Date(ts*1000).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'America/New_York'})+' ET';}
    function histTipHtml(p){var dot=p.border_color||p.color||'#fff',name=p.kind==='expected-move'?p.label+' '+p.side:p.label+' '+p.side.charAt(0),value=p.kind==='expected-move'?p.value:'$'+Number(p.price).toFixed(2)+'  '+p.value;return '<div class="tt-row"><span class="tt-dot" style="background:'+dot+'"></span><div class="tt-main"><span class="tt-name">'+name+'</span><span class="tt-value">'+value+'</span></div></div>';}
    function posHistTip(t,e){var c=document.getElementById('price-chart');if(!t||!c||!e)return;var b=c.getBoundingClientRect();var l=Math.min(Math.max(8,e.clientX-b.left+12),Math.max(8,b.width-t.offsetWidth-8));var top=Math.min(Math.max(8,e.clientY-b.top+12),Math.max(8,b.height-t.offsetHeight-8));t.style.left=l+'px';t.style.top=top+'px';}
    function findHistHoverPoints(e){var c=document.getElementById('price-chart');if(!c||!tvHistoricalRenderedPoints.length)return[];var b=c.getBoundingClientRect(),cx=e.clientX-b.left,cy=e.clientY-b.top;return tvHistoricalRenderedPoints.filter(function(p){var dx=cx-p.x,dy=cy-p.y,r=Math.max(8,(p.size||8)/2+5);return(dx*dx+dy*dy)<=(r*r);}).sort(function(a,bp){var ad=(cx-a.x)*(cx-a.x)+(cy-a.y)*(cy-a.y),bd=(cx-bp.x)*(cx-bp.x)+(cy-bp.y)*(cy-bp.y);return ad-bd;});}
    function updateHistTip(e){var t=ensureHistTip();if(!t)return;var pts=findHistHoverPoints(e);if(!pts.length){t.style.display='none';return;}var topPts=pts.slice(0,5),anchorTime=topPts[0].time;t.innerHTML='<div class="tt-head"><span class="tt-badge">'+pts.length+' bubble'+(pts.length===1?'':'s')+'</span><div class="tt-time">'+fmtHistTime(anchorTime)+'</div></div><div class="tt-list">'+topPts.map(function(p){return histTipHtml(p);}).join('')+'</div>'+(pts.length>topPts.length?'<div class="tt-more">+'+(pts.length-topPts.length)+' more</div>':'');t.style.display='block';posHistTip(t,e);}
    function getVisibleHistoricalBubblePoints(){if(!tvHistoricalPoints.length)return[];var pts=tvHistoricalPoints;try{var range=tvChart.timeScale().getVisibleLogicalRange();if(range&&tvLastCandles.length){var li=Math.max(0,Math.floor(range.from)-2),ri=Math.min(tvLastCandles.length-1,Math.ceil(range.to)+2),left=tvLastCandles[li],right=tvLastCandles[ri];if(left&&right){var span=tvLastCandles.length>1?Math.max(60,tvLastCandles[1].time-tvLastCandles[0].time):60,minTime=left.time-(span*2),maxTime=right.time+(span*2);pts=tvHistoricalPoints.filter(function(p){return p.time>=minTime&&p.time<=maxTime;});}}}catch(e){}if(pts.length<=historicalBubbleMaxVisible)return pts;var priority=[],secondary=[];pts.forEach(function(p){if(p.kind==='expected-move'||p.rank===1)priority.push(p);else secondary.push(p);});if(priority.length>=historicalBubbleMaxVisible){var pStride=Math.ceil(priority.length/historicalBubbleMaxVisible);return priority.filter(function(_,i){return i%pStride===0;});}var slots=Math.max(0,historicalBubbleMaxVisible-priority.length);if(!secondary.length||slots===0)return priority;var stride=Math.ceil(secondary.length/slots);return priority.concat(secondary.filter(function(_,i){return i%stride===0;}));}
    function drawHistoricalBubbles(){var o=ensureHistOverlay(),t=ensureHistTip();if(!o||!tvChart||!tvCandle)return;tvHistoricalRenderedPoints=[];if(!tvHistoricalPoints.length){o.replaceChildren();o.style.display='none';if(t)t.style.display='none';return;}var points=getVisibleHistoricalBubblePoints();if(!points.length){o.replaceChildren();o.style.display='none';if(t)t.style.display='none';return;}var frag=document.createDocumentFragment(),visible=0;points.forEach(function(p){var x=tvChart.timeScale().timeToCoordinate(p.time),y=tvCandle.priceToCoordinate(p.price);if(x==null||y==null||Number.isNaN(x)||Number.isNaN(y))return;var b=document.createElement('div');b.className='tv-historical-bubble';b.style.left=x+'px';b.style.top=y+'px';b.style.width=(p.size||8)+'px';b.style.height=(p.size||8)+'px';b.style.background=p.color||'rgba(255,255,255,0.6)';b.style.border=(p.border_width||1)+'px solid '+(p.border_color||p.color||'#fff');frag.appendChild(b);tvHistoricalRenderedPoints.push(Object.assign({},p,{x:x,y:y}));visible++;});o.replaceChildren(frag);o.style.display=visible>0?'block':'none';}
    function scheduleHistoricalBubbleDraw(){if(historicalBubbleDrawPending)return;historicalBubbleDrawPending=true;requestAnimationFrame(function(){historicalBubbleDrawPending=false;drawHistoricalBubbles();});}

  // ── Main renderer ──────────────────────────────────────────────────────────
  var isFirstRender=true;
  function renderPriceChart(priceData){
    var candles=priceData.candles||[];
    var upColor=priceData.call_color||'#10B981',downColor=priceData.put_color||'#EF4444';
    popoutUpColor=upColor; popoutDownColor=downColor;
    popoutTimeframe=parseInt(priceData.timeframe)||1;
    lineStyleMap={dashed:LightweightCharts.LineStyle.Dashed,dotted:LightweightCharts.LineStyle.Dotted,large_dashed:LightweightCharts.LineStyle.LargeDashed};
    if(!tvChart){
      var el=document.getElementById('price-chart');
      tvChart=LightweightCharts.createChart(el,{autoSize:true,layout:{background:{color:'#1E1E1E'},textColor:'#CCCCCC',fontFamily:'Arial,sans-serif'},grid:{vertLines:{color:'#2A2A2A'},horzLines:{color:'#2A2A2A'}},crosshair:{mode:LightweightCharts.CrosshairMode.Normal,vertLine:{color:'#555',labelBackgroundColor:'#2D2D2D'},horzLine:{color:'#555',labelBackgroundColor:'#2D2D2D'}},rightPriceScale:{borderColor:'#333',scaleMargins:{top:0.04,bottom:0.15}},localization:{timeFormatter:function(time){var d=new Date(time*1000);return d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'America/New_York'});}},timeScale:{borderColor:'#333',timeVisible:true,secondsVisible:false,fixLeftEdge:false,fixRightEdge:false,tickMarkFormatter:function(time){var d=new Date(time*1000);return d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'America/New_York'});}},handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true},handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false}});
      tvCandle=tvChart.addCandlestickSeries({upColor:upColor,downColor:downColor,borderVisible:false,wickUpColor:upColor,wickDownColor:downColor});
      tvVol=tvChart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
      tvChart.priceScale('volume').applyOptions({scaleMargins:{top:0.88,bottom:0}});
      document.getElementById('chart-title').textContent=priceData.use_heikin_ashi?'Price Chart (Heikin-Ashi)':'Price Chart';
      buildToolbar(candles,upColor,downColor);
    ensureHistOverlay();ensureHistTip();tvChart.timeScale().subscribeVisibleLogicalRangeChange(function(){scheduleHistoricalBubbleDraw();});if(!historicalDomBound){historicalDomBound=true;el.addEventListener('wheel',function(){scheduleHistoricalBubbleDraw();},{passive:true});el.addEventListener('mouseup',function(){scheduleHistoricalBubbleDraw();});el.addEventListener('touchend',function(){scheduleHistoricalBubbleDraw();},{passive:true});el.addEventListener('mousemove',function(e){updateHistTip(e);});el.addEventListener('mouseleave',function(){var t=ensureHistTip();if(t)t.style.display='none';});}
      // ── OHLC hover tooltip ──────────────────────────────────────────────
      var _ptip=document.createElement('div');_ptip.className='tv-ohlc-tooltip';_ptip.id='tv-ohlc-tooltip';el.appendChild(_ptip);
      tvChart.subscribeCrosshairMove(function(param){
        var tip=document.getElementById('tv-ohlc-tooltip');if(!tip)return;
        if(!param||!param.time||!param.seriesData){tip.style.display='none';return;}
        var bar=param.seriesData.get(tvCandle);if(!bar){tip.style.display='none';return;}
        var d=new Date(param.time*1000);
        var ts=d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'America/New_York'})+' ET';
        var cls=bar.close>=bar.open?'tt-up':'tt-dn';
        var chg=bar.open!==0?((bar.close-bar.open)/bar.open*100).toFixed(2):'0.00';
        var fmt=function(v){return v!=null?v.toFixed(2):'--';};
        var fv=function(v){return v>=1e6?(v/1e6).toFixed(2)+'M':v>=1e3?(v/1e3).toFixed(0)+'K':(v||0).toString();};
        tip.innerHTML='<div class="tt-time">'+ts+'</div>'
          +'<span class="'+cls+'">O <b>'+fmt(bar.open)+'</b>  H <b>'+fmt(bar.high)+'</b>  L <b>'+fmt(bar.low)+'</b>  C <b>'+fmt(bar.close)+'</b>  '+(chg>=0?'+':'')+chg+'%</span>'
          +'<br><span style="color:#888">Vol <b>'+fv(bar.volume)+'</b></span>';
        tip.style.display='block';
      });
    } else {
      tvCandle.applyOptions({upColor:upColor,downColor:downColor,wickUpColor:upColor,wickDownColor:downColor});
    }
    tvCandle.setData(candles);
    tvVol.setData(priceData.volume||[]);
    tvLastCandles=candles;
        tvPriceLines.forEach(function(l){try{tvCandle.removePriceLine(l);}catch(e){}});tvPriceLines=[];tvAllLevelPrices=[];
        tvHistoricalPoints=priceData.historical_exposure_levels||[];
        tvHistoricalPoints.forEach(function(p){tvAllLevelPrices.push(p.price);});
        scheduleHistoricalBubbleDraw();
    tvApplyAutoscale();
    if(activeInds.size>0)applyIndicators(candles);
    if(isFirstRender||tvAutoRange){fitAll();isFirstRender=false;}
  }

  // ── Real-time quote / candle application ─────────────────────────────────
  // SSE is always 1-minute; we aggregate into the selected timeframe bucket.
  function tvBucketSec(){return (parseInt(popoutTimeframe,10)||1)*60;}
  function applyRealtimeQuote(last){
    if(!tvCandle||!tvLastCandles.length)return;
    var bucketSec=tvBucketSec();
    var nowSec=Math.floor(Date.now()/1000);
    var bucketStart=Math.floor(nowSec/bucketSec)*bucketSec;
    var lc=tvLastCandles[tvLastCandles.length-1];
    if(lc.time===bucketStart){
      var updated={time:lc.time,open:lc.open,high:Math.max(lc.high,last),low:Math.min(lc.low,last),close:last,volume:lc.volume||0};
      try{tvCandle.update(updated);}catch(e){}
      tvLastCandles[tvLastCandles.length-1]=updated;
    } else if(bucketStart>lc.time){
      var fresh={time:bucketStart,open:last,high:last,low:last,close:last,volume:0};
      try{tvCandle.update(fresh);}catch(e){}
      try{if(tvVol)tvVol.update({time:bucketStart,value:0,color:popoutUpColor});}catch(e){}
      tvLastCandles.push(fresh);
    }
  }
  function applyRealtimeCandle(candle){
    if(!tvCandle)return;
    var bucketSec=tvBucketSec();
    var bucketStart=Math.floor(candle.time/bucketSec)*bucketSec;
    var idx=tvLastCandles.findIndex(function(x){return x.time===bucketStart;});
    var merged;
    if(idx>=0){
      var b=tvLastCandles[idx];
      var bucketWasSeededFromQuote=(b.volume||0)===0;
      if(bucketWasSeededFromQuote){
        merged={time:b.time,open:candle.open,high:candle.high,low:candle.low,close:candle.close,volume:candle.volume||0};
      }else{
        merged={time:b.time,open:b.open,high:Math.max(b.high,candle.high),low:Math.min(b.low,candle.low),close:candle.close,volume:(b.volume||0)+(candle.volume||0)};
      }
      tvLastCandles[idx]=merged;
    }else{
      merged={time:bucketStart,open:candle.open,high:candle.high,low:candle.low,close:candle.close,volume:candle.volume||0};
      tvLastCandles.push(merged);tvLastCandles.sort(function(a,b){return a.time-b.time;});
    }
    try{tvCandle.update(merged);}catch(e){}
    try{if(tvVol)tvVol.update({time:merged.time,value:merged.volume||0,color:merged.close>=merged.open?popoutUpColor:popoutDownColor});}catch(e){}
    if(activeInds.size>0)applyIndicators(tvLastCandles);
  }

  // ── SSE price stream ───────────────────────────────────────────────────────
  var popoutEvtSource=null,popoutSseTicker=null;
  function connectPopoutStream(ticker){
    if(!ticker)return;
    var upper=ticker.toUpperCase();
    if(popoutEvtSource&&popoutSseTicker===upper&&popoutEvtSource.readyState!==2)return;
    if(popoutEvtSource){try{popoutEvtSource.close();}catch(e){}}
    popoutEvtSource=new EventSource('/price_stream/'+encodeURIComponent(upper));
    popoutSseTicker=upper;
    popoutEvtSource.onmessage=function(ev){
      try{
        var msg=JSON.parse(ev.data);
        if(msg.type==='quote'&&typeof msg.last==='number'){applyRealtimeQuote(msg.last);}
        else if(msg.type==='candle'&&msg.time){applyRealtimeCandle(msg);}
      }catch(e){}
    };
    popoutEvtSource.onerror=function(){console.debug('[Popout] SSE error – browser will retry.');};
  }

  // ── Initial candle load + settings helpers ────────────────────────────────
  var popoutFetching=false,popoutCurrentTicker=null;
  function getSettingsFromOpener(){
    try{
      var op=window.opener;if(!op||op.closed)return null;
      var d=op.document;
      function val(id){var el=d.getElementById(id);return el?el.value:null;}
      function chk(id){var el=d.getElementById(id);return el?el.checked:false;}
      var ticker=val('ticker');if(!ticker)return null;
      var levelsTypes=[];
      try{levelsTypes=Array.from(d.querySelectorAll('.levels-option input:checked')).map(function(cb){return cb.value;});}catch(e){}
      return{ticker:ticker,timeframe:val('timeframe')||'1',call_color:val('call_color')||'#00ff00',put_color:val('put_color')||'#ff0000',levels_types:levelsTypes,levels_count:parseInt(val('levels_count'))||3,use_heikin_ashi:chk('use_heikin_ashi'),strike_range:parseFloat(val('strike_range'))/100||0.1,highlight_max_level:chk('highlight_max_level'),max_level_color:val('max_level_color')||'#800080',coloring_mode:val('coloring_mode')||'Linear Intensity'};
    }catch(e){return null;}
  }
  function loadInitialData(){
    if(popoutFetching)return;
    var settings=getSettingsFromOpener();
    if(!settings||!settings.ticker)return;
    popoutFetching=true;
    fetch('/update_price',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)})
      .then(function(r){return r.json();})
      .then(function(data){
        if(!data.error&&data.price){
          renderPriceChart(typeof data.price==='string'?JSON.parse(data.price):data.price);
        }
        // Connect SSE after we have the initial candle history
        connectPopoutStream(settings.ticker);
      })
      .catch(function(e){console.warn('Popout initial load error:',e);connectPopoutStream(settings.ticker);})
      .finally(function(){popoutFetching=false;});
  }

  // ── Ticker-change watcher (lightweight DOM read only) ─────────────────────
  // Reconnects SSE and reloads candle history whenever the ticker changes.
  // Exposure levels are refreshed periodically since options data changes.
  var popoutExpLevelTimer=null;
  function tickerWatchLoop(){
    var settings=getSettingsFromOpener();
    var ticker=settings?settings.ticker:null;
    if(ticker&&ticker!==popoutCurrentTicker){
      popoutCurrentTicker=ticker;
      loadInitialData();
    }
  }
  // Refresh exposure levels every 60 s (options cache updated by main /update cycle)
  function refreshExposureLevels(){
    if(popoutFetching||!popoutCurrentTicker)return;
    var settings=getSettingsFromOpener();
    if(!settings)return;
    popoutFetching=true;
    fetch('/update_price',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)})
      .then(function(r){return r.json();})
      .then(function(data){
        if(!data.error&&data.price){
          var pd=typeof data.price==='string'?JSON.parse(data.price):data.price;
          // Only refresh price lines (exposure levels + expected moves), not candles
          if(tvCandle){
            tvPriceLines.forEach(function(l){try{tvCandle.removePriceLine(l);}catch(e){}});
            tvPriceLines=[];tvAllLevelPrices=[];
                        tvHistoricalPoints=pd.historical_exposure_levels||[];
                        tvHistoricalPoints.forEach(function(p){tvAllLevelPrices.push(p.price);});
                        scheduleHistoricalBubbleDraw();
            tvApplyAutoscale();
          }
        }
      })
      .catch(function(e){console.warn('Popout exposure refresh error:',e);})
      .finally(function(){popoutFetching=false;});
  }

  // Kick off: initial load then watch for ticker changes every 3 s
  setTimeout(function(){
    loadInitialData();
    setInterval(tickerWatchLoop,3000);
    setInterval(refreshExposureLevels,60000);
  },300);

  // Entry point kept for compatibility with pushDataToPopout
  window.updatePopoutChart=function(priceDataJSON){
    try{var priceData=typeof priceDataJSON==='string'?JSON.parse(priceDataJSON):priceDataJSON;if(!priceData||priceData.error)return;renderPriceChart(priceData);}catch(e){console.error('Popout price chart error:',e);}
  };
  window.addEventListener('resize',function(){if(tvChart&&tvAutoRange){try{tvChart.timeScale().fitContent();}catch(e){}}});
  window.addEventListener('beforeunload',function(){if(popoutEvtSource){try{popoutEvtSource.close();}catch(e){}}});
<\\/script></body></html>`);
            } else {
                popup.document.write(`<!DOCTYPE html>
<html><head><title>${displayName} - EzOptions</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"><\\/script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1E1E1E; overflow: hidden; position: relative; }
  #popout-logo { position: fixed; top: 6px; left: 10px; z-index: 100; font-family: Arial, sans-serif; font-size: 11px; font-weight: bold; color: #800080; opacity: 0.7; pointer-events: none; letter-spacing: 0.5px; }
  #popout-plot { width: 100vw; height: 100vh; }
  #popout-html { width: 100vw; height: 100vh; overflow: auto; background: #1E1E1E; color: white; font-family: Arial, sans-serif; }
</style></head><body>
<div id="popout-logo">EzDuz1t Options</div>
<div id="popout-plot"></div>
<div id="popout-html" style="display:none;"></div>
<script>
  let plotInited = false;
  window.updatePopoutChart = function(chartDataJSON, isHtml) {
    if (isHtml) {
      document.getElementById('popout-plot').style.display = 'none';
      const htmlDiv = document.getElementById('popout-html');
      htmlDiv.style.display = 'block';
      htmlDiv.innerHTML = chartDataJSON;
      return;
    }
    document.getElementById('popout-html').style.display = 'none';
    const plotDiv = document.getElementById('popout-plot');
    plotDiv.style.display = 'block';
    try {
      const chartData = JSON.parse(chartDataJSON);
      chartData.layout.autosize = true;
      chartData.layout.width = null;
      chartData.layout.height = null;
      chartData.layout.margin = { l: 60, r: 130, t: 60, b: 40 };
      const config = { responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d','select2d'], displaylogo: false, scrollZoom: true };
      if (plotInited) {
        Plotly.react('popout-plot', chartData.data, chartData.layout, config);
      } else {
        Plotly.newPlot('popout-plot', chartData.data, chartData.layout, config);
        plotInited = true;
      }
    } catch(e) { console.error('Popout chart error:', e); }
  };
  window.addEventListener('resize', function() {
    const el = document.getElementById('popout-plot');
    if (el && el.querySelector('.js-plotly-plot')) { try { Plotly.Plots.resize(el); } catch(e) {} }
  });
<\\/script></body></html>`);
            }
            popup.document.close();

            popoutWindows[chartId] = popup;

            // Push initial data after a small delay so the popup's DOM is ready
            setTimeout(() => { pushDataToPopout(chartId); }, 300);

            // Clean up reference when popup closes
            const checkClosed = setInterval(() => {
                if (popup.closed) {
                    clearInterval(checkClosed);
                    delete popoutWindows[chartId];
                }
            }, 1000);
        }

        function pushDataToPopout(chartId) {
            const popup = popoutWindows[chartId];
            if (!popup || popup.closed) { delete popoutWindows[chartId]; return; }
            if (typeof popup.updatePopoutChart !== 'function') return; // not ready yet

            // Determine the data key from chart id  (e.g. 'gamma-chart' -> 'gamma', 'price-chart' -> 'price')
            const dataKey = chartId.replace('-chart', '');
            // Price data is stored separately since it's fetched via /update_price
            const chartPayload = (dataKey === 'price') ? lastPriceData : lastData[dataKey];
            if (!chartPayload) return;

            const isHtml = (dataKey === 'large_trades');
            try {
                popup.updatePopoutChart(chartPayload, isHtml);
            } catch(e) {
                // popup may have navigated away or been closed
                console.warn('Could not push to popout:', e);
            }
        }

        function pushAllPopouts() {
            Object.keys(popoutWindows).forEach(chartId => pushDataToPopout(chartId));
        }

        function addPopoutButton(container) {
            if (!container || container.querySelector('.chart-popout-btn')) return;
            const btn = document.createElement('button');
            btn.className = 'chart-popout-btn';
            btn.innerHTML = popoutSvg;
            btn.title = 'Pop out chart to separate window';
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                openPopoutChart(container.id);
            });
            container.appendChild(btn);
        }

        // Clean up popout windows on page unload
        window.addEventListener('beforeunload', function() {
            Object.values(popoutWindows).forEach(w => { try { w.close(); } catch(e) {} });
        });
        // --- End pop-out support ---

        function showError(message) {
            const notification = document.getElementById('error-notification');
            const messageElement = document.getElementById('error-message');
            messageElement.textContent = message;
            notification.style.display = 'block';
            
            // Auto-hide after 10 seconds unless it's a persistent error
            setTimeout(hideError, 10000);
        }

        function hideError() {
            document.getElementById('error-notification').style.display = 'none';
        }
        
        // Update colors when color pickers change
        document.getElementById('call_color').addEventListener('change', function(e) {
            callColor = e.target.value;
            updateData();
        });
        
        document.getElementById('put_color').addEventListener('change', function(e) {
            putColor = e.target.value;
            updateData();
        });

        document.getElementById('max_level_color').addEventListener('change', function(e) {
            maxLevelColor = e.target.value;
            updateData();
        });

        document.getElementById('highlight_max_level').addEventListener('change', updateData);
        document.getElementById('max_level_mode').addEventListener('change', updateData);
        
        // Helper function to create rgba color with opacity
        function createRgbaColor(hexColor, opacity) {
            const r = parseInt(hexColor.slice(1, 3), 16);
            const g = parseInt(hexColor.slice(3, 5), 16);
            const b = parseInt(hexColor.slice(5, 7), 16);
            return `rgba(${r}, ${g}, ${b}, ${opacity})`;
        }
        
        // Update strike range value display
        document.getElementById('strike_range').addEventListener('input', function() {
            document.getElementById('strike_range_value').textContent = this.value + '%';
            updateData();
        });

        // EM range lock toggle state
        let emRangeLocked = false;

        function applyEmRange(em, triggerUpdate) {
            if (!em || em.upper_pct == null) return false;
            const emPct = Math.abs(em.upper_pct);
            const withWiggle = emPct + 0.5;
            const stepped = Math.round(withWiggle / 0.5) * 0.5;
            const clamped = Math.min(20, Math.max(0.5, stepped));
            const slider = document.getElementById('strike_range');
            if (parseFloat(slider.value) === clamped) return true; // no change needed
            slider.value = clamped;
            document.getElementById('strike_range_value').textContent = clamped + '%';
            if (triggerUpdate) updateData();
            return true;
        }

        function setEmRangeLocked(locked) {
            emRangeLocked = locked;
            const btn = document.getElementById('match_em_range');
            if (locked) {
                btn.style.background = '#1a4a1a';
                btn.style.color = '#00ff88';
                btn.style.borderColor = '#00aa55';
                btn.title = 'EM Range Lock ON — click to disable';
            } else {
                btn.style.background = '#2a2a2a';
                btn.style.color = '#888888';
                btn.style.borderColor = '#555555';
                btn.title = 'Toggle: auto-sync strike range to Expected Move (ATM straddle) + 0.5% wiggle room';
            }
        }

        // Match EM range button: toggle auto-sync of strike range to EM
        document.getElementById('match_em_range').addEventListener('click', function() {
            if (emRangeLocked) {
                setEmRangeLocked(false);
            } else {
                setEmRangeLocked(true);
                // Apply immediately if EM data is already available
                const em = lastData && lastData.price_info && lastData.price_info.expected_move_range;
                if (!applyEmRange(em, true)) {
                    alert('Expected Move data not yet available. Fetch data first.');
                    setEmRangeLocked(false);
                }
            }
        });

        // Coloring mode listeners
        document.getElementById('timeframe').addEventListener('change', function() {
            persistSelectedTimeframe(this.value);
            tvForceSessionFocus = true;
            updateData();
            startCandleCloseTimer();
        });
        document.getElementById('coloring_mode').addEventListener('change', updateData);
        document.getElementById('exposure_metric').addEventListener('change', updateData);
        document.getElementById('levels_count').addEventListener('input', updateData);
        document.getElementById('abs_gex_opacity').addEventListener('input', updateData);
        document.getElementById('top_oi_count').addEventListener('input', function() {
            normalizeTopOICountInput();
            updateData();
        });

        // Levels dropdown handlers
        function updateLevelsDisplay() {
            const checkedBoxes = document.querySelectorAll('.levels-option input[type="checkbox"]:checked');
            const levelsText = document.getElementById('levels-text');
            
            if (checkedBoxes.length === 0) {
                levelsText.textContent = 'None';
            } else if (checkedBoxes.length === 1) {
                levelsText.textContent = checkedBoxes[0].value;
            } else {
                levelsText.textContent = `${checkedBoxes.length} selected`;
            }
        }

        document.getElementById('levels-display').addEventListener('click', function(e) {
            e.stopPropagation();
            const options = document.getElementById('levels-options');
            options.classList.toggle('open');
        });
        
        // Add event listeners for level checkboxes
        document.querySelectorAll('.levels-option input[type="checkbox"]').forEach(checkbox => {
            checkbox.addEventListener('change', function() {
                updateLevelsDisplay();
                updateData();
            });
        });
        
        function updateData() {
            if (updateInProgress) {
                return; // Skip if an update is already in progress
            }
            
            updateInProgress = true;
            
            const ticker = document.getElementById('ticker').value;
            const isFirstLoad = tvLastTicker === null;
            const tickerChanged = !isFirstLoad && ticker.toUpperCase() !== tvLastTicker.toUpperCase();
            const shouldRefreshPriceLevels = isFirstLoad || tickerChanged;

            const selectedCheckboxes = document.querySelectorAll('.expiry-option input[type="checkbox"]:checked');
            const expiry = Array.from(selectedCheckboxes).map(checkbox => checkbox.value);
            const topOiCount = normalizeTopOICountInput();
            const topOiContextKey = buildTopOIContextKey(ticker, expiry, topOiCount);

            // Reset chart state when the ticker changes
            if (tickerChanged) {
                tvDrawStart = null;
                tvDrawingPreviewDef = null;
                tvSelectedDrawingId = null;
                // Reset symbol-scoped overlay caches
                _lastTopOI = null;
                _lastTopOIContextKey = '';
                _lastKeyLevels = null;
                _lastKeyLevels0dte = null;
                _lastSessionLevels = null;
                _lastSessionLevelsMeta = null;
                _lastStats0dte = null;
                clearTopOILines();
                clearKeyLevels();
                clearSessionLevels();
                // Reset zoom on the next render
                tvLastCandles = [];
                tvIndicatorCandles = [];
                tvCurrentDayStartTime = 0;
                tvForceSessionFocus = true;
                // Disconnect the price stream so it reconnects on the new ticker
                disconnectPriceStream();
            }
            tvLastTicker = ticker;
            
            // Ensure at least one expiry is selected
            if (expiry.length === 0) {
                console.warn('No expiry selected, skipping update');
                updateInProgress = false;
                return;
            }
            const showCalls = document.getElementById('show_calls').checked;
            const showPuts = document.getElementById('show_puts').checked;
            const showNet = document.getElementById('show_net').checked;
            const coloringMode = document.getElementById('coloring_mode').value;
            const levelsTypes = Array.from(document.querySelectorAll('.levels-option input:checked')).map(cb => cb.value);
            const levelsCount = parseInt(document.getElementById('levels_count').value);
            const useHeikinAshi = document.getElementById('use_heikin_ashi').checked;
            const horizontalBars = document.getElementById('horizontal_bars').checked;
            const showAbsGex = document.getElementById('show_abs_gex').checked;
            const absGexOpacity = parseInt(document.getElementById('abs_gex_opacity').value) / 100;
            const useRange = document.getElementById('use_range').checked;
            const exposureMetric = document.getElementById('exposure_metric').value;
            const deltaAdjusted = document.getElementById('delta_adjusted_exposures').checked;
            const calculateInNotional = document.getElementById('calculate_in_notional').checked;
            const strikeRange = parseFloat(document.getElementById('strike_range').value) / 100;
            const highlightMaxLevel = document.getElementById('highlight_max_level').checked;
            const maxLevelMode = document.getElementById('max_level_mode').value;
            const gateAlerts = !!(document.getElementById('gate_alerts') && document.getElementById('gate_alerts').checked);
            setAlertGateSetting(gateAlerts);
            
            // Get visible charts (server payload uses show_<id> keys for back-compat)
            const _vis = getChartVisibility();
            const visibleCharts = {};
            CHART_IDS.forEach(id => { visibleCharts['show_' + id] = _vis[id]; });

            // Common payload fields shared by both requests
            const sharedPayload = {
                ticker,
                timeframe: document.getElementById('timeframe').value,
                call_color: callColor,
                put_color: putColor,
                levels_types: levelsTypes,
                levels_count: levelsCount,
                use_heikin_ashi: useHeikinAshi,
                strike_range: strikeRange,
                highlight_max_level: highlightMaxLevel,
                max_level_color: maxLevelColor,
                coloring_mode: coloringMode,
                top_oi_count: topOiCount,
                ov_show_calls: !!document.getElementById('ov_show_calls').checked,
                ov_show_puts: !!document.getElementById('ov_show_puts').checked,
                ov_show_net: !!document.getElementById('ov_show_net').checked,
                ov_show_totals: !!document.getElementById('ov_show_totals').checked,
            };

            // Fetch price history: immediate on ticker/settings change, throttled to 30s otherwise.
            // Real-time candle ticks come from SSE (connectPriceStream), not from polling.
            if (visibleCharts.show_price) {
                fetchPriceHistory(tickerChanged || !tvLastCandles.length);
            }
            
            fetch('/update', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    ticker, 
                    expiry,
                    timeframe: document.getElementById('timeframe').value,
                    show_calls: showCalls,
                    show_puts: showPuts,
                    show_net: showNet,
                    coloring_mode: coloringMode,
                    levels_types: levelsTypes,
                    levels_count: levelsCount,
                    use_heikin_ashi: useHeikinAshi,
                    horizontal_bars: horizontalBars,
                    show_abs_gex: showAbsGex,
                    abs_gex_opacity: absGexOpacity,
                    use_range: useRange,
                    exposure_metric: exposureMetric,
                    delta_adjusted: deltaAdjusted,
                    calculate_in_notional: calculateInNotional,
                    strike_range: strikeRange,
                    gate_alerts: gateAlerts,
                    call_color: callColor,
                    put_color: putColor,
                    highlight_max_level: highlightMaxLevel,
                    max_level_color: maxLevelColor,
                    max_level_mode: maxLevelMode,
                    show_price: false,  // price is fetched independently via /update_price
                    ...visibleCharts
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    showError(data.error);
                    // Pause streaming on persistent error
                    if (isStreaming) {
                        toggleStreaming();
                    }
                    return;
                }
                
                // Only update if data has changed
                if (JSON.stringify(data) !== JSON.stringify(lastData)) {
                    lastData = data;  // Update before rendering so popout windows get fresh data
                    updateCharts(data, topOiContextKey);
                    updatePriceInfo(data.price_info);
                }
                // Options cache is now populated — refresh price levels immediately.
                // This fixes the delay where levels were missing right after a ticker change
                // because /update_price fired before the options chain was cached.
                // Gate on ticker-change / first-load only: running this every tick
                // forces a full setData rebuild at 1Hz, which flashes the candle and
                // level lines as the axis snaps back to the historical-only snapshot.
                if (shouldRefreshPriceLevels && isChartVisible('price')) {
                    _priceHistoryLastKey = ''; // force cache-miss so fetchPriceHistory re-fetches
                    fetchPriceHistory(true);
                }
            })
            .catch(error => {
                showError('Network Error: Could not connect to the server.');
                if (isStreaming) {
                    toggleStreaming();
                }
                console.error('Error fetching data:', error);
            })
            .finally(() => {
                updateInProgress = false;
            });
        }

        // ── TradingView Lightweight Charts price chart renderer ───────────────

        // ── Indicator math helpers ────────────────────────────────────────────
        function calcSMA(closes, period) {
            return closes.map((_, i) => {
                if (i < period - 1) return null;
                const slice = closes.slice(i - period + 1, i + 1);
                return slice.reduce((a, b) => a + b, 0) / period;
            });
        }
        function calcEMA(closes, period) {
            const k = 2 / (period + 1);
            const result = [];
            let ema = null;
            for (let i = 0; i < closes.length; i++) {
                if (i < period - 1) { result.push(null); continue; }
                if (ema === null) { ema = closes.slice(0, period).reduce((a,b)=>a+b,0)/period; }
                else              { ema = closes[i] * k + ema * (1 - k); }
                result.push(ema);
            }
            return result;
        }
        function calcVWAP(candles) {
            let cumPV = 0, cumVol = 0;
            return candles.map(c => {
                const typical = (c.high + c.low + c.close) / 3;
                cumPV  += typical * c.volume;
                cumVol += c.volume;
                return cumVol > 0 ? cumPV / cumVol : c.close;
            });
        }
        function calcBB(closes, period=20, mult=2) {
            const sma = calcSMA(closes, period);
            return sma.map((mid, i) => {
                if (mid === null) return { upper: null, mid: null, lower: null };
                const slice = closes.slice(Math.max(0, i - period + 1), i + 1);
                const variance = slice.reduce((a, b) => a + (b - mid) ** 2, 0) / slice.length;
                const sd = Math.sqrt(variance);
                return { upper: mid + mult * sd, mid, lower: mid - mult * sd };
            });
        }
        function calcRSI(closes, period=14) {
            const result = [];
            for (let i = 0; i < closes.length; i++) {
                if (i < period) { result.push(null); continue; }
                let gains = 0, losses = 0;
                for (let j = i - period + 1; j <= i; j++) {
                    const diff = closes[j] - closes[j-1];
                    if (diff > 0) gains  += diff;
                    else          losses -= diff;
                }
                const avgGain = gains  / period;
                const avgLoss = losses / period;
                const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
                result.push(100 - 100 / (1 + rs));
            }
            return result;
        }
        function calcMACD(closes, fast=12, slow=26, signal=9) {
            const emaFast   = calcEMA(closes, fast);
            const emaSlow   = calcEMA(closes, slow);
            const macdLine  = emaFast.map((v, i) => (v !== null && emaSlow[i] !== null) ? v - emaSlow[i] : null);
            const validMACD = macdLine.filter(v => v !== null);
            const sigLine   = [];
            let emaS = null;
            let validIdx = 0;
            const k = 2 / (signal + 1);
            for (let i = 0; i < macdLine.length; i++) {
                if (macdLine[i] === null) { sigLine.push(null); continue; }
                if (validIdx < signal - 1) { sigLine.push(null); validIdx++; continue; }
                if (emaS === null) {
                    const slice = macdLine.filter(v=>v!==null).slice(0, signal);
                    emaS = slice.reduce((a,b)=>a+b,0)/signal;
                } else {
                    emaS = macdLine[i] * k + emaS * (1 - k);
                }
                sigLine.push(emaS);
                validIdx++;
            }
            return { macd: macdLine, signal: sigLine,
                     histogram: macdLine.map((v,i) => (v!==null && sigLine[i]!==null) ? v-sigLine[i] : null) };
        }

        function calcATR(candles, period=14) {
            const result = [];
            for (let i = 0; i < candles.length; i++) {
                const tr = i === 0
                    ? candles[i].high - candles[i].low
                    : Math.max(
                        candles[i].high - candles[i].low,
                        Math.abs(candles[i].high - candles[i-1].close),
                        Math.abs(candles[i].low  - candles[i-1].close)
                      );
                if (i < period - 1) { result.push(null); continue; }
                if (result.length === 0 || result[result.length-1] === null) {
                    let sum = 0;
                    for (let j = i - period + 1; j <= i; j++) {
                        const t = j === 0
                            ? candles[j].high - candles[j].low
                            : Math.max(
                                candles[j].high - candles[j].low,
                                Math.abs(candles[j].high - candles[j-1].close),
                                Math.abs(candles[j].low  - candles[j-1].close)
                              );
                        sum += t;
                    }
                    result.push(sum / period);
                } else {
                    result.push((result[result.length-1] * (period - 1) + tr) / period);
                }
            }
            return result;
        }

        function getDefaultTVIndicatorPrefs() {
            return Object.fromEntries(
                Object.entries(DEFAULT_TV_INDICATOR_PREFS).map(([key, pref]) => [key, { ...pref }])
            );
        }

        function normalizeTVIndicatorColor(color, fallback) {
            const value = String(color || '').trim();
            return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
        }

        function normalizeTVIndicatorLineWidth(width, fallback = 1) {
            const value = Number(width);
            if (!Number.isFinite(value)) return fallback;
            return Math.max(1, Math.min(4, Math.round(value)));
        }

        function normalizeTVIndicatorLineStyle(style, fallback = 'solid') {
            const value = String(style || '').trim().toLowerCase();
            return ['solid', 'dashed', 'dotted'].includes(value) ? value : fallback;
        }

        function normalizeTVIndicatorPrefMap(prefs) {
            const defaults = getDefaultTVIndicatorPrefs();
            const source = prefs && typeof prefs === 'object' ? prefs : {};
            Object.keys(defaults).forEach(key => {
                const base = defaults[key];
                const next = source[key] && typeof source[key] === 'object' ? source[key] : {};
                defaults[key] = {
                    color: normalizeTVIndicatorColor(next.color, base.color),
                    lineWidth: normalizeTVIndicatorLineWidth(next.lineWidth, base.lineWidth),
                    lineStyle: normalizeTVIndicatorLineStyle(next.lineStyle, base.lineStyle),
                };
            });
            return defaults;
        }

        function getDefaultPriceLevelPrefs() {
            return normalizePriceLevelPrefMap(DEFAULT_PRICE_LEVEL_PREFS, { migrateLegacyDefaults: false });
        }

        function rgbToHexColor(color) {
            const value = String(color || '').trim();
            const match = value.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/i);
            if (!match) return '';
            const channels = match.slice(1, 4).map(v => Math.max(0, Math.min(255, Number(v) || 0)));
            return `#${channels.map(v => v.toString(16).padStart(2, '0')).join('').toUpperCase()}`;
        }

        function resolveCssColor(color) {
            const value = String(color || '').trim();
            if (!value) return '';
            const hex = value.match(/^#([0-9a-f]{6})$/i);
            if (hex) return `#${hex[1].toUpperCase()}`;
            const token = value.match(/^var\((--[a-z0-9-]+)\)$/i);
            const tokenName = token ? token[1] : (/^--[a-z0-9-]+$/i.test(value) ? value : '');
            if (tokenName) {
                try {
                    return resolveCssColor(getComputedStyle(document.documentElement).getPropertyValue(tokenName));
                } catch (e) {
                    return '';
                }
            }
            return rgbToHexColor(value);
        }

        function normalizePriceLevelColor(color, fallback) {
            return resolveCssColor(color) || resolveCssColor(fallback) || '#9CA3AF';
        }

        function normalizePriceLevelLineWidth(width, fallback = 1) {
            const value = Number(width);
            if (!Number.isFinite(value)) return fallback;
            return Math.max(1, Math.min(4, Math.round(value)));
        }

        function normalizePriceLevelLineStyle(style, fallback = 'solid') {
            const value = String(style || '').trim().toLowerCase();
            return ['solid', 'dashed', 'dotted', 'large-dashed'].includes(value) ? value : fallback;
        }

        function normalizePriceLevelPrefMap(prefs, options = {}) {
            const shouldMigrateLegacyDefaults = options.migrateLegacyDefaults !== false;
            const defaults = Object.fromEntries(
                Object.entries(DEFAULT_PRICE_LEVEL_PREFS).map(([key, pref]) => [key, {
                    ...pref,
                    color: normalizePriceLevelColor(pref.color, '#9CA3AF'),
                }])
            );
            const source = prefs && typeof prefs === 'object' ? prefs : {};
            Object.keys(defaults).forEach(key => {
                const base = defaults[key];
                const next = source[key] && typeof source[key] === 'object' ? source[key] : {};
                let nextColor = next.color;
                const legacyColor = LEGACY_SESSION_LEVEL_COLORS[key];
                if (shouldMigrateLegacyDefaults && nextColor !== undefined && legacyColor) {
                    const currentColor = normalizePriceLevelColor(nextColor, '');
                    const oldDefaultColor = normalizePriceLevelColor(legacyColor, '');
                    if (currentColor && oldDefaultColor && currentColor.toLowerCase() === oldDefaultColor.toLowerCase()) {
                        nextColor = base.color;
                    }
                }
                defaults[key] = {
                    label: base.label,
                    visible: next.visible === undefined ? base.visible : !!next.visible,
                    color: normalizePriceLevelColor(nextColor, base.color),
                    lineWidth: normalizePriceLevelLineWidth(next.lineWidth, base.lineWidth),
                    lineStyle: normalizePriceLevelLineStyle(next.lineStyle, base.lineStyle),
                };
            });
            return defaults;
        }

        function normalizeTVIndicatorActiveKeys(keys) {
            const validKeys = new Set(TV_INDICATOR_DEFS.map(def => def.key));
            const list = Array.isArray(keys) ? keys : [];
            return Array.from(new Set(list.filter(key => validKeys.has(key))));
        }

        function readPersistedTVIndicatorState() {
            try {
                const raw = localStorage.getItem(TV_INDICATOR_STATE_KEY);
                if (!raw) return null;
                const parsed = JSON.parse(raw);
                if (!parsed || typeof parsed !== 'object') return null;
                return {
                    active: normalizeTVIndicatorActiveKeys(parsed.active),
                    prefs: normalizeTVIndicatorPrefMap(parsed.prefs),
                };
            } catch (e) {
                return null;
            }
        }

        function persistTVIndicatorState() {
            try {
                localStorage.setItem(TV_INDICATOR_STATE_KEY, JSON.stringify({
                    active: normalizeTVIndicatorActiveKeys(Array.from(tvActiveInds)),
                    prefs: normalizeTVIndicatorPrefMap(tvIndicatorPrefs),
                }));
            } catch (e) {}
        }

        function hydrateTVIndicatorStateFromLocalStorage() {
            const persisted = readPersistedTVIndicatorState();
            if (!persisted) return false;
            tvActiveInds = new Set(persisted.active);
            tvIndicatorPrefs = persisted.prefs;
            return true;
        }

        function readPersistedPriceLevelPrefs() {
            try {
                const raw = localStorage.getItem(PRICE_LEVEL_PREFS_KEY);
                if (!raw) return null;
                return normalizePriceLevelPrefMap(JSON.parse(raw));
            } catch (e) {
                return null;
            }
        }

        function persistPriceLevelPrefs() {
            try {
                localStorage.setItem(PRICE_LEVEL_PREFS_KEY, JSON.stringify(normalizePriceLevelPrefMap(priceLevelPrefs)));
            } catch (e) {}
        }

        function hydratePriceLevelPrefsFromLocalStorage() {
            const persisted = readPersistedPriceLevelPrefs();
            if (!persisted) return false;
            priceLevelPrefs = persisted;
            return true;
        }

        function setTVIndicatorDataCache(key, lineSets) {
            if (!key) return;
            const normalized = Array.isArray(lineSets)
                ? lineSets
                    .map(points => Array.isArray(points)
                        ? points.filter(point => point && Number.isFinite(point.time) && Number.isFinite(point.value))
                        : [])
                    .filter(points => points.length)
                : [];
            if (normalized.length) tvIndicatorDataCache[key] = normalized;
            else delete tvIndicatorDataCache[key];
        }

        function getTVIndicatorNearestValue(points, targetTime) {
            if (!Array.isArray(points) || !points.length || !Number.isFinite(targetTime)) return null;
            let lo = 0;
            let hi = points.length - 1;
            while (lo <= hi) {
                const mid = Math.floor((lo + hi) / 2);
                const midTime = Number(points[mid] && points[mid].time);
                if (!Number.isFinite(midTime)) return null;
                if (midTime === targetTime) return Number(points[mid].value);
                if (midTime < targetTime) lo = mid + 1;
                else hi = mid - 1;
            }
            const candidates = [];
            if (lo < points.length) candidates.push(points[lo]);
            if (lo > 0) candidates.push(points[lo - 1]);
            if (!candidates.length) return null;
            const nearest = candidates.reduce((best, point) => {
                if (!best) return point;
                return Math.abs(Number(point.time) - targetTime) < Math.abs(Number(best.time) - targetTime) ? point : best;
            }, null);
            if (!nearest) return null;
            const tolerance = Math.max(getTVTimeframeSeconds() * 2, 60);
            return Math.abs(Number(nearest.time) - targetTime) <= tolerance ? Number(nearest.value) : null;
        }

        function focusTVIndicatorEditorKey(key) {
            const grid = document.getElementById('indicator-settings-grid');
            if (!grid || !key) return;
            grid.querySelectorAll('[data-indicator-row]').forEach(row => {
                row.classList.toggle('is-target', row.dataset.indicatorRow === key);
            });
            const row = grid.querySelector(`[data-indicator-row="${key}"]`);
            if (!row) return;
            row.scrollIntoView({ block: 'nearest' });
            const focusTarget = row.querySelector(`[data-indicator-color="${key}"]`)
                || row.querySelector(`[data-indicator-width="${key}"]`)
                || row.querySelector(`[data-indicator-style="${key}"]`)
                || row.querySelector(`[data-indicator-visible="${key}"]`);
            if (focusTarget && typeof focusTarget.focus === 'function') {
                try { focusTarget.focus({ preventScroll: true }); } catch (e) { focusTarget.focus(); }
            }
        }

        function findTVIndicatorHitKey(param) {
            if (!tvPriceChart || !tvCandleSeries || !param || !param.point) return '';
            const chartPoint = tvResolveChartPoint(param);
            if (!chartPoint || !Number.isFinite(chartPoint.price)) return '';
            const targetTime = Number(chartPoint.time);
            if (!Number.isFinite(targetTime)) return '';
            let bestKey = '';
            let bestDistance = Number.POSITIVE_INFINITY;
            EDITABLE_TV_INDICATOR_KEYS.forEach(key => {
                if (!tvActiveInds.has(key)) return;
                const lineSets = tvIndicatorDataCache[key];
                if (!Array.isArray(lineSets) || !lineSets.length) return;
                lineSets.forEach(points => {
                    const value = getTVIndicatorNearestValue(points, targetTime);
                    if (!Number.isFinite(value)) return;
                    const y = tvCandleSeries.priceToCoordinate(value);
                    if (!Number.isFinite(y)) return;
                    const distance = Math.abs(y - Number(param.point.y));
                    if (distance < bestDistance) {
                        bestDistance = distance;
                        bestKey = key;
                    }
                });
            });
            return bestDistance <= 10 ? bestKey : '';
        }

        function getTVIndicatorPref(key) {
            if (!tvIndicatorPrefs || typeof tvIndicatorPrefs !== 'object') {
                tvIndicatorPrefs = getDefaultTVIndicatorPrefs();
            }
            if (!tvIndicatorPrefs[key] && DEFAULT_TV_INDICATOR_PREFS[key]) {
                tvIndicatorPrefs[key] = { ...DEFAULT_TV_INDICATOR_PREFS[key] };
            }
            return tvIndicatorPrefs[key] || null;
        }

        function getPriceLevelPref(key) {
            if (!priceLevelPrefs || typeof priceLevelPrefs !== 'object') {
                priceLevelPrefs = getDefaultPriceLevelPrefs();
            }
            if (!priceLevelPrefs[key] && DEFAULT_PRICE_LEVEL_PREFS[key]) {
                priceLevelPrefs[key] = normalizePriceLevelPrefMap({ [key]: DEFAULT_PRICE_LEVEL_PREFS[key] }, { migrateLegacyDefaults: false })[key];
            }
            return priceLevelPrefs[key] || null;
        }

        function tvIndicatorLineStyleValue(style) {
            if (style === 'large-dashed') return LightweightCharts.LineStyle.LargeDashed;
            if (style === 'dashed') return LightweightCharts.LineStyle.Dashed;
            if (style === 'dotted') return LightweightCharts.LineStyle.Dotted;
            return LightweightCharts.LineStyle.Solid;
        }

        function tvIndicatorSeriesOptions(color, lineWidth, lineStyle, title='') {
            return {
                color,
                lineWidth,
                lineStyle: tvIndicatorLineStyleValue(lineStyle),
                priceScaleId: 'right',
                lastValueVisible: true,
                priceLineVisible: false,
                title,
            };
        }

        function colorWithAlpha(hex, alpha) {
            const match = String(hex || '').trim().match(/^#?([0-9a-f]{6})$/i);
            if (!match) return hex;
            const value = match[1];
            const r = parseInt(value.slice(0, 2), 16);
            const g = parseInt(value.slice(2, 4), 16);
            const b = parseInt(value.slice(4, 6), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }

        function getTVIndicatorSourceCandles() {
            if (Array.isArray(tvIndicatorCandles) && tvIndicatorCandles.length) return tvIndicatorCandles;
            if (Array.isArray(tvLastCandles) && tvLastCandles.length) return tvLastCandles;
            return [];
        }

        function syncTVIndicatorToggleButtons() {
            document.querySelectorAll('.tv-tb-btn[data-indicator-key]').forEach(btn => {
                btn.classList.toggle('active', tvActiveInds.has(btn.dataset.indicatorKey));
            });
        }

        function reapplyTVIndicators() {
            const candles = getTVIndicatorSourceCandles();
            if (candles.length) applyIndicators(candles, tvActiveInds);
            else updateIndicatorLegend();
        }

        function setTVIndicatorEnabled(key, enabled) {
            if (!key) return;
            if (enabled) tvActiveInds.add(key);
            else tvActiveInds.delete(key);
            persistTVIndicatorState();
            syncTVIndicatorToggleButtons();
            renderTVIndicatorEditor();
            reapplyTVIndicators();
            if (key === 'oi' && enabled) ensureTopOILoaded();
        }

        function updateTVIndicatorPref(key, patch) {
            const current = getTVIndicatorPref(key);
            if (!current) return;
            tvIndicatorPrefs[key] = normalizeTVIndicatorPrefMap({
                ...tvIndicatorPrefs,
                [key]: { ...current, ...(patch || {}) },
            })[key];
            persistTVIndicatorState();
            reapplyTVIndicators();
        }

        function updatePriceLevelPref(key, patch) {
            const current = getPriceLevelPref(key);
            if (!current) return;
            priceLevelPrefs[key] = normalizePriceLevelPrefMap({
                ...priceLevelPrefs,
                [key]: { ...current, ...(patch || {}) },
            })[key];
            persistPriceLevelPrefs();
            renderKeyLevels(getScopedKeyLevels());
            renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
            scheduleSessionLevelCloudDraw();
        }

        // ── Apply/remove indicators on existing chart ─────────────────────────
        function applyIndicators(candles, activeInds) {
            if (!tvPriceChart || !tvCandleSeries) return;
            const times  = candles.map(c => c.time);
            const closes = candles.map(c => c.close);

            // Remove deactivated indicators
            Object.keys(tvIndicatorSeries).forEach(key => {
                if (!activeInds.has(key)) {
                    const series = tvIndicatorSeries[key];
                    if (Array.isArray(series)) series.forEach(s => { try { tvPriceChart.removeSeries(s); } catch(e){} });
                    else                       { try { tvPriceChart.removeSeries(series); } catch(e){} }
                    delete tvIndicatorSeries[key];
                    delete tvIndicatorDataCache[key];
                }
            });

            function upsertLineSeries(slotKey, title, prefKey = slotKey, colorOverride = null) {
                const pref = getTVIndicatorPref(prefKey);
                const options = tvIndicatorSeriesOptions(
                    colorOverride || (pref && pref.color) || '#888888',
                    pref && pref.lineWidth,
                    pref && pref.lineStyle,
                    title
                );
                if (!tvIndicatorSeries[slotKey]) {
                    tvIndicatorSeries[slotKey] = tvPriceChart.addLineSeries(options);
                } else {
                    tvIndicatorSeries[slotKey].applyOptions(options);
                }
                return tvIndicatorSeries[slotKey];
            }

            // Helper: filter computed (time, value) pairs to today only.
            // candles may span multiple days (for warmup); we only plot current-day values.
            const dayStart = tvCurrentDayStartTime || 0;
            function todayOnly(pairs) {
                return pairs.filter(p => p !== null && p.time >= dayStart);
            }

            if (activeInds.has('sma20')) {
                const data = todayOnly(calcSMA(closes, 20).map((v,i) => v!==null ? {time:times[i], value:v} : null));
                upsertLineSeries('sma20', 'SMA20').setData(data);
                setTVIndicatorDataCache('sma20', [data]);
            }
            if (activeInds.has('sma50')) {
                const data = todayOnly(calcSMA(closes, 50).map((v,i) => v!==null ? {time:times[i], value:v} : null));
                upsertLineSeries('sma50', 'SMA50').setData(data);
                setTVIndicatorDataCache('sma50', [data]);
            }
            if (activeInds.has('sma200')) {
                const data = todayOnly(calcSMA(closes, 200).map((v,i) => v!==null ? {time:times[i], value:v} : null));
                upsertLineSeries('sma200', 'SMA200').setData(data);
                setTVIndicatorDataCache('sma200', [data]);
            }
            if (activeInds.has('ema9')) {
                const data = todayOnly(calcEMA(closes, 9).map((v,i) => v!==null ? {time:times[i], value:v} : null));
                upsertLineSeries('ema9', 'EMA9').setData(data);
                setTVIndicatorDataCache('ema9', [data]);
            }
            if (activeInds.has('ema21')) {
                const data = todayOnly(calcEMA(closes, 21).map((v,i) => v!==null ? {time:times[i], value:v} : null));
                upsertLineSeries('ema21', 'EMA21').setData(data);
                setTVIndicatorDataCache('ema21', [data]);
            }
            if (activeInds.has('vwap')) {
                // VWAP resets daily — always compute from today's candles only
                const todayCandles = dayStart > 0 ? candles.filter(c => c.time >= dayStart) : candles;
                const vwapVals = calcVWAP(todayCandles.map(c => ({
                    time: c.time, high: c.high, low: c.low, close: c.close, volume: c.volume || 0
                })));
                const data = vwapVals.map((v, i) => ({time: todayCandles[i].time, value: v}));
                upsertLineSeries('vwap', 'VWAP').setData(data);
                setTVIndicatorDataCache('vwap', [data]);
            }
            if (activeInds.has('bb')) {
                const pref = getTVIndicatorPref('bb');
                const upperLowerColor = colorWithAlpha(pref.color, 0.82);
                const midColor = colorWithAlpha(pref.color, 0.48);
                const bb = calcBB(closes);
                if (!tvIndicatorSeries['bb']) {
                    tvIndicatorSeries['bb'] = [
                        tvPriceChart.addLineSeries(tvIndicatorSeriesOptions(upperLowerColor, pref.lineWidth, pref.lineStyle, 'BB Upper')),
                        tvPriceChart.addLineSeries(tvIndicatorSeriesOptions(midColor, pref.lineWidth, pref.lineStyle, 'BB Mid')),
                        tvPriceChart.addLineSeries(tvIndicatorSeriesOptions(upperLowerColor, pref.lineWidth, pref.lineStyle, 'BB Lower')),
                    ];
                } else {
                    const [upperS, midS, lowerS] = tvIndicatorSeries['bb'];
                    upperS.applyOptions(tvIndicatorSeriesOptions(upperLowerColor, pref.lineWidth, pref.lineStyle, 'BB Upper'));
                    midS.applyOptions(tvIndicatorSeriesOptions(midColor, pref.lineWidth, pref.lineStyle, 'BB Mid'));
                    lowerS.applyOptions(tvIndicatorSeriesOptions(upperLowerColor, pref.lineWidth, pref.lineStyle, 'BB Lower'));
                }
                const [upperS, midS, lowerS] = tvIndicatorSeries['bb'];
                const upperData = todayOnly(bb.map((v,i) => v.upper!==null ? {time:times[i],value:v.upper} : null));
                const midData = todayOnly(bb.map((v,i) => v.mid!==null ? {time:times[i],value:v.mid} : null));
                const lowerData = todayOnly(bb.map((v,i) => v.lower!==null ? {time:times[i],value:v.lower} : null));
                upperS.setData(upperData);
                midS.setData(midData);
                lowerS.setData(lowerData);
                setTVIndicatorDataCache('bb', [upperData, midData, lowerData]);
            }
            if (activeInds.has('atr')) {
                const atrVals = calcATR(candles);
                const ema20   = calcEMA(closes, 20);
                const mult    = 1.5;
                if (!tvIndicatorSeries['atr']) {
                    tvIndicatorSeries['atr'] = [
                        tvPriceChart.addLineSeries(tvIndicatorSeriesOptions('rgba(255,152,0,0.8)', 1, 'solid', 'ATR Upper')),
                        tvPriceChart.addLineSeries(tvIndicatorSeriesOptions('rgba(255,152,0,0.8)', 1, 'solid', 'ATR Lower')),
                    ];
                }
                const [atrUpper, atrLower] = tvIndicatorSeries['atr'];
                atrUpper.setData(todayOnly(ema20.map((v,i) => (v!==null && atrVals[i]!==null) ? {time:times[i], value:v + mult*atrVals[i]} : null)));
                atrLower.setData(todayOnly(ema20.map((v,i) => (v!==null && atrVals[i]!==null) ? {time:times[i], value:v - mult*atrVals[i]} : null)));
            }

            // RSI and MACD sub-panes: compute with full history but display today only
            const todayCandles = dayStart > 0 ? candles.filter(c => c.time >= dayStart) : candles;
            if (activeInds.has('rsi')) applyRsiPane(candles, todayCandles);
            else                       destroyRsiPane();
            if (activeInds.has('macd')) applyMacdPane(candles, todayCandles);
            else                        destroyMacdPane();

            renderTopOI(_lastTopOI);

            // Update legend overlay
            updateIndicatorLegend();
        }

        // ── Indicator legend ─────────────────────────────────────────────────
        function updateIndicatorLegend() {
            const container = document.getElementById('price-chart');
            if (!container) return;
            let legend = container.querySelector('.tv-indicator-legend');
            if (!legend) {
                legend = document.createElement('div');
                legend.className = 'tv-indicator-legend';
                container.appendChild(legend);
            }
            const labels = {
                sma20:'SMA20', sma50:'SMA50', sma200:'SMA200',
                ema9:'EMA9', ema21:'EMA21', vwap:'VWAP', bb:'BB(20,2)',
                rsi:'RSI14', macd:'MACD', atr:'ATR Bands'
            };
            const fallbackColors = {
                rsi:'#e91e63',
                macd:'#2196f3',
                atr:'rgba(255,152,0,0.8)'
            };
            const legendKeys = TV_INDICATOR_DEFS.map(def => def.key).filter(key => tvIndicatorSeries[key]);
            legend.innerHTML = legendKeys.map(k => `
                <div class="tv-legend-item">
                    <div class="tv-legend-swatch" style="background:${(getTVIndicatorPref(k) && getTVIndicatorPref(k).color) || fallbackColors[k] || '#888'}"></div>
                    ${labels[k]||k}
                </div>`).join('');
        }

        function renderTVIndicatorEditor() {
            const grid = document.getElementById('indicator-settings-grid');
            if (!grid) return;
            const widthOptions = [1, 2, 3, 4].map(value =>
                `<option value="${value}">${value}px</option>`
            ).join('');
            const styleOptions = [
                ['solid', 'Solid'],
                ['dashed', 'Dashed'],
                ['dotted', 'Dotted'],
            ].map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
            grid.innerHTML = EDITABLE_TV_INDICATOR_KEYS.map(key => {
                const def = TV_INDICATOR_DEFS.find(item => item.key === key);
                const pref = getTVIndicatorPref(key);
                return (
                    `<div class="indicator-modal-row${tvIndicatorEditorTargetKey === key ? ' is-target' : ''}" data-indicator-row="${key}">` +
                        `<div class="indicator-modal-name">` +
                            `<span class="indicator-modal-swatch" style="background:${pref.color}"></span>` +
                            `<label for="indicator-visible-${key}">${def ? def.label : key}</label>` +
                        `</div>` +
                        `<div class="indicator-modal-toggle">` +
                            `<input type="checkbox" id="indicator-visible-${key}" data-indicator-visible="${key}" ${tvActiveInds.has(key) ? 'checked' : ''}>` +
                        `</div>` +
                        `<input type="color" value="${pref.color}" data-indicator-color="${key}" aria-label="${def ? def.label : key} color">` +
                        `<select data-indicator-width="${key}" aria-label="${def ? def.label : key} line width">${widthOptions}</select>` +
                        `<select data-indicator-style="${key}" aria-label="${def ? def.label : key} line style">${styleOptions}</select>` +
                    `</div>`
                );
            }).join('');
            EDITABLE_TV_INDICATOR_KEYS.forEach(key => {
                const pref = getTVIndicatorPref(key);
                const widthEl = grid.querySelector(`[data-indicator-width="${key}"]`);
                const styleEl = grid.querySelector(`[data-indicator-style="${key}"]`);
                if (widthEl) widthEl.value = String(pref.lineWidth);
                if (styleEl) styleEl.value = pref.lineStyle;
            });
            grid.querySelectorAll('[data-indicator-visible]').forEach(input => {
                input.addEventListener('change', () => setTVIndicatorEnabled(input.dataset.indicatorVisible, input.checked));
            });
            grid.querySelectorAll('[data-indicator-color]').forEach(input => {
                input.addEventListener('input', () => {
                    const row = input.closest('[data-indicator-row]');
                    const swatch = row && row.querySelector('.indicator-modal-swatch');
                    if (swatch) swatch.style.background = input.value;
                    updateTVIndicatorPref(input.dataset.indicatorColor, { color: input.value });
                });
            });
            grid.querySelectorAll('[data-indicator-width]').forEach(select => {
                select.addEventListener('change', () => updateTVIndicatorPref(select.dataset.indicatorWidth, { lineWidth: Number(select.value) }));
            });
            grid.querySelectorAll('[data-indicator-style]').forEach(select => {
                select.addEventListener('change', () => updateTVIndicatorPref(select.dataset.indicatorStyle, { lineStyle: select.value }));
            });
            if (tvIndicatorEditorTargetKey) {
                requestAnimationFrame(() => focusTVIndicatorEditorKey(tvIndicatorEditorTargetKey));
            }
        }

        function openTVIndicatorEditor(targetKey = '') {
            const modal = document.getElementById('indicator-settings-modal');
            if (!modal) return;
            tvIndicatorEditorTargetKey = EDITABLE_TV_INDICATOR_KEYS.includes(targetKey) ? targetKey : '';
            renderTVIndicatorEditor();
            if (modal.open) {
                if (tvIndicatorEditorTargetKey) focusTVIndicatorEditorKey(tvIndicatorEditorTargetKey);
                return;
            }
            if (modal.showModal) modal.showModal();
            else modal.setAttribute('open', '');
            if (tvIndicatorEditorTargetKey) {
                requestAnimationFrame(() => focusTVIndicatorEditorKey(tvIndicatorEditorTargetKey));
            }
        }

        function renderPriceLevelEditor() {
            const grid = document.getElementById('price-level-settings-grid');
            if (!grid) return;
            const widthOptions = [1, 2, 3, 4].map(value =>
                `<option value="${value}">${value}px</option>`
            ).join('');
            const styleOptions = [
                ['solid', 'Solid'],
                ['dashed', 'Dashed'],
                ['large-dashed', 'Long Dash'],
                ['dotted', 'Dotted'],
            ].map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
            grid.innerHTML = PRICE_LEVEL_GROUPS.map(group => {
                const rows = group.keys.map(key => {
                    const pref = getPriceLevelPref(key);
                    if (!pref) return '';
                    return (
                        `<div class="indicator-modal-row" data-price-level-row="${key}">` +
                            `<div class="indicator-modal-name">` +
                                `<span class="indicator-modal-swatch" style="background:${pref.color}"></span>` +
                                `<label for="price-level-visible-${key}">${pref.label}</label>` +
                            `</div>` +
                            `<div class="indicator-modal-toggle">` +
                                `<input type="checkbox" id="price-level-visible-${key}" data-price-level-visible="${key}" ${pref.visible ? 'checked' : ''}>` +
                            `</div>` +
                            `<input type="color" value="${pref.color}" data-price-level-color="${key}" aria-label="${pref.label} color">` +
                            `<select data-price-level-width="${key}" aria-label="${pref.label} line width">${widthOptions}</select>` +
                            `<select data-price-level-style="${key}" aria-label="${pref.label} line style">${styleOptions}</select>` +
                        `</div>`
                    );
                }).join('');
                return `<div class="price-level-modal-sep">${group.label}</div>${rows}`;
            }).join('');
            Object.keys(DEFAULT_PRICE_LEVEL_PREFS).forEach(key => {
                const pref = getPriceLevelPref(key);
                const widthEl = grid.querySelector(`[data-price-level-width="${key}"]`);
                const styleEl = grid.querySelector(`[data-price-level-style="${key}"]`);
                if (widthEl) widthEl.value = String(pref.lineWidth);
                if (styleEl) styleEl.value = pref.lineStyle;
            });
            grid.querySelectorAll('[data-price-level-visible]').forEach(input => {
                input.addEventListener('change', () => updatePriceLevelPref(input.dataset.priceLevelVisible, { visible: input.checked }));
            });
            grid.querySelectorAll('[data-price-level-color]').forEach(input => {
                input.addEventListener('input', () => {
                    const row = input.closest('[data-price-level-row]');
                    const swatch = row && row.querySelector('.indicator-modal-swatch');
                    if (swatch) swatch.style.background = input.value;
                    updatePriceLevelPref(input.dataset.priceLevelColor, { color: input.value });
                });
            });
            grid.querySelectorAll('[data-price-level-width]').forEach(select => {
                select.addEventListener('change', () => updatePriceLevelPref(select.dataset.priceLevelWidth, { lineWidth: Number(select.value) }));
            });
            grid.querySelectorAll('[data-price-level-style]').forEach(select => {
                select.addEventListener('change', () => updatePriceLevelPref(select.dataset.priceLevelStyle, { lineStyle: select.value }));
            });
        }

        function openPriceLevelEditor() {
            const modal = document.getElementById('price-level-settings-modal');
            if (!modal) return;
            renderPriceLevelEditor();
            if (modal.open) return;
            if (modal.showModal) modal.showModal();
            else modal.setAttribute('open', '');
        }

        // ── Sub-pane chart helper functions ──────────────────────────────────
        function createSubPaneChart(element, height) {
            if (!element) return null;
            return LightweightCharts.createChart(element, {
                autoSize: true,
                height: height,
                layout: { background: { color: '#1E1E1E' }, textColor: '#CCCCCC', fontFamily: 'Arial, sans-serif' },
                grid: { vertLines: { color: '#2A2A2A' }, horzLines: { color: '#2A2A2A' } },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                    vertLine: { color: '#555555', labelBackgroundColor: '#2D2D2D' },
                    horzLine: { color: '#555555', labelBackgroundColor: '#2D2D2D' },
                },
                rightPriceScale: { borderColor: '#333333', scaleMargins: { top: 0.1, bottom: 0.1 } },
                timeScale: {
                    borderColor: '#333333', timeVisible: false, secondsVisible: false,
                    fixLeftEdge: true, fixRightEdge: false,
                },
                handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
                handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
            });
        }

        function setupTimeScaleSync() {
            // Remove old subscriptions first
            tvSyncHandlers.forEach(({chart, handler}) => {
                try { chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler); } catch(e){}
            });
            tvSyncHandlers = [];
            const allCharts = [tvPriceChart, tvRsiChart, tvMacdChart].filter(Boolean);
            if (allCharts.length < 2) return;
            allCharts.forEach(srcChart => {
                const others = allCharts.filter(c => c !== srcChart);
                const handler = (range) => {
                    if (tvSyncingTimeScale || !range) return;
                    tvSyncingTimeScale = true;
                    others.forEach(c => { try { c.timeScale().setVisibleLogicalRange(range); } catch(e){} });
                    tvSyncingTimeScale = false;
                };
                try { srcChart.timeScale().subscribeVisibleLogicalRangeChange(handler); } catch(e){}
                tvSyncHandlers.push({ chart: srcChart, handler });
            });
            // Immediately match current main chart range
            if (tvPriceChart) {
                try {
                    const range = tvPriceChart.timeScale().getVisibleLogicalRange();
                    if (range) [tvRsiChart, tvMacdChart].filter(Boolean).forEach(c => {
                        try { c.timeScale().setVisibleLogicalRange(range); } catch(e){}
                    });
                } catch(e){}
            }
        }

        function applyRsiPane(allCandles, todayCandles) {
            const pane = document.getElementById('rsi-pane');
            if (!pane) return;
            pane.style.display = 'block';
            // Compute RSI using full history for warmup, then filter to today for display
            const allTimes   = allCandles.map(c => c.time);
            const rsiVals    = calcRSI(allCandles.map(c => c.close));
            const dayStart   = tvCurrentDayStartTime || 0;
            const rsiData    = rsiVals
                .map((v,i) => v!==null ? {time:allTimes[i],value:v} : null)
                .filter(p => p !== null && p.time >= dayStart);
            if (!tvRsiChart) {
                const chartEl = document.getElementById('rsi-chart');
                if (!chartEl) return;
                tvRsiChart = createSubPaneChart(chartEl, 110);
                tvRsiSeries = tvRsiChart.addLineSeries({
                    color: '#e91e63', lineWidth: 1.5,
                    lastValueVisible: true, priceLineVisible: false, title: 'RSI14'
                });
                tvRsiSeries.createPriceLine({ price: 70, color: 'rgba(255,100,100,0.7)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '70' });
                tvRsiSeries.createPriceLine({ price: 50, color: 'rgba(150,150,150,0.4)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: false, title: '' });
                tvRsiSeries.createPriceLine({ price: 30, color: 'rgba(100,200,100,0.7)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '30' });
            }
            if (rsiData.length) tvRsiSeries.setData(rsiData);
            setupTimeScaleSync();
        }

        function destroyRsiPane() {
            const pane = document.getElementById('rsi-pane');
            if (pane) pane.style.display = 'none';
            if (tvRsiChart) {
                tvSyncHandlers = tvSyncHandlers.filter(h => h.chart !== tvRsiChart);
                try { tvRsiChart.remove(); } catch(e){}
                tvRsiChart = null; tvRsiSeries = null;
            }
        }

        function applyMacdPane(allCandles, todayCandles) {
            const pane = document.getElementById('macd-pane');
            if (!pane) return;
            pane.style.display = 'block';
            // Compute MACD using full history for warmup, then filter to today for display
            const allTimes = allCandles.map(c => c.time);
            const macdData = calcMACD(allCandles.map(c => c.close));
            const dayStart = tvCurrentDayStartTime || 0;
            function todayOnly(pairs) { return pairs.filter(p => p !== null && p.time >= dayStart); }
            const histData = todayOnly(macdData.histogram.map((v,i) => v!==null ? {time:allTimes[i],value:v,color:v>=0?'rgba(76,175,80,0.8)':'rgba(244,67,54,0.8)'} : null));
            const lineData = todayOnly(macdData.macd.map((v,i)    => v!==null ? {time:allTimes[i],value:v} : null));
            const sigData  = todayOnly(macdData.signal.map((v,i)  => v!==null ? {time:allTimes[i],value:v} : null));
            if (!tvMacdChart) {
                const chartEl = document.getElementById('macd-chart');
                if (!chartEl) return;
                tvMacdChart = createSubPaneChart(chartEl, 120);
                tvMacdSeries.hist   = tvMacdChart.addHistogramSeries({ lastValueVisible: false, priceLineVisible: false });
                tvMacdSeries.line   = tvMacdChart.addLineSeries({ color: '#2196f3', lineWidth: 1.5, lastValueVisible: true, priceLineVisible: false, title: 'MACD' });
                tvMacdSeries.signal = tvMacdChart.addLineSeries({ color: '#ff9800', lineWidth: 1,   lastValueVisible: true, priceLineVisible: false, title: 'Signal' });
                tvMacdSeries.line.createPriceLine({ price: 0, color: 'rgba(150,150,150,0.4)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: false, title: '' });
            }
            if (histData.length) tvMacdSeries.hist.setData(histData);
            if (lineData.length) tvMacdSeries.line.setData(lineData);
            if (sigData.length)  tvMacdSeries.signal.setData(sigData);
            setupTimeScaleSync();
        }

        function destroyMacdPane() {
            const pane = document.getElementById('macd-pane');
            if (pane) pane.style.display = 'none';
            if (tvMacdChart) {
                tvSyncHandlers = tvSyncHandlers.filter(h => h.chart !== tvMacdChart);
                try { tvMacdChart.remove(); } catch(e){}
                tvMacdChart = null; tvMacdSeries = {};
            }
        }

        // ── Drawing tools ─────────────────────────────────────────────────────
        function getCurrentTVDrawingScopeKey() {
            const parts = getCurrentTVDrawingScopeParts();
            return parts.ticker ? `${parts.ticker}::${parts.heikin}` : '';
        }

        function getCurrentTVDrawingScopeParts() {
            const tickerEl = document.getElementById('ticker');
            const timeframeEl = document.getElementById('timeframe');
            const haEl = document.getElementById('use_heikin_ashi');
            const ticker = tickerEl ? (tickerEl.value || '').trim().toUpperCase() : '';
            const timeframe = timeframeEl ? String(timeframeEl.value || '1') : '1';
            const heikin = haEl && haEl.checked ? 'ha' : 'candles';
            return { ticker, timeframe, heikin };
        }

        function getLegacyTVDrawingScopeKey() {
            const parts = getCurrentTVDrawingScopeParts();
            return parts.ticker ? `${parts.ticker}::${parts.timeframe}::${parts.heikin}` : '';
        }

        function findLegacyTVDrawingScopeKey(store, parts) {
            if (!store || !parts || !parts.ticker) return '';
            const prefix = `${parts.ticker}::`;
            const suffix = `::${parts.heikin}`;
            return Object.keys(store).find(key =>
                key.startsWith(prefix)
                && key.endsWith(suffix)
                && Array.isArray(store[key])
            ) || '';
        }

        function loadTVDrawingStore() {
            try {
                const parsed = JSON.parse(localStorage.getItem(TV_DRAWING_STORE_KEY) || '{}');
                return parsed && typeof parsed === 'object' ? parsed : {};
            } catch (e) {
                return {};
            }
        }

        function saveTVDrawingStore(store) {
            try {
                localStorage.setItem(TV_DRAWING_STORE_KEY, JSON.stringify(store || {}));
            } catch (e) {}
        }

        function createTVDrawingId() {
            tvDrawingIdCounter += 1;
            return `tvd-${Date.now().toString(36)}-${tvDrawingIdCounter.toString(36)}`;
        }

        function loadTVDrawingToolPrefs() {
            try {
                const parsed = JSON.parse(localStorage.getItem(TV_DRAWING_TOOL_PREFS_KEY) || '{}');
                return parsed && typeof parsed === 'object' ? parsed : {};
            } catch (e) {
                return {};
            }
        }

        function saveTVDrawingToolPrefs(prefs) {
            try {
                localStorage.setItem(TV_DRAWING_TOOL_PREFS_KEY, JSON.stringify(prefs || {}));
            } catch (e) {}
        }

        function normalizeTVHLinePresetKey(value) {
            const key = String(value || '').trim().toLowerCase();
            return Object.prototype.hasOwnProperty.call(TV_HLINE_PRESETS, key) ? key : 'custom';
        }

        function getTVHLinePreset(key) {
            return TV_HLINE_PRESETS[normalizeTVHLinePresetKey(key)] || TV_HLINE_PRESETS.custom;
        }

        function getActiveTVHLinePresetKey() {
            const prefs = loadTVDrawingToolPrefs();
            return normalizeTVHLinePresetKey(prefs.hlinePreset);
        }

        function getTVChannelAxisSnapEnabled() {
            const prefs = loadTVDrawingToolPrefs();
            return prefs.channelAxisSnap === true;
        }

        function setActiveTVHLinePresetKey(nextKey, options = {}) {
            const presetKey = normalizeTVHLinePresetKey(nextKey);
            const prefs = loadTVDrawingToolPrefs();
            prefs.hlinePreset = presetKey;
            saveTVDrawingToolPrefs(prefs);
            if (options.syncToolbar !== false) {
                syncTVHLineToolbarPreset();
            }
            return presetKey;
        }

        function setTVChannelAxisSnapEnabled(enabled, options = {}) {
            const prefs = loadTVDrawingToolPrefs();
            prefs.channelAxisSnap = !!enabled;
            saveTVDrawingToolPrefs(prefs);
            if (options.syncToolbar !== false) {
                syncTVChannelSnapToolbarButton();
            }
            return prefs.channelAxisSnap;
        }

        function getTVHLinePresetColor(presetKey, fallback = '#FFD700') {
            const preset = getTVHLinePreset(presetKey);
            return preset.color || fallback;
        }

        function normalizeTVDrawingMidlineVisible(value) {
            return value !== false;
        }

        function normalizeTVDrawingExtendRight(value) {
            return value === true;
        }

        function getTVDrawingColorFallback(mode = tvDrawMode) {
            const colorInput = document.getElementById('tv-draw-color');
            const fallback = colorInput ? colorInput.value : '#FFD700';
            if (mode === 'hline') {
                return getTVHLinePresetColor(getActiveTVHLinePresetKey(), fallback);
            }
            return fallback;
        }

        function getTVHLinePresetToggleMarkup(presetKey) {
            const preset = getTVHLinePreset(presetKey);
            const swatchColor = preset.color || getTVDrawingColorFallback('custom');
            return '<span class="tv-draw-pill-swatch" style="background:' + swatchColor + '"></span><span>▾</span>';
        }

        function syncTVHLineToolbarPreset() {
            const button = document.getElementById('tv-hline-draw-button');
            const toggle = document.getElementById('tv-hline-preset-toggle');
            const presetKey = getActiveTVHLinePresetKey();
            const preset = getTVHLinePreset(presetKey);
            if (button) {
                button.dataset.hlinePreset = presetKey;
                button.title = `Draw ${preset.label} H-Line`;
            }
            if (toggle) {
                toggle.innerHTML = getTVHLinePresetToggleMarkup(presetKey);
                toggle.title = `Choose H-Line preset (current: ${preset.label})`;
            }
            document.querySelectorAll('.tv-draw-menu-item[data-hline-preset]').forEach(node => {
                const nodePreset = getTVHLinePreset(node.dataset.hlinePreset);
                node.innerHTML =
                    '<span class="tv-draw-pill-swatch" style="background:' + (nodePreset.color || getTVDrawingColorFallback('custom')) + '"></span>' +
                    nodePreset.label;
                node.classList.toggle('active', node.dataset.hlinePreset === presetKey);
            });
        }

        function syncTVChannelSnapToolbarButton() {
            const button = document.getElementById('tv-channel-snap-toggle');
            if (!button) return;
            const enabled = getTVChannelAxisSnapEnabled();
            button.classList.toggle('active', enabled);
            button.textContent = enabled ? '🔒 HV' : '🔓 HV';
            button.title = enabled
                ? 'Channel base line snaps horizontal/vertical while placing the second point'
                : 'Turn on horizontal/vertical snapping for the first channel segment';
        }

        function applyTVHLinePresetToDef(def, presetKey, options = {}) {
            if (!def || def.type !== 'hline') return def;
            const fallback = options.fallbackColor || def.color || getTVDrawingColorFallback('custom');
            const normalizedPreset = normalizeTVHLinePresetKey(presetKey);
            def.preset = normalizedPreset;
            def.color = getTVHLinePresetColor(normalizedPreset, fallback);
            return def;
        }

        function clampTVDrawingLineWidth(width) {
            const value = Number(width);
            if (!Number.isFinite(value)) return 2;
            return Math.max(1, Math.min(5, Math.round(value)));
        }

        function normalizeTVDrawingStyle(style) {
            return style === 'dashed' || style === 'dotted' ? style : 'solid';
        }

        function normalizeTVDrawingLogical(value) {
            const logical = Number(value);
            return Number.isFinite(logical) ? logical : null;
        }

        function normalizeTVDrawingLabelPosition(type, position) {
            const value = String(position || '').trim().toLowerCase();
            if (type === 'trendline' || type === 'channel') {
                return ['auto', 'start', 'middle', 'end'].includes(value) ? value : 'auto';
            }
            if (type === 'rect') {
                return ['auto', 'top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'].includes(value) ? value : 'auto';
            }
            return '';
        }

        function normalizeTVDrawingDef(def) {
            if (!def || typeof def !== 'object' || !def.type) return null;
            const normalized = { ...def };
            normalized.id = normalized.id || createTVDrawingId();
            normalized.color = normalized.color || '#FFD700';
            normalized.lineWidth = clampTVDrawingLineWidth(normalized.lineWidth);
            normalized.lineStyle = normalizeTVDrawingStyle(normalized.lineStyle);
            normalized.logical = normalizeTVDrawingLogical(normalized.logical);
            normalized.l1 = normalizeTVDrawingLogical(normalized.l1);
            normalized.l2 = normalizeTVDrawingLogical(normalized.l2);
            normalized.l3 = normalizeTVDrawingLogical(normalized.l3);
            normalized.labelPosition = normalizeTVDrawingLabelPosition(normalized.type, normalized.labelPosition);
            if (normalized.type === 'text') {
                normalized.text = String(normalized.text || 'Label').slice(0, 40);
            } else {
                normalized.label = String(normalized.label || '').slice(0, 40);
            }
            if (normalized.type === 'hline') {
                applyTVHLinePresetToDef(normalized, normalized.preset, { fallbackColor: normalized.color });
            } else {
                delete normalized.preset;
            }
            if (normalized.type === 'channel') {
                normalized.showMidline = normalizeTVDrawingMidlineVisible(normalized.showMidline);
                normalized.extendRight = normalizeTVDrawingExtendRight(normalized.extendRight);
            } else {
                delete normalized.showMidline;
                delete normalized.extendRight;
            }
            if (normalized.id !== '__preview__') {
                if (Number.isFinite(Number(normalized.time))) {
                    normalized.logical = null;
                }
                if (Number.isFinite(Number(normalized.t1))) {
                    normalized.l1 = null;
                }
                if (Number.isFinite(Number(normalized.t2))) {
                    normalized.l2 = null;
                }
                if (Number.isFinite(Number(normalized.t3))) {
                    normalized.l3 = null;
                }
            }
            return normalized;
        }

        function persistTVDrawings() {
            const scopeKey = tvDrawingScopeKey || getCurrentTVDrawingScopeKey();
            if (!scopeKey) return;
            const store = loadTVDrawingStore();
            if (tvDrawingDefs.length) {
                store[scopeKey] = tvDrawingDefs.map(def => normalizeTVDrawingDef(def)).filter(Boolean);
            } else {
                delete store[scopeKey];
            }
            saveTVDrawingStore(store);
        }

        function tvFindDrawingById(id = tvSelectedDrawingId) {
            return tvDrawingDefs.find(def => def.id === id) || null;
        }

        function tvRefreshDrawingLevels() {
            tvRefreshOverlayLevelPrices();
        }

        function tvDrawingDashArray(def) {
            if (!def) return '';
            if (def.lineStyle === 'dashed') return '10 7';
            if (def.lineStyle === 'dotted') return '3 6';
            return '';
        }

        function getTVDrawingContrastTextColor(color) {
            const hex = String(color || '').trim();
            const match = hex.match(/^#?([0-9a-f]{6})$/i);
            if (!match) return '#f8fafc';
            const value = match[1];
            const r = parseInt(value.slice(0, 2), 16);
            const g = parseInt(value.slice(2, 4), 16);
            const b = parseInt(value.slice(4, 6), 16);
            const luminance = ((0.299 * r) + (0.587 * g) + (0.114 * b)) / 255;
            return luminance > 0.62 ? '#0b1220' : '#f8fafc';
        }

        function getTVTimeframeSeconds() {
            const timeframeEl = document.getElementById('timeframe');
            const minutes = timeframeEl ? parseInt(timeframeEl.value, 10) : NaN;
            if (Number.isFinite(minutes) && minutes > 0) return minutes * 60;
            if (tvLastCandles.length > 1) {
                const span = Math.abs(Number(tvLastCandles[tvLastCandles.length - 1].time) - Number(tvLastCandles[tvLastCandles.length - 2].time));
                if (Number.isFinite(span) && span > 0) return span;
            }
            return 60;
        }

        function tvCoordinateToLogical(x) {
            const timeScale = tvPriceChart && tvPriceChart.timeScale ? tvPriceChart.timeScale() : null;
            if (!timeScale || typeof timeScale.coordinateToLogical !== 'function') return null;
            try {
                const logical = timeScale.coordinateToLogical(x);
                return Number.isFinite(logical) ? logical : null;
            } catch (e) {
                return null;
            }
        }

        function tvLogicalToCoordinate(logical) {
            const timeScale = tvPriceChart && tvPriceChart.timeScale ? tvPriceChart.timeScale() : null;
            if (!timeScale || typeof timeScale.logicalToCoordinate !== 'function' || !Number.isFinite(logical)) return null;
            try {
                const x = timeScale.logicalToCoordinate(logical);
                return Number.isFinite(x) ? x : null;
            } catch (e) {
                return null;
            }
        }

        function tvLogicalToTime(logical) {
            if (!tvLastCandles.length || !Number.isFinite(logical)) return null;
            const span = getTVTimeframeSeconds();
            const lastIndex = tvLastCandles.length - 1;
            const firstTime = Number(tvLastCandles[0].time);
            const lastTime = Number(tvLastCandles[lastIndex].time);
            if (!Number.isFinite(firstTime) || !Number.isFinite(lastTime)) return null;
            if (logical <= 0) return Math.round(firstTime + (logical * span));
            if (logical >= lastIndex) return Math.round(lastTime + ((logical - lastIndex) * span));
            const lowerIndex = Math.max(0, Math.min(lastIndex, Math.floor(logical)));
            const upperIndex = Math.max(0, Math.min(lastIndex, Math.ceil(logical)));
            const lower = tvLastCandles[lowerIndex];
            const upper = tvLastCandles[upperIndex] || lower;
            if (!lower) return null;
            if (!upper || upperIndex === lowerIndex) return Number(lower.time) || null;
            const step = Math.max(1, Number(upper.time) - Number(lower.time));
            return Math.round(Number(lower.time) + ((logical - lowerIndex) * step));
        }

        function tvTimeToLogical(time) {
            if (!tvLastCandles.length || !Number.isFinite(time)) return null;
            const span = getTVTimeframeSeconds();
            const firstTime = Number(tvLastCandles[0].time);
            const lastIndex = tvLastCandles.length - 1;
            const lastTime = Number(tvLastCandles[lastIndex].time);
            if (!Number.isFinite(firstTime) || !Number.isFinite(lastTime)) return null;
            const maxBackward = span * 12;
            const maxForward = span * 240;
            if (time < firstTime - maxBackward || time > lastTime + maxForward) return null;
            if (time <= firstTime) return (time - firstTime) / span;
            if (time >= lastTime) return lastIndex + ((time - lastTime) / span);
            for (let i = 1; i < tvLastCandles.length; i += 1) {
                const prev = tvLastCandles[i - 1];
                const next = tvLastCandles[i];
                const prevTime = Number(prev && prev.time);
                const nextTime = Number(next && next.time);
                if (!Number.isFinite(prevTime) || !Number.isFinite(nextTime)) continue;
                if (time === nextTime) return i;
                if (time < nextTime) {
                    const step = Math.max(1, nextTime - prevTime);
                    return (i - 1) + ((time - prevTime) / step);
                }
            }
            return lastIndex;
        }

        function tvTimeToCoordinateExtended(time) {
            const timeScale = tvPriceChart && tvPriceChart.timeScale ? tvPriceChart.timeScale() : null;
            if (!timeScale || !Number.isFinite(time)) return null;
            try {
                const x = timeScale.timeToCoordinate(time);
                if (Number.isFinite(x)) return x;
            } catch (e) {}
            const logical = tvTimeToLogical(time);
            return logical == null ? null : tvLogicalToCoordinate(logical);
        }

        function tvResolveDrawingAnchorX(timeValue, logicalValue, previewX) {
            if (Number.isFinite(previewX)) return previewX;
            if (Number.isFinite(timeValue)) {
                const timeX = tvTimeToCoordinateExtended(timeValue);
                return Number.isFinite(timeX) ? timeX : null;
            }
            return Number.isFinite(logicalValue) ? tvLogicalToCoordinate(logicalValue) : null;
        }

        function tvResolveDrawingAnchorLogicalValue(timeValue, logicalValue) {
            if (Number.isFinite(logicalValue)) return logicalValue;
            if (Number.isFinite(timeValue)) return tvTimeToLogical(Number(timeValue));
            return null;
        }

        function tvComputeChannelData(def) {
            if (!def || def.type !== 'channel') return null;
            const l1 = tvResolveDrawingAnchorLogicalValue(def.t1, def.l1);
            const l2 = tvResolveDrawingAnchorLogicalValue(def.t2, def.l2);
            const l3 = tvResolveDrawingAnchorLogicalValue(def.t3, def.l3);
            const p1 = Number(def.p1);
            const p2 = Number(def.p2);
            const p3 = Number(def.p3);
            if (![l1, l2, l3, p1, p2, p3].every(Number.isFinite)) return null;
            const dx = l2 - l1;
            if (Math.abs(dx) < 1e-6) {
                return {
                    vertical: true,
                    l1,
                    l2,
                    l3,
                    p1,
                    p2,
                    p3,
                    mid1: p1,
                    mid2: p2,
                };
            }
            const slope = (p2 - p1) / dx;
            const q1 = p3 + (slope * (l1 - l3));
            const q2 = p3 + (slope * (l2 - l3));
            return {
                vertical: false,
                slope,
                l1,
                l2,
                l3,
                p1,
                p2,
                p3,
                q1,
                q2,
                mid1: (p1 + q1) / 2,
                mid2: (p2 + q2) / 2,
            };
        }

        function measureTVDrawingBadgeWidth(text, minWidth, maxWidth, leadingPad = 8) {
            const safeText = String(text || '');
            return Math.max(minWidth, Math.min(maxWidth, 14 + leadingPad + (safeText.length * 7)));
        }

        function appendTVDrawingBadge(group, options = {}) {
            if (!group) return null;
            const labelText = String(options.label || '').trim();
            const valueText = options.value == null ? '' : String(options.value).trim();
            if (!labelText && !valueText) return null;
            const width = Math.max(0, Number(options.boundWidth) || 0);
            const height = Math.max(0, Number(options.boundHeight) || 0);
            const badgeHeight = 22;
            const labelHasSwatch = labelText && options.showSwatch !== false;
            const labelPad = labelHasSwatch ? 22 : 8;
            const labelWidth = labelText ? measureTVDrawingBadgeWidth(labelText, 54, 170, labelPad) : 0;
            const valueWidth = valueText ? measureTVDrawingBadgeWidth(valueText, 56, 100, 8) : 0;
            const gap = labelText && valueText ? 2 : 0;
            const totalWidth = labelWidth + gap + valueWidth;
            const anchor = options.anchor === 'right' ? 'right' : (options.anchor === 'center' ? 'center' : 'left');
            let x = Number.isFinite(options.x) ? options.x : 8;
            if (anchor === 'right') x -= totalWidth;
            if (anchor === 'center') x -= totalWidth / 2;
            if (width > totalWidth) x = Math.max(8, Math.min(width - totalWidth - 8, x));
            const y = height > badgeHeight
                ? Math.max(8, Math.min(height - badgeHeight - 8, Number(options.y) || 8))
                : 0;
            let cursorX = x;

            if (labelText) {
                group.appendChild(createSvgEl('rect', {
                    class: 'tv-drawing-shape',
                    x: cursorX,
                    y,
                    rx: 6,
                    ry: 6,
                    width: labelWidth,
                    height: badgeHeight,
                    fill: 'rgba(71, 85, 105, 0.96)',
                    stroke: 'rgba(255,255,255,0.10)',
                    'stroke-width': 1,
                }));
                if (labelHasSwatch) {
                    group.appendChild(createSvgEl('circle', {
                        class: 'tv-drawing-shape',
                        cx: cursorX + 10,
                        cy: y + (badgeHeight / 2),
                        r: 4,
                        fill: options.color || '#FFD700',
                    }));
                }
                group.appendChild(createSvgEl('text', {
                    class: 'tv-drawing-text',
                    x: cursorX + (labelHasSwatch ? 18 : 8),
                    y: y + (badgeHeight / 2),
                    fill: '#f8fafc',
                }));
                group.lastChild.textContent = labelText;
                cursorX += labelWidth + gap;
            }

            if (valueText) {
                const valueFill = options.valueFill || options.color || '#FFD700';
                group.appendChild(createSvgEl('rect', {
                    class: 'tv-drawing-shape',
                    x: cursorX,
                    y,
                    rx: 6,
                    ry: 6,
                    width: valueWidth,
                    height: badgeHeight,
                    fill: valueFill,
                    stroke: valueFill,
                    'stroke-width': 1,
                }));
                group.appendChild(createSvgEl('text', {
                    class: 'tv-drawing-text',
                    x: cursorX + 8,
                    y: y + (badgeHeight / 2),
                    fill: getTVDrawingContrastTextColor(valueFill),
                }));
                group.lastChild.textContent = valueText;
            }

            return { x, y, width: totalWidth, height: badgeHeight };
        }

        function tvDrawingLineStyleToNative(lineStyle) {
            if (!window.LightweightCharts) return null;
            const LS = LightweightCharts.LineStyle;
            if (lineStyle === 'dashed') return LS.Dashed;
            if (lineStyle === 'dotted') return LS.Dotted;
            return LS.Solid;
        }

        function clearTVUserHLinePriceLines() {
            if (tvCandleSeries) {
                tvUserHLinePriceLines.forEach(line => {
                    try { tvCandleSeries.removePriceLine(line); } catch (e) {}
                });
            }
            tvUserHLinePriceLines = new Map();
        }

        function syncTVUserHLinePriceLines() {
            if (!tvCandleSeries || !window.LightweightCharts) {
                tvUserHLinePriceLines = new Map();
                return;
            }
            const defs = tvDrawingDefs.filter(def => def && def.type === 'hline' && Number.isFinite(def.price));
            const activeIds = new Set(defs.map(def => def.id));
            tvUserHLinePriceLines.forEach((line, id) => {
                if (activeIds.has(id)) return;
                try { tvCandleSeries.removePriceLine(line); } catch (e) {}
                tvUserHLinePriceLines.delete(id);
            });
            defs.forEach(def => {
                const options = {
                    price: def.price,
                    color: def.color,
                    lineWidth: clampTVDrawingLineWidth(def.lineWidth),
                    lineStyle: tvDrawingLineStyleToNative(def.lineStyle),
                    axisLabelVisible: true,
                    title: String(def.label || ''),
                };
                const existing = tvUserHLinePriceLines.get(def.id);
                if (existing && typeof existing.applyOptions === 'function') {
                    try {
                        existing.applyOptions(options);
                        return;
                    } catch (e) {
                        try { tvCandleSeries.removePriceLine(existing); } catch (err) {}
                        tvUserHLinePriceLines.delete(def.id);
                    }
                } else if (existing) {
                    try { tvCandleSeries.removePriceLine(existing); } catch (e) {}
                    tvUserHLinePriceLines.delete(def.id);
                }
                try {
                    const line = tvCandleSeries.createPriceLine(options);
                    tvUserHLinePriceLines.set(def.id, line);
                } catch (e) {
                    console.warn('createPriceLine failed for user H-Line', e);
                }
            });
        }

        function getTVDrawingLabelPositionOptions(type) {
            if (type === 'trendline' || type === 'channel') {
                return [
                    { value: 'auto', label: 'Auto' },
                    { value: 'start', label: 'Start' },
                    { value: 'middle', label: 'Middle' },
                    { value: 'end', label: 'End' },
                ];
            }
            if (type === 'rect') {
                return [
                    { value: 'auto', label: 'Auto' },
                    { value: 'top-left', label: 'Top Left' },
                    { value: 'top-right', label: 'Top Right' },
                    { value: 'bottom-left', label: 'Bottom Left' },
                    { value: 'bottom-right', label: 'Bottom Right' },
                    { value: 'center', label: 'Center' },
                ];
            }
            return [];
        }

        function getTVTrendlineLabelPlacement(screen, labelPosition, width, height) {
            const xMid = (screen.x1 + screen.x2) / 2;
            const yMid = (screen.y1 + screen.y2) / 2;
            const placement = labelPosition || 'auto';
            if (placement === 'start') {
                return { x: screen.x1 + 12, y: screen.y1 - 12, anchor: 'left', boundWidth: width, boundHeight: height };
            }
            if (placement === 'middle') {
                return { x: xMid, y: yMid - 16, anchor: 'center', boundWidth: width, boundHeight: height };
            }
            return { x: screen.x2 + 12, y: screen.y2 - 12, anchor: 'left', boundWidth: width, boundHeight: height };
        }

        function getTVRectLabelPlacement(screen, labelPosition, width, height) {
            const placement = labelPosition || 'auto';
            const inset = 8;
            const bottomY = screen.y + screen.h - 30;
            if (placement === 'top-right') {
                return { x: screen.x + screen.w - inset, y: screen.y + inset, anchor: 'right', boundWidth: width, boundHeight: height };
            }
            if (placement === 'bottom-left') {
                return { x: screen.x + inset, y: bottomY, anchor: 'left', boundWidth: width, boundHeight: height };
            }
            if (placement === 'bottom-right') {
                return { x: screen.x + screen.w - inset, y: bottomY, anchor: 'right', boundWidth: width, boundHeight: height };
            }
            if (placement === 'center') {
                return { x: screen.x + (screen.w / 2), y: screen.y + (screen.h / 2) - 11, anchor: 'center', boundWidth: width, boundHeight: height };
            }
            return { x: screen.x + inset, y: screen.y + inset, anchor: 'left', boundWidth: width, boundHeight: height };
        }

        function scheduleTVDrawingOverlayDraw() {
            if (tvDrawingOverlayPending) return;
            tvDrawingOverlayPending = true;
            requestAnimationFrame(() => {
                tvDrawingOverlayPending = false;
                drawTVDrawingOverlay();
            });
        }

        function ensureTVDrawingOverlay() {
            const container = document.getElementById('price-chart');
            if (!container) return null;
            let overlay = container.querySelector('.tv-drawing-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'tv-drawing-overlay';
                overlay.innerHTML = '<svg class="tv-drawing-svg" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"></svg>';
                container.appendChild(overlay);
            }
            return overlay;
        }

        function scheduleSessionLevelCloudDraw() {
            if (tvSessionCloudOverlayPending) return;
            tvSessionCloudOverlayPending = true;
            requestAnimationFrame(() => {
                tvSessionCloudOverlayPending = false;
                drawSessionLevelClouds();
            });
        }

        function ensureSessionLevelCloudOverlay() {
            const container = document.getElementById('price-chart');
            if (!container) return null;
            let overlay = container.querySelector('.tv-session-cloud-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'tv-session-cloud-overlay';
                overlay.innerHTML = '<svg class="tv-session-cloud-svg" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"></svg>';
                const drawingOverlay = container.querySelector('.tv-drawing-overlay');
                if (drawingOverlay) container.insertBefore(overlay, drawingOverlay);
                else container.appendChild(overlay);
            }
            return overlay;
        }

        function appendSessionLevelCloud(svg, options = {}) {
            if (!svg || !tvCandleSeries) return;
            const topPrice = Number(options.topPrice);
            const bottomPrice = Number(options.bottomPrice);
            if (!Number.isFinite(topPrice) || !Number.isFinite(bottomPrice)) return;
            const yTopRaw = tvCandleSeries.priceToCoordinate(Math.max(topPrice, bottomPrice));
            const yBotRaw = tvCandleSeries.priceToCoordinate(Math.min(topPrice, bottomPrice));
            if ([yTopRaw, yBotRaw].some(v => v == null || Number.isNaN(v))) return;
            const width = Math.max(0, Number(options.width) || 0);
            const y = Math.min(yTopRaw, yBotRaw);
            const h = Math.max(1, Math.abs(yBotRaw - yTopRaw));
            if (!width || !h) return;
            const gradientId = `session-cloud-${options.key || 'range'}`;
            const defs = createSvgEl('defs');
            const gradient = createSvgEl('linearGradient', {
                id: gradientId,
                x1: 0,
                y1: y,
                x2: 0,
                y2: y + h,
                gradientUnits: 'userSpaceOnUse',
            });
            gradient.appendChild(createSvgEl('stop', {
                offset: '0%',
                'stop-color': options.topColor || '#10B981',
                'stop-opacity': options.opacityTop || 0.12,
            }));
            gradient.appendChild(createSvgEl('stop', {
                offset: '100%',
                'stop-color': options.bottomColor || '#EF4444',
                'stop-opacity': options.opacityBottom || 0.10,
            }));
            defs.appendChild(gradient);
            svg.appendChild(defs);
            svg.appendChild(createSvgEl('rect', {
                x: 0,
                y,
                width,
                height: h,
                fill: `url(#${gradientId})`,
                stroke: options.stroke || 'rgba(255,255,255,0.08)',
                'stroke-width': 1,
            }));
        }

        function drawSessionLevelClouds() {
            const overlay = ensureSessionLevelCloudOverlay();
            if (!overlay) return;
            const svg = overlay.querySelector('svg');
            if (!svg) return;
            const width = Math.max(0, overlay.clientWidth);
            const height = Math.max(0, overlay.clientHeight);
            svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
            svg.replaceChildren();
            const settings = normalizeSessionLevelSettings(getSessionLevelSettingsFromDom());
            const levels = _lastSessionLevels;
            if (!settings.enabled || !levels || !tvCandleSeries || !width || !height) {
                overlay.style.display = 'none';
                return;
            }
            let visible = 0;
            if (settings.opening_range && settings.show_or_cloud) {
                const highPref = getPriceLevelPref('opening_range_high');
                const lowPref = getPriceLevelPref('opening_range_low');
                const high = levels.opening_range_high && levels.opening_range_high.price;
                const low = levels.opening_range_low && levels.opening_range_low.price;
                if (Number.isFinite(high) && Number.isFinite(low)) {
                    appendSessionLevelCloud(svg, {
                        key: 'or',
                        width,
                        topPrice: high,
                        bottomPrice: low,
                        topColor: (highPref && highPref.color) || '#10B981',
                        bottomColor: (lowPref && lowPref.color) || '#EF4444',
                        opacityTop: 0.12,
                        opacityBottom: 0.10,
                    });
                    visible += 1;
                }
            }
            if (settings.initial_balance && settings.show_ib_cloud) {
                const highPref = getPriceLevelPref('ib_high');
                const lowPref = getPriceLevelPref('ib_low');
                const high = levels.ib_high && levels.ib_high.price;
                const low = levels.ib_low && levels.ib_low.price;
                if (Number.isFinite(high) && Number.isFinite(low)) {
                    appendSessionLevelCloud(svg, {
                        key: 'ib',
                        width,
                        topPrice: high,
                        bottomPrice: low,
                        topColor: (highPref && highPref.color) || '#10B981',
                        bottomColor: (lowPref && lowPref.color) || '#EF4444',
                        opacityTop: 0.10,
                        opacityBottom: 0.09,
                    });
                    visible += 1;
                }
            }
            overlay.style.display = visible > 0 ? 'block' : 'none';
        }

        function ensureTVDrawingEditor() {
            const container = document.getElementById('price-chart');
            if (!container) return null;
            let editor = container.querySelector('.tv-drawing-editor');
            if (!editor) {
                editor = document.createElement('div');
                editor.className = 'tv-drawing-editor';
                editor.innerHTML =
                    '<div class="tv-drawing-editor-head">' +
                        '<span id="tv-drawing-editor-title">Drawing</span>' +
                        '<button type="button" class="tv-tb-btn" data-action="close">Close</button>' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row">' +
                        '<label for="tv-selected-draw-color">Color</label>' +
                        '<input type="color" id="tv-selected-draw-color" />' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row" id="tv-drawing-preset-row">' +
                        '<label for="tv-selected-draw-preset">Preset</label>' +
                        '<select id="tv-selected-draw-preset">' +
                            '<option value="support">Support</option>' +
                            '<option value="resistance">Resistance</option>' +
                            '<option value="neutral">Neutral</option>' +
                            '<option value="custom">Custom</option>' +
                        '</select>' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row">' +
                        '<label for="tv-selected-draw-width">Thickness</label>' +
                        '<select id="tv-selected-draw-width">' +
                            '<option value="1">1 px</option>' +
                            '<option value="2">2 px</option>' +
                            '<option value="3">3 px</option>' +
                            '<option value="4">4 px</option>' +
                            '<option value="5">5 px</option>' +
                        '</select>' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row">' +
                        '<label for="tv-selected-draw-style">Style</label>' +
                        '<select id="tv-selected-draw-style">' +
                            '<option value="solid">Solid</option>' +
                            '<option value="dashed">Dashed</option>' +
                            '<option value="dotted">Dotted</option>' +
                        '</select>' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row">' +
                        '<label for="tv-selected-draw-text">Label</label>' +
                        '<input type="text" id="tv-selected-draw-text" maxlength="40" />' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row" id="tv-drawing-label-position-row">' +
                        '<label for="tv-selected-draw-label-position">Label Position</label>' +
                        '<select id="tv-selected-draw-label-position"></select>' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row" id="tv-drawing-midline-row">' +
                        '<label for="tv-selected-draw-midline">Midline</label>' +
                        '<input type="checkbox" id="tv-selected-draw-midline" />' +
                    '</div>' +
                    '<div class="tv-drawing-editor-row" id="tv-drawing-extend-right-row">' +
                        '<label for="tv-selected-draw-extend-right">Extend Right</label>' +
                        '<input type="checkbox" id="tv-selected-draw-extend-right" />' +
                    '</div>' +
                    '<div class="tv-drawing-editor-actions">' +
                        '<button type="button" class="tv-tb-btn danger" data-action="delete">Delete</button>' +
                    '</div>';
                container.appendChild(editor);
            }
            if (!editor.__wired) {
                editor.__wired = true;
                editor.addEventListener('click', event => event.stopPropagation());
                editor.querySelector('[data-action="close"]').addEventListener('click', () => {
                    tvSelectedDrawingId = null;
                    updateTVDrawingEditor();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('[data-action="delete"]').addEventListener('click', () => {
                    if (!tvSelectedDrawingId) return;
                    tvDrawingDefs = tvDrawingDefs.filter(def => def.id !== tvSelectedDrawingId);
                    tvSelectedDrawingId = null;
                    persistTVDrawings();
                    tvRestoreDrawings();
                    updateTVDrawingEditor();
                });
                editor.querySelector('#tv-selected-draw-color').addEventListener('input', event => {
                    const def = tvFindDrawingById();
                    if (!def) return;
                    const nextColor = event.target.value || def.color;
                    if (def.type === 'hline') {
                        applyTVHLinePresetToDef(def, 'custom', { fallbackColor: nextColor });
                    }
                    def.color = nextColor;
                    persistTVDrawings();
                    if (def.type === 'hline') {
                        syncTVUserHLinePriceLines();
                        updateTVDrawingEditor();
                    }
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-preset').addEventListener('change', event => {
                    const def = tvFindDrawingById();
                    if (!def || def.type !== 'hline') return;
                    applyTVHLinePresetToDef(def, event.target.value, { fallbackColor: def.color });
                    persistTVDrawings();
                    syncTVUserHLinePriceLines();
                    updateTVDrawingEditor();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-width').addEventListener('change', event => {
                    const def = tvFindDrawingById();
                    if (!def || def.type === 'text') return;
                    def.lineWidth = clampTVDrawingLineWidth(event.target.value);
                    persistTVDrawings();
                    if (def.type === 'hline') syncTVUserHLinePriceLines();
                    tvRefreshDrawingLevels();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-style').addEventListener('change', event => {
                    const def = tvFindDrawingById();
                    if (!def || def.type === 'text') return;
                    def.lineStyle = normalizeTVDrawingStyle(event.target.value);
                    persistTVDrawings();
                    if (def.type === 'hline') syncTVUserHLinePriceLines();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-text').addEventListener('input', event => {
                    const def = tvFindDrawingById();
                    if (!def) return;
                    if (def.type === 'text') {
                        def.text = String(event.target.value || '').slice(0, 40) || 'Label';
                    } else {
                        def.label = String(event.target.value || '').slice(0, 40);
                    }
                    persistTVDrawings();
                    if (def.type === 'hline') syncTVUserHLinePriceLines();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-label-position').addEventListener('change', event => {
                    const def = tvFindDrawingById();
                    if (!def) return;
                    def.labelPosition = normalizeTVDrawingLabelPosition(def.type, event.target.value);
                    persistTVDrawings();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-midline').addEventListener('change', event => {
                    const def = tvFindDrawingById();
                    if (!def || def.type !== 'channel') return;
                    def.showMidline = !!event.target.checked;
                    persistTVDrawings();
                    scheduleTVDrawingOverlayDraw();
                });
                editor.querySelector('#tv-selected-draw-extend-right').addEventListener('change', event => {
                    const def = tvFindDrawingById();
                    if (!def || def.type !== 'channel') return;
                    def.extendRight = !!event.target.checked;
                    persistTVDrawings();
                    scheduleTVDrawingOverlayDraw();
                });
            }
            return editor;
        }

        function updateTVDrawingEditor() {
            const editor = ensureTVDrawingEditor();
            if (!editor) return;
            const def = tvFindDrawingById();
            if (!def) {
                editor.classList.remove('visible');
                return;
            }
            const titleEl = editor.querySelector('#tv-drawing-editor-title');
            const colorInput = editor.querySelector('#tv-selected-draw-color');
            const presetRow = editor.querySelector('#tv-drawing-preset-row');
            const presetSelect = editor.querySelector('#tv-selected-draw-preset');
            const widthSelect = editor.querySelector('#tv-selected-draw-width');
            const styleSelect = editor.querySelector('#tv-selected-draw-style');
            const textInput = editor.querySelector('#tv-selected-draw-text');
            const labelPositionRow = editor.querySelector('#tv-drawing-label-position-row');
            const labelPositionSelect = editor.querySelector('#tv-selected-draw-label-position');
            const midlineRow = editor.querySelector('#tv-drawing-midline-row');
            const midlineInput = editor.querySelector('#tv-selected-draw-midline');
            const extendRightRow = editor.querySelector('#tv-drawing-extend-right-row');
            const extendRightInput = editor.querySelector('#tv-selected-draw-extend-right');
            if (titleEl) {
                const typeLabel = def.type === 'hline' ? 'H-Line'
                    : def.type === 'trendline' ? 'Trend Line'
                    : def.type === 'channel' ? 'Channel'
                    : def.type === 'rect' ? 'Box'
                    : 'Text Label';
                titleEl.textContent = typeLabel;
            }
            if (colorInput) colorInput.value = def.color || '#FFD700';
            if (presetRow && presetSelect) {
                const isHLine = def.type === 'hline';
                presetRow.style.display = isHLine ? 'flex' : 'none';
                presetSelect.value = normalizeTVHLinePresetKey(def.preset);
                presetSelect.disabled = !isHLine;
            }
            if (widthSelect) {
                widthSelect.value = String(clampTVDrawingLineWidth(def.lineWidth));
                widthSelect.disabled = def.type === 'text';
            }
            if (styleSelect) {
                styleSelect.value = normalizeTVDrawingStyle(def.lineStyle);
                styleSelect.disabled = def.type === 'text';
            }
            if (textInput) {
                textInput.value = def.type === 'text' ? (def.text || '') : (def.label || '');
                textInput.disabled = false;
            }
            if (labelPositionRow && labelPositionSelect) {
                const options = getTVDrawingLabelPositionOptions(def.type);
                if (options.length) {
                    labelPositionRow.style.display = 'flex';
                    labelPositionSelect.innerHTML = options.map(opt =>
                        `<option value="${opt.value}">${opt.label}</option>`
                    ).join('');
                    labelPositionSelect.value = normalizeTVDrawingLabelPosition(def.type, def.labelPosition);
                    labelPositionSelect.disabled = false;
                } else {
                    labelPositionRow.style.display = 'none';
                    labelPositionSelect.innerHTML = '';
                    labelPositionSelect.disabled = true;
                }
            }
            if (midlineRow && midlineInput) {
                const isChannel = def.type === 'channel';
                midlineRow.style.display = isChannel ? 'flex' : 'none';
                midlineInput.checked = isChannel ? normalizeTVDrawingMidlineVisible(def.showMidline) : true;
                midlineInput.disabled = !isChannel;
            }
            if (extendRightRow && extendRightInput) {
                const isChannel = def.type === 'channel';
                extendRightRow.style.display = isChannel ? 'flex' : 'none';
                extendRightInput.checked = isChannel ? normalizeTVDrawingExtendRight(def.extendRight) : false;
                extendRightInput.disabled = !isChannel;
            }
            editor.classList.add('visible');
        }

        function tvLoadDrawingsForScope(scopeKey) {
            tvDrawingScopeKey = scopeKey || '';
            const store = loadTVDrawingStore();
            const scopeParts = getCurrentTVDrawingScopeParts();
            const legacyScopeKey = getLegacyTVDrawingScopeKey();
            const fallbackLegacyKey = legacyScopeKey && !Array.isArray(store[legacyScopeKey])
                ? findLegacyTVDrawingScopeKey(store, scopeParts)
                : '';
            const rawDefs = Array.isArray(store[tvDrawingScopeKey])
                ? store[tvDrawingScopeKey]
                : (Array.isArray(store[legacyScopeKey])
                    ? store[legacyScopeKey]
                    : (Array.isArray(store[fallbackLegacyKey]) ? store[fallbackLegacyKey] : []));
            tvDrawingDefs = rawDefs.map(normalizeTVDrawingDef).filter(Boolean);
            if (!tvFindDrawingById()) {
                tvSelectedDrawingId = null;
            }
            tvDrawStart = null;
            tvDrawingPreviewDef = null;
            tvRestoreDrawings();
        }

        function tvSyncDrawingScope() {
            const scopeKey = getCurrentTVDrawingScopeKey();
            if (!scopeKey || scopeKey === tvDrawingScopeKey) return;
            tvLoadDrawingsForScope(scopeKey);
        }

        function createSvgEl(tagName, attrs = {}) {
            const el = document.createElementNS('http://www.w3.org/2000/svg', tagName);
            Object.entries(attrs).forEach(([key, value]) => {
                if (value !== undefined && value !== null) el.setAttribute(key, String(value));
            });
            return el;
        }

        function tvResolvePreviewPoint(param) {
            const container = document.getElementById('price-chart');
            if (!tvPriceChart || !tvCandleSeries || !container || !param || !param.point) return null;
            const x = Math.max(0, Math.min(container.clientWidth, Number(param.point.x) || 0));
            const y = Math.max(0, Math.min(container.clientHeight, Number(param.point.y) || 0));
            const price = tvCandleSeries.coordinateToPrice(y);
            if (price === null || price === undefined || Number.isNaN(price)) return null;
            return { x, y, price };
        }

        function tvDrawingToScreen(def, width) {
            if (!tvPriceChart || !tvCandleSeries || !def) return null;
            if (def.type === 'hline') {
                const y = def.previewY != null ? def.previewY : tvCandleSeries.priceToCoordinate(def.price);
                if (y == null || Number.isNaN(y)) return null;
                return { type: 'hline', y };
            }
            if (def.type === 'trendline') {
                const x1 = tvResolveDrawingAnchorX(def.t1, def.l1, null);
                const x2 = tvResolveDrawingAnchorX(def.t2, def.l2, def.previewX2);
                const y1 = tvCandleSeries.priceToCoordinate(def.p1);
                const y2 = def.previewY2 != null ? def.previewY2 : tvCandleSeries.priceToCoordinate(def.p2);
                if ([x1, x2, y1, y2].some(v => v == null || Number.isNaN(v))) return null;
                return { type: 'trendline', x1, y1, x2, y2 };
            }
            if (def.type === 'channel') {
                const channel = tvComputeChannelData(def);
                if (!channel) return null;
                const x1 = tvResolveDrawingAnchorX(def.t1, def.l1, null);
                const x2 = tvResolveDrawingAnchorX(def.t2, def.l2, def.previewX2);
                if ([x1, x2].some(v => v == null || Number.isNaN(v))) return null;
                const y1 = tvCandleSeries.priceToCoordinate(channel.p1);
                const y2 = tvCandleSeries.priceToCoordinate(channel.p2);
                if ([y1, y2].some(v => v == null || Number.isNaN(v))) return null;
                if (channel.vertical) {
                    const x3 = tvResolveDrawingAnchorX(def.t3, def.l3, def.previewX3);
                    if (x3 == null || Number.isNaN(x3)) return null;
                    const parallelX1 = x1 + (x3 - x1);
                    const parallelX2 = x2 + (x3 - x2);
                    return {
                        type: 'channel',
                        vertical: true,
                        x1,
                        y1,
                        x2,
                        y2,
                        px1: parallelX1,
                        py1: y1,
                        px2: parallelX2,
                        py2: y2,
                        mx1: x1 + ((parallelX1 - x1) / 2),
                        my1: y1,
                        mx2: x2 + ((parallelX2 - x2) / 2),
                        my2: y2,
                    };
                }
                const py1 = tvCandleSeries.priceToCoordinate(channel.q1);
                const py2 = tvCandleSeries.priceToCoordinate(channel.q2);
                const my1 = tvCandleSeries.priceToCoordinate(channel.mid1);
                const my2 = tvCandleSeries.priceToCoordinate(channel.mid2);
                if ([py1, py2, my1, my2].some(v => v == null || Number.isNaN(v))) return null;
                const screen = {
                    type: 'channel',
                    vertical: false,
                    x1,
                    y1,
                    x2,
                    y2,
                    px1: x1,
                    py1,
                    px2: x2,
                    py2,
                    mx1: x1,
                    my1,
                    mx2: x2,
                    my2,
                };
                if (def.extendRight === true) {
                    const rightLogical = tvCoordinateToLogical(width);
                    const anchorLogical = channel.l2 >= channel.l1 ? channel.l2 : channel.l1;
                    const anchorBase = channel.l2 >= channel.l1 ? channel.p2 : channel.p1;
                    const anchorParallel = channel.l2 >= channel.l1 ? channel.q2 : channel.q1;
                    const anchorMid = channel.l2 >= channel.l1 ? channel.mid2 : channel.mid1;
                    if (Number.isFinite(rightLogical) && rightLogical > anchorLogical) {
                        const delta = rightLogical - anchorLogical;
                        const baseExtY = tvCandleSeries.priceToCoordinate(anchorBase + (channel.slope * delta));
                        const parallelExtY = tvCandleSeries.priceToCoordinate(anchorParallel + (channel.slope * delta));
                        const midExtY = tvCandleSeries.priceToCoordinate(anchorMid + (channel.slope * delta));
                        if ([baseExtY, parallelExtY, midExtY].every(v => v != null && !Number.isNaN(v))) {
                            const rightIsSecond = channel.l2 >= channel.l1;
                            screen.extendRight = {
                                leftBaseX: rightIsSecond ? x1 : x2,
                                leftBaseY: rightIsSecond ? y1 : y2,
                                leftParallelX: rightIsSecond ? x1 : x2,
                                leftParallelY: rightIsSecond ? py1 : py2,
                                baseFromX: channel.l2 >= channel.l1 ? x2 : x1,
                                baseFromY: channel.l2 >= channel.l1 ? y2 : y1,
                                parallelFromX: channel.l2 >= channel.l1 ? x2 : x1,
                                parallelFromY: channel.l2 >= channel.l1 ? py2 : py1,
                                midFromX: channel.l2 >= channel.l1 ? x2 : x1,
                                midFromY: channel.l2 >= channel.l1 ? my2 : my1,
                                toX: width,
                                baseToY: baseExtY,
                                parallelToY: parallelExtY,
                                midToY: midExtY,
                            };
                        }
                    }
                }
                return screen;
            }
            if (def.type === 'rect') {
                const startY = tvCandleSeries.priceToCoordinate(def.startPrice != null ? def.startPrice : def.top);
                const yTop = def.previewY2 != null && startY != null ? Math.min(startY, def.previewY2) : tvCandleSeries.priceToCoordinate(def.top);
                const yBot = def.previewY2 != null && startY != null ? Math.max(startY, def.previewY2) : tvCandleSeries.priceToCoordinate(def.bot);
                if ([yTop, yBot].some(v => v == null || Number.isNaN(v))) return null;
                let x1 = 0;
                let x2 = width;
                if (def.t1 != null && (def.t2 != null || def.previewX2 != null)) {
                    const rawX1 = tvResolveDrawingAnchorX(def.t1, def.l1, null);
                    const rawX2 = tvResolveDrawingAnchorX(def.t2, def.l2, def.previewX2);
                    if ([rawX1, rawX2].some(v => v == null || Number.isNaN(v))) return null;
                    x1 = Math.min(rawX1, rawX2);
                    x2 = Math.max(rawX1, rawX2);
                }
                return {
                    type: 'rect',
                    x: x1,
                    y: Math.min(yTop, yBot),
                    w: Math.max(1, x2 - x1),
                    h: Math.max(1, Math.abs(yBot - yTop)),
                };
            }
            if (def.type === 'text') {
                const y = def.previewY != null ? def.previewY : tvCandleSeries.priceToCoordinate(def.price);
                if (y == null || Number.isNaN(y)) return null;
                const x = tvResolveDrawingAnchorX(def.time, def.logical, def.previewX);
                if (x == null || Number.isNaN(x)) return null;
                return { type: 'text', x, y };
            }
            return null;
        }

        function appendTVDrawingShape(svg, def, isPreview, width, height) {
            const screen = tvDrawingToScreen(def, width);
            if (!screen) return;
            const group = createSvgEl('g', {
                class: [
                    'tv-drawing-layer',
                    isPreview ? 'tv-drawing-preview' : '',
                    !isPreview && def.id === tvSelectedDrawingId ? 'tv-drawing-selected' : '',
                ].filter(Boolean).join(' ')
            });
            const strokeWidth = clampTVDrawingLineWidth(def.lineWidth);
            const dashArray = tvDrawingDashArray(def);
            const interactive = !isPreview && !tvDrawMode;
            const labelText = def.type === 'text' ? String(def.text || 'Label') : String(def.label || '');
            const bindSelect = (target) => {
                if (!interactive || !target) return;
                target.addEventListener('click', event => {
                    event.preventDefault();
                    event.stopPropagation();
                    tvSelectedDrawingId = def.id;
                    updateTVDrawingEditor();
                    scheduleTVDrawingOverlayDraw();
                });
            };

            if (screen.type === 'hline') {
                if (isPreview) {
                    group.appendChild(createSvgEl('line', {
                        class: 'tv-drawing-shape',
                        x1: 0,
                        y1: screen.y,
                        x2: width,
                        y2: screen.y,
                        stroke: def.color,
                        'stroke-width': strokeWidth,
                        'stroke-dasharray': dashArray || null,
                        'stroke-linecap': 'round',
                        opacity: 0.8,
                    }));
                    const priceText = Number.isFinite(def.price) ? def.price.toFixed(2) : '--';
                    const pillY = Math.max(8, Math.min(height - 22, screen.y - 11));
                    const pillWidth = Math.max(58, 16 + (priceText.length * 7));
                    const pillX = Math.max(8, width - pillWidth - 10);
                    group.appendChild(createSvgEl('rect', {
                        class: 'tv-drawing-shape',
                        x: pillX,
                        y: pillY,
                        rx: 6,
                        ry: 6,
                        width: pillWidth,
                        height: 22,
                        fill: 'rgba(15, 23, 42, 0.92)',
                        stroke: def.color,
                        'stroke-width': 1,
                    }));
                    group.appendChild(createSvgEl('text', {
                        class: 'tv-drawing-text',
                        x: pillX + 8,
                        y: pillY + 11,
                        fill: def.color,
                    }));
                    group.lastChild.textContent = priceText;
                } else if (def.id === tvSelectedDrawingId) {
                    group.appendChild(createSvgEl('line', {
                        class: 'tv-drawing-shape',
                        x1: 0,
                        y1: screen.y,
                        x2: width,
                        y2: screen.y,
                        stroke: 'rgba(255,255,255,0.42)',
                        'stroke-width': Math.max(strokeWidth + 1, 3),
                        'stroke-linecap': 'round',
                    }));
                }
                if (interactive) {
                    const hit = createSvgEl('line', {
                        class: 'tv-drawing-hitbox',
                        x1: 0,
                        y1: screen.y,
                        x2: width,
                        y2: screen.y,
                        'stroke-width': Math.max(12, strokeWidth + 8),
                    });
                    bindSelect(hit);
                    group.appendChild(hit);
                }
            } else if (screen.type === 'trendline') {
                group.appendChild(createSvgEl('line', {
                    class: 'tv-drawing-shape',
                    x1: screen.x1,
                    y1: screen.y1,
                    x2: screen.x2,
                    y2: screen.y2,
                    stroke: def.color,
                    'stroke-width': strokeWidth,
                    'stroke-dasharray': dashArray || null,
                    'stroke-linecap': 'round',
                    opacity: isPreview ? 0.8 : 1,
                }));
                if (labelText) {
                    appendTVDrawingBadge(group, Object.assign({
                        label: labelText,
                        color: def.color,
                    }, getTVTrendlineLabelPlacement(screen, def.labelPosition, width, height)));
                }
                if (interactive) {
                    const hit = createSvgEl('line', {
                        class: 'tv-drawing-hitbox',
                        x1: screen.x1,
                        y1: screen.y1,
                        x2: screen.x2,
                        y2: screen.y2,
                        'stroke-width': Math.max(12, strokeWidth + 8),
                    });
                    bindSelect(hit);
                    group.appendChild(hit);
                }
            } else if (screen.type === 'channel') {
                group.appendChild(createSvgEl('polygon', {
                    class: 'tv-drawing-shape',
                    points: screen.extendRight
                        ? `${screen.extendRight.leftBaseX},${screen.extendRight.leftBaseY} ${screen.extendRight.toX},${screen.extendRight.baseToY} ${screen.extendRight.toX},${screen.extendRight.parallelToY} ${screen.extendRight.leftParallelX},${screen.extendRight.leftParallelY}`
                        : `${screen.x1},${screen.y1} ${screen.x2},${screen.y2} ${screen.px2},${screen.py2} ${screen.px1},${screen.py1}`,
                    fill: def.color,
                    'fill-opacity': isPreview ? 0.07 : 0.1,
                    stroke: 'none',
                }));
                if (!isPreview && def.id === tvSelectedDrawingId) {
                    [
                        { x1: screen.x1, y1: screen.y1, x2: screen.x2, y2: screen.y2 },
                        { x1: screen.px1, y1: screen.py1, x2: screen.px2, y2: screen.py2 },
                    ].forEach(line => {
                        group.appendChild(createSvgEl('line', {
                            class: 'tv-drawing-shape',
                            x1: line.x1,
                            y1: line.y1,
                            x2: line.x2,
                            y2: line.y2,
                            stroke: 'rgba(255,255,255,0.42)',
                            'stroke-width': Math.max(strokeWidth + 1, 3),
                            'stroke-linecap': 'round',
                        }));
                    });
                }
                [
                    { x1: screen.x1, y1: screen.y1, x2: screen.x2, y2: screen.y2 },
                    { x1: screen.px1, y1: screen.py1, x2: screen.px2, y2: screen.py2 },
                ].forEach(line => {
                    group.appendChild(createSvgEl('line', {
                        class: 'tv-drawing-shape',
                        x1: line.x1,
                        y1: line.y1,
                        x2: line.x2,
                        y2: line.y2,
                        stroke: def.color,
                        'stroke-width': strokeWidth,
                        'stroke-dasharray': dashArray || null,
                        'stroke-linecap': 'round',
                        opacity: isPreview ? 0.8 : 1,
                    }));
                });
                if (screen.extendRight) {
                    [
                        { x1: screen.extendRight.baseFromX, y1: screen.extendRight.baseFromY, x2: screen.extendRight.toX, y2: screen.extendRight.baseToY },
                        { x1: screen.extendRight.parallelFromX, y1: screen.extendRight.parallelFromY, x2: screen.extendRight.toX, y2: screen.extendRight.parallelToY },
                    ].forEach(line => {
                        group.appendChild(createSvgEl('line', {
                            class: 'tv-drawing-shape',
                            x1: line.x1,
                            y1: line.y1,
                            x2: line.x2,
                            y2: line.y2,
                            stroke: def.color,
                            'stroke-width': strokeWidth,
                            'stroke-dasharray': dashArray || null,
                            'stroke-linecap': 'round',
                            opacity: isPreview ? 0.7 : 0.94,
                        }));
                    });
                }
                if (def.showMidline !== false) {
                    group.appendChild(createSvgEl('line', {
                        class: 'tv-drawing-shape',
                        x1: screen.mx1,
                        y1: screen.my1,
                        x2: screen.mx2,
                        y2: screen.my2,
                        stroke: def.color,
                        'stroke-width': Math.max(1, strokeWidth - 1),
                        'stroke-dasharray': '7 6',
                        'stroke-linecap': 'round',
                        opacity: 0.72,
                    }));
                    if (screen.extendRight) {
                        group.appendChild(createSvgEl('line', {
                            class: 'tv-drawing-shape',
                            x1: screen.extendRight.midFromX,
                            y1: screen.extendRight.midFromY,
                            x2: screen.extendRight.toX,
                            y2: screen.extendRight.midToY,
                            stroke: def.color,
                            'stroke-width': Math.max(1, strokeWidth - 1),
                            'stroke-dasharray': '7 6',
                            'stroke-linecap': 'round',
                            opacity: 0.68,
                        }));
                    }
                }
                if (labelText) {
                    appendTVDrawingBadge(group, Object.assign({
                        label: labelText,
                        color: def.color,
                    }, getTVTrendlineLabelPlacement({
                        x1: screen.mx1,
                        y1: screen.my1,
                        x2: screen.mx2,
                        y2: screen.my2,
                    }, def.labelPosition, width, height)));
                }
                if (interactive) {
                    [
                        { x1: screen.x1, y1: screen.y1, x2: screen.x2, y2: screen.y2 },
                        { x1: screen.px1, y1: screen.py1, x2: screen.px2, y2: screen.py2 },
                    ].forEach(line => {
                        const hit = createSvgEl('line', {
                            class: 'tv-drawing-hitbox',
                            x1: line.x1,
                            y1: line.y1,
                            x2: line.x2,
                            y2: line.y2,
                            'stroke-width': Math.max(12, strokeWidth + 8),
                        });
                        bindSelect(hit);
                        group.appendChild(hit);
                    });
                }
            } else if (screen.type === 'rect') {
                group.appendChild(createSvgEl('rect', {
                    class: 'tv-drawing-shape',
                    x: screen.x,
                    y: screen.y,
                    width: screen.w,
                    height: screen.h,
                    fill: def.color,
                    'fill-opacity': isPreview ? 0.08 : 0.12,
                    stroke: def.color,
                    'stroke-width': strokeWidth,
                    'stroke-dasharray': dashArray || null,
                }));
                if (labelText) {
                    appendTVDrawingBadge(group, Object.assign({
                        label: labelText,
                        color: def.color,
                    }, getTVRectLabelPlacement(screen, def.labelPosition, width, height)));
                }
                if (interactive) {
                    const hit = createSvgEl('rect', {
                        class: 'tv-drawing-hitbox',
                        x: Math.max(0, screen.x - 4),
                        y: Math.max(0, screen.y - 4),
                        width: Math.min(width, screen.w + 8),
                        height: Math.min(height, screen.h + 8),
                    });
                    bindSelect(hit);
                    group.appendChild(hit);
                }
            } else if (screen.type === 'text') {
                const text = labelText;
                const badge = appendTVDrawingBadge(group, {
                    label: text,
                    color: def.color,
                    x: screen.x,
                    y: screen.y - 11,
                    boundWidth: width,
                    boundHeight: height,
                });
                if (!badge) return;
                group.appendChild(createSvgEl('line', {
                    class: 'tv-drawing-shape',
                    x1: Math.max(0, badge.x - 18),
                    y1: screen.y,
                    x2: badge.x,
                    y2: screen.y,
                    stroke: def.color,
                    'stroke-width': 2,
                    opacity: isPreview ? 0.75 : 0.95,
                }));
                if (interactive) {
                    const hit = createSvgEl('rect', {
                        class: 'tv-drawing-hitbox',
                        x: badge.x,
                        y: badge.y,
                        rx: 6,
                        ry: 6,
                        width: badge.width,
                        height: badge.height,
                    });
                    bindSelect(hit);
                    group.appendChild(hit);
                }
            }

            svg.appendChild(group);
        }

        function drawTVDrawingOverlay() {
            const overlay = ensureTVDrawingOverlay();
            if (!overlay) return;
            const svg = overlay.querySelector('svg');
            if (!svg) return;
            const width = Math.max(0, overlay.clientWidth);
            const height = Math.max(0, overlay.clientHeight);
            svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
            svg.replaceChildren();
            if (!tvPriceChart || !tvCandleSeries || !width || !height) {
                updateTVDrawingEditor();
                return;
            }

            tvDrawingDefs.forEach(def => appendTVDrawingShape(svg, def, false, width, height));
            if (tvDrawingPreviewDef) {
                appendTVDrawingShape(svg, tvDrawingPreviewDef, true, width, height);
            }
            const pendingPoints = Array.isArray(tvDrawStart && tvDrawStart.points)
                ? tvDrawStart.points
                : (tvDrawStart ? [tvDrawStart] : []);
            if (pendingPoints.length && (tvDrawMode === 'trendline' || tvDrawMode === 'rect' || tvDrawMode === 'channel')) {
                pendingPoints.forEach(point => {
                    const x = tvResolveDrawingAnchorX(point.time, point.logical, null);
                    const y = tvCandleSeries.priceToCoordinate(point.price);
                    if (x == null || y == null || Number.isNaN(x) || Number.isNaN(y)) return;
                    svg.appendChild(createSvgEl('circle', {
                        class: 'tv-drawing-anchor',
                        cx: x,
                        cy: y,
                        r: 4.5,
                    }));
                });
            }
            updateTVDrawingEditor();
        }

        function tvRestoreDrawings() {
            syncTVUserHLinePriceLines();
            tvRefreshDrawingLevels();
            scheduleTVDrawingOverlayDraw();
        }

        function clearTVDrawingPreview() {
            if (!tvDrawingPreviewDef) return;
            tvDrawingPreviewDef = null;
            scheduleTVDrawingOverlayDraw();
        }

        function finishTVDrawingCreation(def) {
            const normalized = normalizeTVDrawingDef(def);
            if (!normalized) return null;
            tvDrawingDefs.push(normalized);
            persistTVDrawings();
            setDrawMode(null);
            tvSelectedDrawingId = normalized.id;
            tvRestoreDrawings();
            updateTVDrawingEditor();
            return normalized;
        }

        function tvResolveChartPoint(param) {
            if (!tvPriceChart || !tvCandleSeries || !param || !param.point) return null;
            const price = tvCandleSeries.coordinateToPrice(param.point.y);
            if (price === null || price === undefined) return null;
            let logical = tvCoordinateToLogical(param.point.x);
            if (logical == null && param.logical != null) {
                const fallbackLogical = Number(param.logical);
                logical = Number.isFinite(fallbackLogical) ? fallbackLogical : null;
            }
            let time = param.time;
            if (!time) {
                try { time = tvPriceChart.timeScale().coordinateToTime(param.point.x); } catch (e) {}
                if (!time && logical != null) {
                    time = tvLogicalToTime(logical);
                }
                if (!time && tvLastCandles && tvLastCandles.length) {
                    const fallbackLogical = logical != null ? logical : tvLastCandles.length - 1;
                    const idx = Math.max(0, Math.min(Math.round(fallbackLogical), tvLastCandles.length - 1));
                    time = tvLastCandles[idx].time;
                }
            }
            return { price, time, logical };
        }

        function getTVChannelSnappedPoint(startPoint, point, previewPoint = null) {
            if (!getTVChannelAxisSnapEnabled() || !startPoint || !point) return point;
            const startPrice = Number(startPoint.price);
            const nextPrice = Number(point.price);
            const startLogical = Number(startPoint.logical);
            const nextLogical = Number(point.logical);
            if (![startPrice, nextPrice, startLogical, nextLogical].every(Number.isFinite)) {
                return point;
            }
            const snapHorizontal = Math.abs(nextLogical - startLogical) >= Math.abs(nextPrice - startPrice);
            if (snapHorizontal) {
                const snappedY = tvCandleSeries ? tvCandleSeries.priceToCoordinate(startPrice) : null;
                return {
                    ...point,
                    price: startPrice,
                    previewX: previewPoint && Number.isFinite(previewPoint.x) ? previewPoint.x : null,
                    previewY: Number.isFinite(snappedY)
                        ? snappedY
                        : (previewPoint && Number.isFinite(previewPoint.y) ? previewPoint.y : null),
                };
            }
            const snappedX = tvResolveDrawingAnchorX(startPoint.time, startPoint.logical, null);
            return {
                ...point,
                time: startPoint.time,
                logical: startPoint.logical,
                previewX: Number.isFinite(snappedX)
                    ? snappedX
                    : (previewPoint && Number.isFinite(previewPoint.x) ? previewPoint.x : null),
                previewY: previewPoint && Number.isFinite(previewPoint.y) ? previewPoint.y : null,
            };
        }

        function updateTVDrawingPreview(param) {
            if (!tvDrawMode || !tvPriceChart || !tvCandleSeries || !param || !param.point) return;
            const previewPoint = tvResolvePreviewPoint(param);
            const point = tvResolveChartPoint(param);
            if (!previewPoint || !point) return;
            const drawColor = getTVDrawingColorFallback();
            let nextPreview = null;
            if (tvDrawMode === 'hline') {
                nextPreview = normalizeTVDrawingDef({
                    id: '__preview__',
                    type: 'hline',
                    price: previewPoint.price,
                    previewY: previewPoint.y,
                    color: drawColor,
                    lineWidth: 2,
                    lineStyle: 'dashed',
                });
            } else if (tvDrawMode === 'text') {
                nextPreview = normalizeTVDrawingDef({
                    id: '__preview__',
                    type: 'text',
                    price: previewPoint.price,
                    previewX: previewPoint.x,
                    previewY: previewPoint.y,
                    text: 'Label',
                    color: drawColor,
                });
            } else if ((tvDrawMode === 'trendline' || tvDrawMode === 'rect' || tvDrawMode === 'channel') && tvDrawStart) {
                const drawPoints = Array.isArray(tvDrawStart.points) ? tvDrawStart.points : [tvDrawStart];
                if (tvDrawMode === 'trendline' && drawPoints.length === 1) {
                    const startPoint = drawPoints[0];
                    nextPreview = normalizeTVDrawingDef({
                        id: '__preview__',
                        type: 'trendline',
                        t1: startPoint.time,
                        l1: startPoint.logical,
                        p1: startPoint.price,
                        t2: point.time || startPoint.time,
                        l2: point.logical,
                        p2: previewPoint.price,
                        previewX2: previewPoint.x,
                        previewY2: previewPoint.y,
                        color: drawColor,
                        lineWidth: 2,
                        lineStyle: 'dashed',
                    });
                } else if (tvDrawMode === 'rect' && drawPoints.length === 1) {
                    const startPoint = drawPoints[0];
                    nextPreview = normalizeTVDrawingDef({
                        id: '__preview__',
                        type: 'rect',
                        t1: startPoint.time,
                        l1: startPoint.logical,
                        t2: point.time || startPoint.time,
                        l2: point.logical,
                        top: Math.max(startPoint.price, previewPoint.price),
                        bot: Math.min(startPoint.price, previewPoint.price),
                        startPrice: startPoint.price,
                        previewX2: previewPoint.x,
                        previewY2: previewPoint.y,
                        color: drawColor,
                        lineWidth: 2,
                        lineStyle: 'dashed',
                    });
                } else if (tvDrawMode === 'channel') {
                    if (drawPoints.length === 1) {
                        const startPoint = drawPoints[0];
                        const snappedPoint = getTVChannelSnappedPoint(startPoint, point, previewPoint);
                        nextPreview = normalizeTVDrawingDef({
                            id: '__preview__',
                            type: 'trendline',
                            t1: startPoint.time,
                            l1: startPoint.logical,
                            p1: startPoint.price,
                            t2: snappedPoint.time || startPoint.time,
                            l2: snappedPoint.logical,
                            p2: snappedPoint.price,
                            previewX2: Number.isFinite(snappedPoint.previewX) ? snappedPoint.previewX : previewPoint.x,
                            previewY2: Number.isFinite(snappedPoint.previewY) ? snappedPoint.previewY : previewPoint.y,
                            color: drawColor,
                            lineWidth: 2,
                            lineStyle: 'dashed',
                        });
                    } else if (drawPoints.length >= 2) {
                        const first = drawPoints[0];
                        const second = drawPoints[1];
                        nextPreview = normalizeTVDrawingDef({
                            id: '__preview__',
                            type: 'channel',
                            t1: first.time,
                            l1: first.logical,
                            p1: first.price,
                            t2: second.time,
                            l2: second.logical,
                            p2: second.price,
                            t3: point.time || second.time,
                            l3: point.logical,
                            p3: previewPoint.price,
                            previewX3: previewPoint.x,
                            previewY3: previewPoint.y,
                            color: drawColor,
                            lineWidth: 2,
                            lineStyle: 'dashed',
                            showMidline: true,
                        });
                    }
                }
            }
            const previous = JSON.stringify(tvDrawingPreviewDef || null);
            const next = JSON.stringify(nextPreview || null);
            if (previous !== next) {
                tvDrawingPreviewDef = nextPreview;
                scheduleTVDrawingOverlayDraw();
            }
        }

        function closeTVToolbarMenus() {
            if (tvOpenDrawMenuRoot) {
                tvOpenDrawMenuRoot.classList.remove('open');
            }
            if (tvOpenDrawMenuPanel) {
                tvOpenDrawMenuPanel.style.left = '0px';
                tvOpenDrawMenuPanel.style.top = '0px';
            }
            tvOpenDrawMenuRoot = null;
            tvOpenDrawMenuAnchor = null;
            tvOpenDrawMenuPanel = null;
        }

        function positionTVOpenDrawMenu() {
            if (!tvOpenDrawMenuAnchor || !tvOpenDrawMenuPanel || !tvOpenDrawMenuRoot) return;
            const anchorRect = tvOpenDrawMenuAnchor.getBoundingClientRect();
            const panel = tvOpenDrawMenuPanel;
            const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
            const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
            const panelRect = panel.getBoundingClientRect();
            const panelWidth = panelRect.width || 154;
            const panelHeight = panelRect.height || 0;
            const left = Math.max(8, Math.min(viewportWidth - panelWidth - 8, anchorRect.left));
            const preferredTop = anchorRect.bottom + 6;
            const top = (preferredTop + panelHeight + 8 <= viewportHeight)
                ? preferredTop
                : Math.max(8, anchorRect.top - panelHeight - 6);
            panel.style.left = `${Math.round(left)}px`;
            panel.style.top = `${Math.round(top)}px`;
        }

        function openTVToolbarMenu(root, anchor, panel) {
            if (!root || !anchor || !panel) return;
            closeTVToolbarMenus();
            root.classList.add('open');
            tvOpenDrawMenuRoot = root;
            tvOpenDrawMenuAnchor = anchor;
            tvOpenDrawMenuPanel = panel;
            positionTVOpenDrawMenu();
        }

        function bindTVToolbarMenuDismiss() {
            if (tvToolbarMenuDismissBound) return;
            tvToolbarMenuDismissBound = true;
            document.addEventListener('pointerdown', event => {
                if (!tvOpenDrawMenuRoot) return;
                if (tvOpenDrawMenuRoot.contains(event.target)) return;
                closeTVToolbarMenus();
            });
            document.addEventListener('keydown', event => {
                if (event.key === 'Escape') closeTVToolbarMenus();
            });
            window.addEventListener('resize', () => {
                if (!tvOpenDrawMenuRoot) return;
                positionTVOpenDrawMenu();
            });
            document.addEventListener('scroll', () => {
                if (!tvOpenDrawMenuRoot) return;
                positionTVOpenDrawMenu();
            }, true);
        }

        function setDrawMode(mode, options = {}) {
            const shouldToggle = options.force !== true;
            tvDrawMode = shouldToggle && tvDrawMode === mode ? null : mode;
            tvDrawStart = null;
            clearTVDrawingPreview();
            closeTVToolbarMenus();
            const container = document.getElementById('price-chart');
            if (!container) return;
            container.classList.toggle('draw-mode', !!tvDrawMode);
            container.title = '';
            // Sync button states
            document.querySelectorAll('.tv-tb-btn[data-draw]').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.draw === tvDrawMode);
            });
            if (tvDrawMode) {
                tvSelectedDrawingId = null;
                updateTVDrawingEditor();
            }
            scheduleTVDrawingOverlayDraw();
        }

        function tvUndoDrawing() {
            if (!tvDrawingDefs.length) return;
            const removed = tvDrawingDefs.pop();
            if (removed && removed.id === tvSelectedDrawingId) {
                tvSelectedDrawingId = null;
            }
            persistTVDrawings();
            tvRestoreDrawings();
        }

        function tvClearDrawings() {
            tvSelectedDrawingId = null;
            tvDrawStart = null;
            clearTVDrawingPreview();
            tvDrawingDefs = [];
            persistTVDrawings();
            tvRestoreDrawings();
        }

        function tvHandleChartClick(param) {
            if (!tvDrawMode && param && param.point) {
                const indicatorKey = findTVIndicatorHitKey(param);
                if (indicatorKey) {
                    tvSelectedDrawingId = null;
                    updateTVDrawingEditor();
                    scheduleTVDrawingOverlayDraw();
                    openTVIndicatorEditor(indicatorKey);
                    return;
                }
            }
            if (!tvDrawMode || !param || !param.point) {
                if (!tvDrawMode && tvSelectedDrawingId) {
                    tvSelectedDrawingId = null;
                    updateTVDrawingEditor();
                    scheduleTVDrawingOverlayDraw();
                }
                return;
            }
            const point = tvResolveChartPoint(param);
            if (!point) return;
            const { price, time: clickTime, logical } = point;
            const drawColor = getTVDrawingColorFallback(tvDrawMode);

            if (tvDrawMode === 'hline') {
                finishTVDrawingCreation({
                    type: 'hline',
                    price,
                    color: drawColor,
                    lineWidth: 2,
                    lineStyle: 'solid',
                    preset: getActiveTVHLinePresetKey(),
                });
                return;
            }

            if (tvDrawMode === 'trendline' || tvDrawMode === 'rect') {
                if (!clickTime) return; // still no time — bail
                const drawPoints = Array.isArray(tvDrawStart && tvDrawStart.points) ? tvDrawStart.points : [];
                if (!drawPoints.length) {
                    tvDrawStart = { points: [{ price, time: clickTime, logical }] };
                    // Show visual hint that first point is set
                    const container = document.getElementById('price-chart');
                    if (container) { container.title = 'Click second point to complete drawing'; }
                    scheduleTVDrawingOverlayDraw();
                } else {
                    const startPoint = drawPoints[0];
                    if (tvDrawMode === 'trendline') {
                        finishTVDrawingCreation({
                            type: 'trendline',
                            t1: startPoint.time,
                            l1: startPoint.logical,
                            p1: startPoint.price,
                            t2: clickTime,
                            l2: logical,
                            p2: price,
                            color: drawColor,
                            lineWidth: 2,
                            lineStyle: 'solid',
                        });
                    } else if (tvDrawMode === 'rect') {
                        finishTVDrawingCreation({
                            type: 'rect',
                            t1: startPoint.time,
                            l1: startPoint.logical,
                            t2: clickTime,
                            l2: logical,
                            top: Math.max(startPoint.price, price),
                            bot: Math.min(startPoint.price, price),
                            color: drawColor,
                            lineWidth: 2,
                            lineStyle: 'solid',
                        });
                    }
                    tvDrawStart = null;
                    const container = document.getElementById('price-chart');
                    if (container) container.title = '';
                }
                return;
            }

            if (tvDrawMode === 'channel') {
                if (!clickTime) return;
                const drawPoints = Array.isArray(tvDrawStart && tvDrawStart.points) ? tvDrawStart.points.slice() : [];
                const nextPoint = drawPoints.length === 1
                    ? getTVChannelSnappedPoint(drawPoints[0], { price, time: clickTime, logical })
                    : { price, time: clickTime, logical };
                drawPoints.push(nextPoint);
                if (drawPoints.length === 1) {
                    tvDrawStart = { points: drawPoints };
                    const container = document.getElementById('price-chart');
                    if (container) {
                        container.title = getTVChannelAxisSnapEnabled()
                            ? 'Click second point to set the channel trend (Snap HV ON)'
                            : 'Click second point to set the channel trend';
                    }
                    scheduleTVDrawingOverlayDraw();
                    return;
                }
                if (drawPoints.length === 2) {
                    tvDrawStart = { points: drawPoints };
                    const container = document.getElementById('price-chart');
                    if (container) container.title = 'Click third point to set the channel width';
                    scheduleTVDrawingOverlayDraw();
                    return;
                }
                finishTVDrawingCreation({
                    type: 'channel',
                    t1: drawPoints[0].time,
                    l1: drawPoints[0].logical,
                    p1: drawPoints[0].price,
                    t2: drawPoints[1].time,
                    l2: drawPoints[1].logical,
                    p2: drawPoints[1].price,
                    t3: drawPoints[2].time,
                    l3: drawPoints[2].logical,
                    p3: drawPoints[2].price,
                    color: drawColor,
                    lineWidth: 2,
                    lineStyle: 'solid',
                    showMidline: true,
                });
                tvDrawStart = null;
                const container = document.getElementById('price-chart');
                if (container) container.title = '';
                return;
            }

            if (tvDrawMode === 'text') {
                const userText = prompt('Enter label text:');
                if (!userText) return;
                finishTVDrawingCreation({
                    type: 'text',
                    price,
                    time: clickTime,
                    logical,
                    text: userText,
                    color: drawColor,
                });
            }
        }

        // ── Candle Close Timer ─────────────────────────────────────────────────────
        function startCandleCloseTimer() {
            if (candleCloseTimerInterval) clearInterval(candleCloseTimerInterval);
            function updateTimer() {
                const el = document.getElementById('candle-close-timer');
                if (!el) { clearInterval(candleCloseTimerInterval); return; }
                const tfEl = document.getElementById('timeframe');
                const tf = tfEl ? parseInt(tfEl.value) || 1 : 1;
                const tfSecs = tf * 60;
                // Use ET (America/New_York) time for candle boundary calculation
                const now = new Date();
                const etFormatter = new Intl.DateTimeFormat('en-US', {
                    timeZone: 'America/New_York',
                    hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false
                });
                const parts = etFormatter.formatToParts(now);
                let h = 0, m2 = 0, s2 = 0;
                for (const p of parts) {
                    if (p.type === 'hour')   h  = parseInt(p.value);
                    if (p.type === 'minute') m2 = parseInt(p.value);
                    if (p.type === 'second') s2 = parseInt(p.value);
                }
                const secondsOfDay = h * 3600 + m2 * 60 + s2;
                const elapsed = secondsOfDay % tfSecs;
                const remaining = tfSecs - elapsed;
                const minutes = Math.floor(remaining / 60);
                const seconds = remaining % 60;
                el.textContent = `⏱ ${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
            updateTimer();
            candleCloseTimerInterval = setInterval(updateTimer, 1000);
        }

        // ── Build the chart toolbar ──────────────────────────────────────────
        function buildTVToolbar(container, candles, upColor, downColor) {
            const toolbarContainer = document.getElementById('tv-toolbar-container');
            if (!toolbarContainer) return;
            closeTVToolbarMenus();
            toolbarContainer.innerHTML = '';
            const toolbar = toolbarContainer;
            toolbar.className = 'tv-toolbar-container';
            const toolbarMain = document.createElement('div');
            toolbarMain.className = 'tv-toolbar-main';
            const toolbarRight = document.createElement('div');
            toolbarRight.className = 'tv-toolbar-right';
            toolbar.appendChild(toolbarMain);
            toolbar.appendChild(toolbarRight);

            // Use the persistent global set so state survives data refreshes
            // (tvActiveInds is declared at page level)

            function btn(text, title, onClick, extraClass='') {
                const b = document.createElement('button');
                b.className = 'tv-tb-btn' + (extraClass ? ' ' + extraClass : '');
                b.textContent = text;
                b.title = title;
                b.addEventListener('click', onClick);
                return b;
            }

            function addLeft(node) {
                toolbarMain.appendChild(node);
                return node;
            }

            function addRight(node) {
                toolbarRight.appendChild(node);
                return node;
            }

            function makeGroup(kind) {
                const el = document.createElement('div');
                el.className = 'tv-toolbar-group';
                el.dataset.group = kind;
                addLeft(el);
                return el;
            }

            const indicatorsGroup = makeGroup('indicators');
            const drawGroup = makeGroup('draw');
            const actionsGroup = makeGroup('actions');
            bindTVToolbarMenuDismiss();

            function addToGroup(group, node) {
                group.appendChild(node);
                return node;
            }

            // Indicator toggles
            const sessionBtn = btn('Sess Lvls', 'Session levels and Initial Balance', () => {
                const next = !getSessionLevelSettingsFromDom().enabled;
                applySessionLevelSettingsToDom(Object.assign({}, getSessionLevelSettingsFromDom(), { enabled: next }));
                renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
                if (next) {
                    _priceHistoryLastKey = '';
                    fetchPriceHistory(true);
                }
            });
            sessionBtn.dataset.sessionToggle = 'session_levels';
            if (getSessionLevelSettingsFromDom().enabled) sessionBtn.classList.add('active');
            addToGroup(indicatorsGroup, sessionBtn);

            TV_INDICATOR_DEFS.forEach(def => {
                const b = btn(def.label, def.title, () => {
                    setTVIndicatorEnabled(def.key, !tvActiveInds.has(def.key));
                });
                b.dataset.indicatorKey = def.key;
                if (tvActiveInds.has(def.key)) b.classList.add('active');
                addToGroup(indicatorsGroup, b);
            });

            // --- Separator ---
            // Drawing tools
            const hLineWrap = document.createElement('div');
            hLineWrap.className = 'tv-draw-dropdown';
            const hLineButton = btn('— H-Line', 'Toggle horizontal price line drawing', event => {
                event.preventDefault();
                event.stopPropagation();
                setDrawMode('hline');
            });
            hLineButton.id = 'tv-hline-draw-button';
            hLineButton.dataset.draw = 'hline';
            if (tvDrawMode === 'hline') hLineButton.classList.add('active');
            const hLineMenuToggle = btn('', 'Choose H-Line type/color', event => {
                event.preventDefault();
                event.stopPropagation();
                const isOpen = hLineWrap.classList.contains('open');
                closeTVToolbarMenus();
                if (!isOpen) {
                    openTVToolbarMenu(hLineWrap, hLineMenuToggle, hLineMenu);
                }
            }, 'icon');
            hLineMenuToggle.id = 'tv-hline-preset-toggle';
            const hLineMenu = document.createElement('div');
            hLineMenu.className = 'tv-draw-dropdown-menu';
            [
                { key: 'support', title: 'Draw support H-Lines in green' },
                { key: 'resistance', title: 'Draw resistance H-Lines in red' },
                { key: 'neutral', title: 'Draw neutral H-Lines in gray' },
                { key: 'custom', title: 'Use the toolbar color picker for new H-Lines' },
            ].forEach(presetDef => {
                const preset = getTVHLinePreset(presetDef.key);
                const option = document.createElement('button');
                option.type = 'button';
                option.className = 'tv-draw-menu-item';
                option.dataset.hlinePreset = presetDef.key;
                option.title = presetDef.title;
                option.innerHTML =
                    '<span class="tv-draw-pill-swatch" style="background:' + (preset.color || getTVDrawingColorFallback('custom')) + '"></span>' +
                    preset.label;
                option.addEventListener('click', event => {
                    event.preventDefault();
                    event.stopPropagation();
                    setActiveTVHLinePresetKey(presetDef.key);
                    setDrawMode('hline', { force: true });
                });
                hLineMenu.appendChild(option);
            });
            hLineWrap.appendChild(hLineButton);
            hLineWrap.appendChild(hLineMenuToggle);
            hLineWrap.appendChild(hLineMenu);
            addToGroup(drawGroup, hLineWrap);
            syncTVHLineToolbarPreset();
            hLineMenu.querySelectorAll('.tv-draw-menu-item').forEach(node => {
                node.classList.toggle('active', node.dataset.hlinePreset === getActiveTVHLinePresetKey());
            });

            const drawDefs = [
                { key:'trendline', label:'↗ Trend',  title:'Draw trend line (click start, click end)' },
                { key:'channel',   label:'∥ Channel', title:'Draw parallel channel (click two trend points, then width)' },
                { key:'rect',      label:'▭ Box',    title:'Draw rectangle between two prices (click two points)' },
                { key:'text',      label:'T Label',  title:'Add price label (click to place)' },
            ];
            drawDefs.forEach(def => {
                const b = btn(def.label, def.title, () => setDrawMode(def.key));
                b.dataset.draw = def.key;
                if (tvDrawMode === def.key) b.classList.add('active');
                if (def.key === 'channel') {
                    const wrap = document.createElement('div');
                    wrap.className = 'tv-draw-inline';
                    wrap.appendChild(b);
                    const channelSnapBtn = btn('', '', () => {
                        setTVChannelAxisSnapEnabled(!getTVChannelAxisSnapEnabled());
                    }, 'pill');
                    channelSnapBtn.id = 'tv-channel-snap-toggle';
                    wrap.appendChild(channelSnapBtn);
                    addToGroup(drawGroup, wrap);
                    syncTVChannelSnapToolbarButton();
                } else {
                    addToGroup(drawGroup, b);
                }
            });

            // Draw color picker
            const colorWrap = document.createElement('span');
            colorWrap.style.cssText = 'display:flex;align-items:center;gap:3px;';
            const colorLabel = document.createElement('span');
            colorLabel.style.cssText = 'font-size:10px;color:#aaa;';
            colorLabel.textContent = '🎨';
            const colorPicker = document.createElement('input');
            colorPicker.type = 'color';
            colorPicker.id = 'tv-draw-color';
            colorPicker.value = '#FFD700';
            colorPicker.style.cssText = 'width:24px;height:22px;border:none;background:none;cursor:pointer;padding:0;';
            colorPicker.title = 'Drawing color';
            colorPicker.addEventListener('input', () => {
                if (getActiveTVHLinePresetKey() === 'custom') syncTVHLineToolbarPreset();
            });
            colorWrap.appendChild(colorLabel);
            colorWrap.appendChild(colorPicker);
            addToGroup(drawGroup, colorWrap);

            // Undo / Clear
            addToGroup(actionsGroup, btn('↩ Undo', 'Undo last drawing', tvUndoDrawing));
            addToGroup(actionsGroup, btn('✕ Clear', 'Clear all drawings', tvClearDrawings, 'danger'));
            addToGroup(actionsGroup, btn('Indicators', 'Edit built-in indicator styles', openTVIndicatorEditor));
            addToGroup(actionsGroup, btn('Levels', 'Edit key/session level visibility and styles', openPriceLevelEditor));

            // Auto-Range toggle
            const arBtn = document.createElement('button');
            const syncAutoRangeButton = () => {
                arBtn.textContent = tvAutoRange ? '⤢ Auto-Range ON' : '⤢ Auto-Range OFF';
                arBtn.classList.toggle('active', tvAutoRange);
            };
            arBtn.className = 'tv-tb-btn' + (tvAutoRange ? ' active' : '');
            arBtn.title = 'Auto-Range: when ON, the chart fits all candles on every data update. When OFF, your zoom & pan are preserved.';
            syncAutoRangeButton();
            arBtn.addEventListener('click', () => {
                tvAutoRange = !tvAutoRange;
                syncAutoRangeButton();
                if (tvPriceChart) tvFitAll();  // always fit immediately when toggling, ON or OFF
            });
            addRight(arBtn);

            addRight(btn('Today', 'Zoom to the current trading day', () => {
                tvAutoRange = false;
                syncAutoRangeButton();
                tvFocusCurrentSession();
            }));
            addRight(btn('⟳ Reset', 'Reset zoom and pan to fit all data', () => tvFitAll()));

            const volumeStatusEl = document.createElement('span');
            volumeStatusEl.className = 'tv-toolbar-status';
            volumeStatusEl.textContent = 'Vol: 1m confirmed';
            volumeStatusEl.title = 'Intrabar volume is confirmed from 1-minute CHART_EQUITY bars. Quote ticks do not carry live volume.';
            addRight(volumeStatusEl);

            // Candle close timer
            const timerEl = document.createElement('span');
            timerEl.id = 'candle-close-timer';
            timerEl.className = 'candle-close-timer';
            timerEl.title = 'Time remaining until the current candle closes';
            timerEl.textContent = '⏱ --:--';
            addRight(timerEl);
            startCandleCloseTimer();

            // Wire up click handler for drawing
            if (tvPriceChart) {
                tvPriceChart.subscribeClick(tvHandleChartClick);
            }
        }

        function ensureTVHistoricalOverlay() {
            const container = document.getElementById('price-chart');
            if (!container) return null;
            let overlay = container.querySelector('.tv-historical-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'tv-historical-overlay';
                container.appendChild(overlay);
            }
            return overlay;
        }

        function ensureTVHistoricalTooltip() {
            const container = document.getElementById('price-chart');
            if (!container) return null;
            let tooltip = container.querySelector('.tv-historical-tooltip');
            if (!tooltip) {
                tooltip = document.createElement('div');
                tooltip.className = 'tv-historical-tooltip';
                container.appendChild(tooltip);
            }
            return tooltip;
        }

        function formatTVBubbleTime(timestamp) {
            return new Date(timestamp * 1000).toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
                timeZone: 'America/New_York'
            }) + ' ET';
        }

        function buildTVHistoricalTooltipHtml(point) {
            const dotColor = point.border_color || point.color || '#ffffff';
            const name = point.kind === 'expected-move'
                ? point.label + ' ' + point.side
                : point.label + ' ' + point.side.charAt(0);
            const value = point.kind === 'expected-move'
                ? point.value
                : '$' + Number(point.price).toFixed(2) + '  ' + point.value;
            return '<div class="tt-row">'
                + '<span class="tt-dot" style="background:' + dotColor + '"></span>'
                + '<div class="tt-main">'
                + '<span class="tt-name">' + name + '</span>'
                + '<span class="tt-value">' + value + '</span>'
                + '</div>'
                + '</div>';
        }

        function positionTVHistoricalTooltip(tooltip, event) {
            const container = document.getElementById('price-chart');
            if (!tooltip || !container || !event) return;
            const bounds = container.getBoundingClientRect();
            const offsetX = 12;
            const offsetY = 12;
            const left = Math.min(
                Math.max(8, event.clientX - bounds.left + offsetX),
                Math.max(8, bounds.width - tooltip.offsetWidth - 8)
            );
            const top = Math.min(
                Math.max(8, event.clientY - bounds.top + offsetY),
                Math.max(8, bounds.height - tooltip.offsetHeight - 8)
            );
            tooltip.style.left = `${left}px`;
            tooltip.style.top = `${top}px`;
        }

        function findTVHistoricalHoverPoints(event) {
            const container = document.getElementById('price-chart');
            if (!container || !tvHistoricalRenderedPoints.length) return [];
            const bounds = container.getBoundingClientRect();
            const cursorX = event.clientX - bounds.left;
            const cursorY = event.clientY - bounds.top;
            return tvHistoricalRenderedPoints
                .filter(point => {
                    const dx = cursorX - point.x;
                    const dy = cursorY - point.y;
                    const radius = Math.max(8, (point.size || 8) / 2 + 5);
                    return (dx * dx + dy * dy) <= (radius * radius);
                })
                .sort((left, right) => {
                    const leftDist = (cursorX - left.x) ** 2 + (cursorY - left.y) ** 2;
                    const rightDist = (cursorX - right.x) ** 2 + (cursorY - right.y) ** 2;
                    return leftDist - rightDist;
                });
        }

        function updateTVHistoricalTooltip(event) {
            const tooltip = ensureTVHistoricalTooltip();
            if (!tooltip) return;
            const hoverPoints = findTVHistoricalHoverPoints(event);
            if (!hoverPoints.length) {
                tooltip.style.display = 'none';
                return;
            }

            const topPoints = hoverPoints.slice(0, 5);
            const anchorTime = topPoints[0].time;
            tooltip.innerHTML = '<div class="tt-head"><span class="tt-badge">' + hoverPoints.length + ' bubble' + (hoverPoints.length === 1 ? '' : 's') + '</span><div class="tt-time">' + formatTVBubbleTime(anchorTime) + '</div></div>'
                + '<div class="tt-list">' + topPoints.map(point => buildTVHistoricalTooltipHtml(point)).join('') + '</div>'
                + (hoverPoints.length > topPoints.length
                    ? '<div class="tt-more">+' + (hoverPoints.length - topPoints.length) + ' more</div>'
                    : '');
            tooltip.style.display = 'block';
            positionTVHistoricalTooltip(tooltip, event);
        }

        function clearTVHistoricalExpectedMoveSeries() {
            if (!tvPriceChart || !tvHistoricalExpectedMoveSeries.length) return;
            tvHistoricalExpectedMoveSeries.forEach(series => {
                try { tvPriceChart.removeSeries(series); } catch(e) {}
            });
            tvHistoricalExpectedMoveSeries = [];
        }

        function getVisibleTVHistoricalPoints() {
            if (!tvHistoricalPoints.length) return [];

            let visiblePoints = tvHistoricalPoints;
            try {
                const visibleRange = tvPriceChart.timeScale().getVisibleLogicalRange();
                if (visibleRange && tvLastCandles.length) {
                    const leftIndex = Math.max(0, Math.floor(visibleRange.from) - 2);
                    const rightIndex = Math.min(tvLastCandles.length - 1, Math.ceil(visibleRange.to) + 2);
                    const leftCandle = tvLastCandles[leftIndex];
                    const rightCandle = tvLastCandles[rightIndex];
                    if (leftCandle && rightCandle) {
                        const candleSpan = tvLastCandles.length > 1
                            ? Math.max(60, tvLastCandles[1].time - tvLastCandles[0].time)
                            : 60;
                        const minTime = leftCandle.time - (candleSpan * 2);
                        const maxTime = rightCandle.time + (candleSpan * 2);
                        visiblePoints = tvHistoricalPoints.filter(point => point.time >= minTime && point.time <= maxTime);
                    }
                }
            } catch(e) {}

            if (visiblePoints.length <= tvHistoricalOverlayMaxVisible) {
                return visiblePoints;
            }

            const priorityPoints = [];
            const secondaryPoints = [];
            visiblePoints.forEach(point => {
                if (point.kind === 'expected-move' || point.rank === 1) priorityPoints.push(point);
                else secondaryPoints.push(point);
            });

            if (priorityPoints.length >= tvHistoricalOverlayMaxVisible) {
                const stride = Math.ceil(priorityPoints.length / tvHistoricalOverlayMaxVisible);
                return priorityPoints.filter((_, index) => index % stride === 0);
            }

            const secondarySlots = Math.max(0, tvHistoricalOverlayMaxVisible - priorityPoints.length);
            if (!secondaryPoints.length || secondarySlots === 0) {
                return priorityPoints;
            }

            const stride = Math.ceil(secondaryPoints.length / secondarySlots);
            return priorityPoints.concat(secondaryPoints.filter((_, index) => index % stride === 0));
        }

        function drawTVHistoricalOverlay() {
            const overlay = ensureTVHistoricalOverlay();
            const tooltip = ensureTVHistoricalTooltip();
            if (!overlay || !tvPriceChart || !tvCandleSeries) return;

            tvHistoricalRenderedPoints = [];
            if (!tvHistoricalPoints.length) {
                overlay.replaceChildren();
                overlay.style.display = 'none';
                if (tooltip) tooltip.style.display = 'none';
                return;
            }
            if (!isChartVisible('historical_dots')) {
                overlay.replaceChildren();
                overlay.style.display = 'none';
                if (tooltip) tooltip.style.display = 'none';
                return;
            }

            const pointsToRender = getVisibleTVHistoricalPoints();
            if (!pointsToRender.length) {
                overlay.replaceChildren();
                overlay.style.display = 'none';
                if (tooltip) tooltip.style.display = 'none';
                return;
            }

            const fragment = document.createDocumentFragment();
            let visibleCount = 0;
            for (const point of pointsToRender) {
                const x = tvPriceChart.timeScale().timeToCoordinate(point.time);
                const y = tvCandleSeries.priceToCoordinate(point.price);
                if (x == null || y == null || Number.isNaN(x) || Number.isNaN(y)) continue;

                const bubble = document.createElement('div');
                bubble.className = 'tv-historical-bubble';
                bubble.style.left = `${x}px`;
                bubble.style.top = `${y}px`;
                bubble.style.width = `${point.size || 8}px`;
                bubble.style.height = `${point.size || 8}px`;
                bubble.style.background = point.color || 'rgba(255,255,255,0.6)';
                bubble.style.border = `${point.border_width || 1}px solid ${point.border_color || point.color || '#ffffff'}`;
                fragment.appendChild(bubble);
                tvHistoricalRenderedPoints.push({ ...point, x, y });
                visibleCount += 1;
            }

            overlay.replaceChildren(fragment);
            overlay.style.display = visibleCount > 0 ? 'block' : 'none';
        }

        function scheduleTVHistoricalOverlayDraw() {
            if (tvHistoricalOverlayPending) return;
            tvHistoricalOverlayPending = true;
            requestAnimationFrame(() => {
                tvHistoricalOverlayPending = false;
                drawTVEthOverlay();
                drawTVHistoricalOverlay();
            });
        }

        function ensureTVEthOverlay() {
            const container = document.getElementById('price-chart');
            if (!container) return null;
            let canvas = container.querySelector('.tv-eth-overlay');
            if (!canvas) {
                canvas = document.createElement('canvas');
                canvas.className = 'tv-eth-overlay';
                container.appendChild(canvas);
            }
            return canvas;
        }

        // Cached ET-hour/minute formatter — creation is expensive vs. per-tick redraws.
        const _tvEthFmt = new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/New_York',
            hour: '2-digit', minute: '2-digit', hour12: false
        });
        function _tvIsEth(unixSec) {
            const parts = _tvEthFmt.formatToParts(new Date(unixSec * 1000));
            let h = +parts.find(p => p.type === 'hour').value;
            const m = +parts.find(p => p.type === 'minute').value;
            if (h === 24) h = 0;  // Safari/older Intl quirk
            return h < 9 || (h === 9 && m < 30) || h >= 16;
        }

        function drawTVEthOverlay() {
            const canvas = ensureTVEthOverlay();
            const container = document.getElementById('price-chart');
            if (!canvas || !container || !tvPriceChart || !tvLastCandles.length) return;
            const bounds = container.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            if (canvas.width !== bounds.width * dpr || canvas.height !== bounds.height * dpr) {
                canvas.width = bounds.width * dpr;
                canvas.height = bounds.height * dpr;
                canvas.style.width = bounds.width + 'px';
                canvas.style.height = bounds.height + 'px';
            }
            const ctx = canvas.getContext('2d');
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, bounds.width, bounds.height);
            ctx.fillStyle = 'rgba(255, 255, 255, 0.035)';

            const ts = tvPriceChart.timeScale();
            // Group contiguous ETH candles into runs; paint one rect per run.
            let runStart = null, runEnd = null;
            const flush = () => {
                if (runStart === null) return;
                const xs = ts.timeToCoordinate(runStart);
                const xe = ts.timeToCoordinate(runEnd);
                if (xs !== null && xe !== null) {
                    const candleSpan = tvLastCandles.length > 1
                        ? tvLastCandles[1].time - tvLastCandles[0].time
                        : 300;
                    const xeEnd = ts.timeToCoordinate(runEnd + candleSpan);
                    const right = (xeEnd !== null) ? xeEnd : (xe + 2);
                    ctx.fillRect(xs, 0, right - xs, bounds.height);
                }
                runStart = null;
                runEnd = null;
            };
            for (let i = 0; i < tvLastCandles.length; i++) {
                const c = tvLastCandles[i];
                if (_tvIsEth(c.time)) {
                    if (runStart === null) runStart = c.time;
                    runEnd = c.time;
                } else {
                    flush();
                }
            }
            flush();
        }

        function renderTVPriceChart(priceData) {
            const container = document.getElementById('price-chart');
            if (!container) return;

            tvSyncDrawingScope();
            tvLastPriceData = priceData;
            const upColor   = priceData.call_color || '#10B981';
            const downColor = priceData.put_color  || '#EF4444';
            const candles   = priceData.candles || [];

            const lineStyleMap = {
                dashed:       LightweightCharts.LineStyle.Dashed,
                dotted:       LightweightCharts.LineStyle.Dotted,
                large_dashed: LightweightCharts.LineStyle.LargeDashed,
            };

            // ── First render: create the chart and all series once ────────────
            if (!tvPriceChart) {
                // Remove any leftover overlays
                container.querySelectorAll('.tv-chart-title, .tv-indicator-legend').forEach(el => el.remove());
                const _tc = document.getElementById('tv-toolbar-container');
                if (_tc) _tc.innerHTML = '';

                tvPriceChart = LightweightCharts.createChart(container, {
                    autoSize: true,
                    layout: {
                        background: { color: '#1E1E1E' },
                        textColor:   '#CCCCCC',
                        fontFamily:  'Arial, sans-serif',
                    },
                    grid: {
                        vertLines: { color: '#2A2A2A' },
                        horzLines: { color: '#2A2A2A' },
                    },
                    crosshair: {
                        mode: LightweightCharts.CrosshairMode.Normal,
                        vertLine: { color: '#555555', labelBackgroundColor: '#2D2D2D' },
                        horzLine: { color: '#555555', labelBackgroundColor: '#2D2D2D' },
                    },
                    rightPriceScale: {
                        borderColor:  '#333333',
                        scaleMargins: { top: 0.04, bottom: 0.15 },
                    },
                    localization: {
                        timeFormatter: (time) => {
                            const d = new Date(time * 1000);
                            return d.toLocaleTimeString('en-US', {
                                hour: '2-digit', minute: '2-digit',
                                hour12: false, timeZone: 'America/New_York'
                            });
                        }
                    },
                    timeScale: {
                        borderColor:      '#333333',
                        timeVisible:      true,
                        secondsVisible:   false,
                        fixLeftEdge:      false,
                        fixRightEdge:     false,
                        // TickMarkType: 0=Year, 1=Month, 2=DayOfMonth, 3=Time, 4=TimeWithSeconds.
                        // Show dates ("Apr 15") at day/month/year boundaries so multi-day views
                        // are readable without vertical separators.
                        tickMarkFormatter: (time, tickMarkType) => {
                            const d = new Date(time * 1000);
                            if (tickMarkType === 0 || tickMarkType === 1 || tickMarkType === 2) {
                                return d.toLocaleDateString('en-US', {
                                    month: 'short', day: 'numeric',
                                    timeZone: 'America/New_York'
                                });
                            }
                            return d.toLocaleTimeString('en-US', {
                                hour: '2-digit', minute: '2-digit',
                                hour12: false, timeZone: 'America/New_York'
                            });
                        }
                    },
                    handleScale:  { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
                    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
                });

                tvCandleSeries = tvPriceChart.addCandlestickSeries({
                    upColor, downColor,
                    borderVisible: false,
                    wickUpColor:   upColor,
                    wickDownColor: downColor,
                });

                tvVolumeSeries = tvPriceChart.addHistogramSeries({
                    priceFormat:  { type: 'volume' },
                    priceScaleId: 'volume',
                    lastValueVisible: false,
                    priceLineVisible: false,
                });
                tvPriceChart.priceScale('volume').applyOptions({
                    scaleMargins: { top: 0.88, bottom: 0 },
                });

                // Toolbar + title (only built once)
                buildTVToolbar(container, candles, upColor, downColor);
                ensureTVDrawingOverlay();
                ensureSessionLevelCloudOverlay();
                ensureTVDrawingEditor();
                ensureTVHistoricalOverlay();
                tvPriceChart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                    scheduleSessionLevelCloudDraw();
                    scheduleTVDrawingOverlayDraw();
                    scheduleTVHistoricalOverlayDraw();
                    scheduleGexPanelSync();
                });
                if (!tvHistoricalOverlayDomEventsBound) {
                    tvHistoricalOverlayDomEventsBound = true;
                    container.addEventListener('wheel',    () => { scheduleSessionLevelCloudDraw(); scheduleTVHistoricalOverlayDraw(); scheduleGexPanelSync(); }, { passive: true });
                    container.addEventListener('mouseup',  () => { scheduleSessionLevelCloudDraw(); scheduleTVHistoricalOverlayDraw(); scheduleGexPanelSync(); });
                    container.addEventListener('touchend', () => { scheduleSessionLevelCloudDraw(); scheduleTVHistoricalOverlayDraw(); scheduleGexPanelSync(); }, { passive: true });
                    container.addEventListener('mousemove', (event) => updateTVHistoricalTooltip(event));
                    container.addEventListener('mouseleave', () => {
                        if (tvDrawMode) clearTVDrawingPreview();
                        const tooltip = ensureTVHistoricalTooltip();
                        if (tooltip) tooltip.style.display = 'none';
                    });
                }
                const _tc2 = document.getElementById('tv-toolbar-container');
                if (_tc2) {
                    const titleEl = document.createElement('div');
                    titleEl.className = 'tv-chart-title';
                    titleEl.textContent = priceData.use_heikin_ashi ? 'Price Chart (Heikin-Ashi)' : 'Price Chart';
                    _tc2.insertBefore(titleEl, _tc2.firstChild);
                }

                // ── OHLC hover tooltip ────────────────────────────────────
                const _tip = document.createElement('div');
                _tip.className = 'tv-ohlc-tooltip';
                _tip.id = 'tv-ohlc-tooltip';
                container.appendChild(_tip);

                tvPriceChart.subscribeCrosshairMove(function(param) {
                    const tip = document.getElementById('tv-ohlc-tooltip');
                    updateTVDrawingPreview(param);
                    if (tvDrawMode) {
                        if (tip) tip.style.display = 'none';
                        return;
                    }
                    if (!tip) return;
                    if (!param || !param.time || !param.seriesData) {
                        tip.style.display = 'none'; return;
                    }
                    const bar = param.seriesData.get(tvCandleSeries);
                    if (!bar) { tip.style.display = 'none'; return; }
                    const d = new Date(param.time * 1000);
                    const timeStr = d.toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'America/New_York'}) + ' ET';
                    const isUp = bar.close >= bar.open;
                    const cls = isUp ? 'tt-up' : 'tt-dn';
                    const chg = bar.open !== 0 ? ((bar.close - bar.open) / bar.open * 100).toFixed(2) : '0.00';
                    const fmt = v => v != null ? v.toFixed(2) : '--';
                    const fmtVol = v => v >= 1e6 ? (v/1e6).toFixed(2)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : (v||0).toString();
                    tip.innerHTML =
                        '<div class="tt-time">'+timeStr+'</div>'
                        +'<span class="'+cls+'">'
                        +'O <b>'+fmt(bar.open)+'</b>  '
                        +'H <b>'+fmt(bar.high)+'</b>  '
                        +'L <b>'+fmt(bar.low)+'</b>  '
                        +'C <b>'+fmt(bar.close)+'</b>  '
                        +(chg>=0?'+':'')+chg+'%'
                        +'</span>'
                        +'<br><span style="color:#888">Vol <b>'+fmtVol(bar.volume)+'</b></span>';
                    tip.style.display = 'block';
                });
            }

            // ── Every render: update data and overlays in place ───────────────

            // Update candle colors in case they changed
            tvCandleSeries.applyOptions({
                upColor, downColor,
                wickUpColor: upColor, wickDownColor: downColor,
            });

            const isFirstRender = !tvLastCandles.length;

            tvCandleSeries.setData(candles);
            tvLastCandles = candles;
            tvVolumeSeries.setData(priceData.volume || []);
            // Use multi-day candles for indicator warmup so SMA200, EMA200, etc. start from day open
            tvIndicatorCandles = (priceData.indicator_candles && priceData.indicator_candles.length > 0)
                ? priceData.indicator_candles : candles;
            tvCurrentDayStartTime = priceData.current_day_start_time || 0;

            // Start/maintain real-time streaming for the current ticker
            const streamTicker = (document.getElementById('ticker').value || '').trim();
            if (streamTicker && isStreaming) {
                connectPriceStream(streamTicker);
            }

            // Remove old dynamic price lines; historical levels now render only as bubbles.
            tvExposurePriceLines.forEach(l => { try { tvCandleSeries.removePriceLine(l); } catch(e){} });
            tvExposurePriceLines = [];
            tvExpectedMovePriceLines.forEach(l => { try { tvCandleSeries.removePriceLine(l); } catch(e){} });
            tvExpectedMovePriceLines = [];
            clearTVHistoricalExpectedMoveSeries();

            tvHistoricalPoints = priceData.historical_exposure_levels || [];
            tvRestoreDrawings();
            scheduleSessionLevelCloudDraw();
            scheduleTVHistoricalOverlayDraw();
            if (tvActiveInds.size > 0) applyIndicators(tvIndicatorCandles, tvActiveInds);
            renderKeyLevels(getScopedKeyLevels());
            renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
            tvRefreshOverlayLevelPrices();
            // Re-sync GEX side panel after candles + autoscale settle
            scheduleGexPanelSync();

            const shouldFitAll = tvAutoRange || tvForceFit;
            const shouldFocusSession = !shouldFitAll && (isFirstRender || tvForceSessionFocus);
            if (shouldFitAll) {
                tvYAxisMode = 'fit-all';
                const _chart = tvPriceChart;
                setTimeout(() => {
                    try {
                        _chart.timeScale().fitContent();
                        _chart.priceScale('right').applyOptions({ autoScale: true });
                        tvApplyAutoscale();
                        if (tvRsiChart)  tvRsiChart.priceScale('right').applyOptions({ autoScale: true });
                        if (tvMacdChart) tvMacdChart.priceScale('right').applyOptions({ autoScale: true });
                        scheduleSessionLevelCloudDraw();
                        scheduleTVHistoricalOverlayDraw();
                    } catch(e) {}
                }, 50);
            } else if (shouldFocusSession) {
                tvFocusCurrentSession();
            }
            tvForceFit = false;
            tvForceSessionFocus = false;
        }
        // ─────────────────────────────────────────────────────────────────────

        function buildFlowEventLaneHtml() {
            return (
                '<div class="flow-event-lane" id="flow-event-lane">' +
                    '<div class="flow-event-strip" id="flow-event-strip-alerts">' +
                        '<div class="flow-event-strip-head">' +
                            '<div class="flow-event-strip-title-row">' +
                                '<div class="flow-event-strip-title">Live Alerts</div>' +
                                '<div class="flow-event-strip-note" id="rail-alerts-title-note">Mixed Lean</div>' +
                            '</div>' +
                        '</div>' +
                        '<div class="rail-alerts-list flow-event-list" id="right-rail-alerts">' +
                            '<div class="rail-alerts-empty">No active alerts.</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="flow-event-strip" id="flow-event-strip-pulse">' +
                        '<div class="flow-event-strip-head">' +
                            '<div class="flow-event-strip-title-row">' +
                                '<div class="flow-event-strip-title">Flow Pulse</div>' +
                                '<div class="flow-event-strip-note" id="rail-flow-pulse-note">Mixed Lean</div>' +
                            '</div>' +
                        '</div>' +
                        '<div class="rail-pulse-list flow-event-list" id="rail-flow-pulse">' +
                            '<div class="rail-pulse-empty">Pulse data builds after a minute of live flow history.</div>' +
                        '</div>' +
                    '</div>' +
                '</div>'
            );
        }

        function pruneDuplicateFlowEventMarkup(grid = document.getElementById('chart-grid')) {
            const root = grid || document;
            const lanes = Array.from(root.querySelectorAll('.flow-event-lane'));
            lanes.slice(1).forEach(extra => extra.remove());
            ['flow-event-lane', 'right-rail-alerts', 'rail-flow-pulse'].forEach(id => {
                const nodes = Array.from(document.querySelectorAll('#' + id));
                nodes.slice(1).forEach(extra => extra.remove());
            });
            return lanes[0] || document.getElementById('flow-event-lane') || null;
        }

        function ensureFlowEventLane(grid = document.getElementById('chart-grid')) {
            if (!grid) return null;
            let lane = pruneDuplicateFlowEventMarkup(grid);
            if (!lane || !grid.contains(lane)) {
                if (lane && lane.parentNode) lane.parentNode.removeChild(lane);
                grid.insertAdjacentHTML('beforeend', buildFlowEventLaneHtml());
                lane = pruneDuplicateFlowEventMarkup(grid);
            }
            const anchor = grid.querySelector('#right-rail-panels') || grid.querySelector('.price-chart-container');
            if (lane && anchor && lane.previousElementSibling !== anchor) {
                anchor.insertAdjacentElement('afterend', lane);
            }
            return lane;
        }

        // Single source of truth for the overview-panel markup. Both the initial
        // server-rendered HTML and ensurePriceChartDom's rebuild path must
        // produce identical DOM, or tick rebuilds (ticker switch) drop the
        // cards added in Phase 3. Mirror any change here in the Python HTML.
        function buildAlertsPanelHtml() {
            return (
                '<div class="right-rail-panel active" data-rail-panel="overview">' +
                    '<div class="rail-card" id="rail-card-price">' +
                        '<div class="rail-card-price-big" data-live-price>—</div>' +
                        '<div class="rail-card-price-sub">' +
                            '<span class="chg" data-met="price_change">—</span>' +
                            '<span class="rail-card-chip" data-met="expiry_chip">—</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-metrics">' +
                        '<div class="rail-metric-pair">' +
                            '<div class="rail-metric">' +
                                '<div class="rail-card-header">Net GEX</div>' +
                                '<div class="v" data-met="net_gex">—</div>' +
                                '<div class="d" data-met="net_gex_delta"></div>' +
                            '</div>' +
                            '<div class="rail-metric">' +
                                '<div class="rail-card-header">Net DEX</div>' +
                                '<div class="v" data-met="net_dex">—</div>' +
                                '<div class="d" data-met="net_dex_delta"></div>' +
                            '</div>' +
                        '</div>' +
                        '<div class="gex-scope-pill" id="gex-scope-pill">' +
                            '<button class="gex-scope-btn" data-scope="all">All</button>' +
                            '<button class="gex-scope-btn" data-scope="0dte">0DTE</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-range">' +
                        '<div class="rail-card-header-row">' +
                            '<div class="rail-card-header">Expected Move <span data-met="em_pct"></span></div>' +
                            '<div class="rail-card-note" data-met="em_type">ATM straddle</div>' +
                        '</div>' +
                        '<div class="rail-range-value" data-met="em_band_label">—</div>' +
                        '<div class="rail-range-track">' +
                            '<div class="rail-range-em" data-met="em_band"></div>' +
                            '<div class="rail-range-marker" data-met="price_marker"></div>' +
                        '</div>' +
                        '<div class="rail-range-labels">' +
                            '<span data-met="range_low">—</span>' +
                            '<span data-met="range_high">—</span>' +
                        '</div>' +
                        '<div class="rail-range-caption" data-met="em_context">Uses the current ATM straddle, not flow alone.</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-profile">' +
                        '<div class="rail-card-header">Gamma Profile</div>' +
                        '<div class="rail-profile-headline">' +
                            '<span class="rail-profile-dot" data-met="profile_dot"></span>' +
                            '<span data-met="profile_headline">—</span>' +
                        '</div>' +
                        '<div class="rail-profile-blurb" data-met="profile_blurb">—</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-dealer">' +
                        '<div class="rail-card-header-row">' +
                            '<div class="rail-card-header">Dealer Impact</div>' +
                            '<div class="rail-card-note">Hedge response</div>' +
                        '</div>' +
                        '<div class="dealer-impact" id="dealer-impact">' +
                            '<div class="dealer-impact-overview">' +
                                '<div class="dealer-impact-overview-head">' +
                                    '<div class="dealer-impact-overview-label">Combined read</div>' +
                                    '<div class="dealer-impact-overview-chip" data-met="dealer_conviction">—</div>' +
                                '</div>' +
                                '<div class="dealer-impact-overview-title" data-met="dealer_headline">—</div>' +
                                '<div class="dealer-impact-overview-sub" data-met="dealer_subhead">—</div>' +
                            '</div>' +
                            '<div class="dealer-impact-legend">' +
                                '<span class="pos">+ buy to hedge</span>' +
                                '<span class="neg">- sell to hedge</span>' +
                            '</div>' +
                            '<div class="dealer-impact-row">' +
                                '<div class="dealer-impact-copy"><div class="label">Spot +1%</div><div class="sub">hedge flow if spot lifts 1%</div></div>' +
                                '<div class="dealer-impact-read">' +
                                    '<div class="val" data-di="hedge_on_up_1pct">—</div>' +
                                    '<div class="dealer-impact-cue" data-di-cue="hedge_on_up_1pct">—</div>' +
                                '</div>' +
                            '</div>' +
                            '<div class="dealer-impact-row">' +
                                '<div class="dealer-impact-copy"><div class="label">Spot −1%</div><div class="sub">hedge flow if spot drops 1%</div></div>' +
                                '<div class="dealer-impact-read">' +
                                    '<div class="val" data-di="hedge_on_down_1pct">—</div>' +
                                    '<div class="dealer-impact-cue" data-di-cue="hedge_on_down_1pct">—</div>' +
                                '</div>' +
                            '</div>' +
                            '<div class="dealer-impact-row">' +
                                '<div class="dealer-impact-copy"><div class="label">Vol +1 pt</div><div class="sub">delta shift from a 1-point IV rise</div></div>' +
                                '<div class="dealer-impact-read">' +
                                    '<div class="val" data-di="vanna_up_1">—</div>' +
                                    '<div class="dealer-impact-cue" data-di-cue="vanna_up_1">—</div>' +
                                '</div>' +
                            '</div>' +
                            '<div class="dealer-impact-row">' +
                                '<div class="dealer-impact-copy"><div class="label">Vol −1 pt</div><div class="sub">delta shift from a 1-point IV drop</div></div>' +
                                '<div class="dealer-impact-read">' +
                                    '<div class="val" data-di="vanna_down_1">—</div>' +
                                    '<div class="dealer-impact-cue" data-di-cue="vanna_down_1">—</div>' +
                                '</div>' +
                            '</div>' +
                            '<div class="dealer-impact-row">' +
                                '<div class="dealer-impact-copy"><div class="label">Charm by close</div><div class="sub">delta bleed projected into 16:00 ET</div></div>' +
                                '<div class="dealer-impact-read">' +
                                    '<div class="val" data-di="charm_by_close">—</div>' +
                                    '<div class="dealer-impact-cue" data-di-cue="charm_by_close">—</div>' +
                                '</div>' +
                            '</div>' +
                            '<div class="dealer-impact-summary" data-met="dealer_takeaway">Positive values indicate dealer buying to hedge; negative values indicate dealer selling to hedge.</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-activity">' +
                        '<div class="rail-card-header">Chain Activity</div>' +
                        '<div class="rail-activity-bias">' +
                            '<span class="rail-activity-bias-label">Bias</span>' +
                            '<span class="rail-activity-bias-value" data-met="activity_bias">—</span>' +
                        '</div>' +
                        '<div class="rail-sentiment-labels"><span>bearish</span><span>bullish</span></div>' +
                        '<div class="rail-sentiment-track">' +
                            '<div class="rail-sentiment-marker" data-met="sentiment_marker"></div>' +
                        '</div>' +
                        '<div class="rail-bar rail-bar-rich">' +
                            '<span>OI</span>' +
                            '<div>' +
                                '<div class="rail-bar-track"><div class="rail-bar-fill" data-met="oi_fill"></div></div>' +
                                '<div class="rail-bar-split" data-met="oi_split">—</div>' +
                            '</div>' +
                            '<span class="num" data-met="oi_cp">—</span>' +
                        '</div>' +
                        '<div class="rail-bar rail-bar-rich">' +
                            '<span>VOL</span>' +
                            '<div>' +
                                '<div class="rail-bar-track"><div class="rail-bar-fill" data-met="vol_fill"></div></div>' +
                                '<div class="rail-bar-split" data-met="vol_split">—</div>' +
                            '</div>' +
                            '<span class="num" data-met="vol_cp">—</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-iv">' +
                        '<div class="rail-card-header-row">' +
                            '<div class="rail-card-header">Skew / IV</div>' +
                            '<div class="rail-card-note" data-met="iv_expiry">Near expiry</div>' +
                        '</div>' +
                        '<div class="rail-iv-top">' +
                            '<div class="rail-iv-atm" data-met="iv_atm">—</div>' +
                            '<div class="rail-iv-headline" data-met="iv_headline">IV context unavailable</div>' +
                        '</div>' +
                        '<div class="rail-iv-blurb" data-met="iv_blurb">Need implied volatility on the near expiry to build a skew read.</div>' +
                        '<div class="rail-iv-grid">' +
                            '<div class="rail-iv-stat"><span class="rail-iv-stat-label">ATM Call</span><span class="rail-iv-stat-value" data-met="iv_atm_call">—</span></div>' +
                            '<div class="rail-iv-stat"><span class="rail-iv-stat-label">ATM Put</span><span class="rail-iv-stat-value" data-met="iv_atm_put">—</span></div>' +
                            '<div class="rail-iv-stat"><span class="rail-iv-stat-label">Put Wing</span><span class="rail-iv-stat-value" data-met="iv_put_wing">—</span></div>' +
                            '<div class="rail-iv-stat"><span class="rail-iv-stat-label">Call Wing</span><span class="rail-iv-stat-value" data-met="iv_call_wing">—</span></div>' +
                            '<div class="rail-iv-stat"><span class="rail-iv-stat-label">Put-Call</span><span class="rail-iv-stat-value" data-met="iv_skew_spread">—</span></div>' +
                            '<div class="rail-iv-stat"><span class="rail-iv-stat-label">Since Open</span><span class="rail-iv-stat-value" data-met="iv_skew_change">—</span></div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="rail-card" id="rail-card-centroid">' +
                        '<div class="rail-card-header-row">' +
                            '<div class="rail-card-header">Centroid Drift</div>' +
                            '<div class="rail-card-note" data-met="centroid_status">Current session</div>' +
                        '</div>' +
                        '<div class="rail-centroid-meta">' +
                            '<span data-met="centroid_time">—</span>' +
                            '<span data-met="centroid_spread">—</span>' +
                        '</div>' +
                        '<div class="rail-centroid-sparkline" data-centroid-sparkline>' +
                            '<div class="rail-centroid-empty">Centroid data loads with stream data.</div>' +
                        '</div>' +
                        '<div class="rail-centroid-legend">' +
                            '<span><i class="call"></i>Call</span>' +
                            '<span><i class="price"></i>Spot</span>' +
                            '<span><i class="put"></i>Put</span>' +
                        '</div>' +
                        '<div class="rail-centroid-stats">' +
                            '<div class="rail-centroid-stat">' +
                                '<span class="label">Call centroid</span>' +
                                '<span class="value" data-met="centroid_call_strike">—</span>' +
                                '<span class="subvalue" data-met="centroid_call_delta">—</span>' +
                            '</div>' +
                            '<div class="rail-centroid-stat">' +
                                '<span class="label">Put centroid</span>' +
                                '<span class="value" data-met="centroid_put_strike">—</span>' +
                                '<span class="subvalue" data-met="centroid_put_delta">—</span>' +
                            '</div>' +
                        '</div>' +
                        '<div class="rail-centroid-drift-row">' +
                            '<span data-met="centroid_call_drift">—</span>' +
                            '<span data-met="centroid_put_drift">—</span>' +
                        '</div>' +
                        '<div class="rail-centroid-reads">' +
                            '<div class="rail-centroid-read" data-met="centroid_structure">—</div>' +
                            '<div class="rail-centroid-read" data-met="centroid_drift_read">—</div>' +
                        '</div>' +
                    '</div>' +
                '</div>'
            );
        }

        // Rebuild missing chart-grid children in the canonical Stage-5 order.
        // The initial HTML markup already includes all of these; this defensive
        // path only kicks in if price-chart-container was removed from the DOM.
        function ensurePriceChartDom() {
            const grid = document.getElementById('chart-grid');
            if (!grid) return null;
            let priceContainer = grid.querySelector('.price-chart-container');
            if (priceContainer) {
                ensureStrikeRailResizeHandle(grid);
                ensureFlowEventLane();
                return priceContainer;
            }

            let toolbarShell = grid.querySelector('.workspace-toolbar-shell');
            if (!toolbarShell) {
                toolbarShell = document.createElement('div');
                toolbarShell.className = 'workspace-toolbar-shell';
                toolbarShell.id = 'workspace-toolbar-shell';
                grid.appendChild(toolbarShell);
            }
            let drawerToggle = toolbarShell.querySelector('#drawerToggle');
            if (!drawerToggle) {
                drawerToggle = document.createElement('button');
                drawerToggle.className = 'btn-icon workspace-drawer-toggle';
                drawerToggle.id = 'drawerToggle';
                drawerToggle.title = 'Open settings drawer';
                drawerToggle.setAttribute('aria-label', 'Open settings');
                drawerToggle.innerHTML = '&#9776;';
                toolbarShell.appendChild(drawerToggle);
                wireDrawerToggle(drawerToggle);
            }
            let toolbar = toolbarShell.querySelector('.tv-toolbar-container');
            if (!toolbar) {
                toolbar = document.createElement('div');
                toolbar.className = 'tv-toolbar-container';
                toolbar.id = 'tv-toolbar-container';
                toolbarShell.appendChild(toolbar);
            }
            let gexHeader = grid.querySelector('.gex-col-header');
            if (!gexHeader) {
                gexHeader = document.createElement('div');
                gexHeader.className = 'gex-col-header';
                gexHeader.id = 'gex-col-header';
                gexHeader.innerHTML =
                    '<div class="strike-rail-header-main">' +
                        '<div class="gex-col-title">Strike Rail</div>' +
                        '<div class="strike-rail-tabs" id="strike-rail-tabs"></div>' +
                    '</div>' +
                    '<button type="button" class="gex-col-toggle" id="gex-col-toggle" title="Collapse">‹</button>';
                grid.appendChild(gexHeader);
                wireGexColumnToggle();
            }
            ensureStrikeRailResizeHandle(grid);
            if (!document.getElementById('strike-rail-tabs')) {
                const main = gexHeader.querySelector('.strike-rail-header-main');
                if (main) {
                    const tabs = document.createElement('div');
                    tabs.className = 'strike-rail-tabs';
                    tabs.id = 'strike-rail-tabs';
                    main.appendChild(tabs);
                }
            }
            applyStrikeRailTabs();
            let tabs = grid.querySelector('.right-rail-tabs');
            if (!tabs) {
                tabs = document.createElement('div');
                tabs.className = 'right-rail-tabs';
                tabs.id = 'right-rail-tabs';
                tabs.innerHTML =
                    '<button type="button" class="right-rail-tab active" data-rail-tab="overview">Overview<span class="tab-badge" id="right-rail-alerts-badge"></span></button>' +
                    '<button type="button" class="right-rail-tab" data-rail-tab="levels">Levels</button>' +
                    '<button type="button" class="right-rail-tab" data-rail-tab="scenarios">Scenarios</button>';
                grid.appendChild(tabs);
                wireRightRailTabs();
            }
            priceContainer = document.createElement('div');
            priceContainer.className = 'price-chart-container';
            const chartDiv = document.createElement('div');
            chartDiv.className = 'chart-container';
            chartDiv.id = 'price-chart';
            const rsiPane = document.createElement('div');
            rsiPane.className = 'tv-sub-pane'; rsiPane.id = 'rsi-pane'; rsiPane.style.display = 'none';
            rsiPane.innerHTML = '<div class="tv-sub-pane-header">RSI 14</div><div id="rsi-chart" style="height:110px"></div>';
            const macdPane = document.createElement('div');
            macdPane.className = 'tv-sub-pane'; macdPane.id = 'macd-pane'; macdPane.style.display = 'none';
            macdPane.innerHTML = '<div class="tv-sub-pane-header">MACD (12,26,9)</div><div id="macd-chart" style="height:120px"></div>';
            priceContainer.appendChild(chartDiv);
            priceContainer.appendChild(rsiPane);
            priceContainer.appendChild(macdPane);
            grid.appendChild(priceContainer);

            let gexCol = grid.querySelector('.gex-column');
            if (!gexCol) {
                gexCol = document.createElement('div');
                gexCol.className = 'gex-column';
                gexCol.id = 'gex-column';
                gexCol.innerHTML = '<div class="gex-side-panel-wrap"><div id="gex-side-panel"></div></div>';
                grid.appendChild(gexCol);
            }
            let railPanels = grid.querySelector('.right-rail-panels');
            if (!railPanels) {
                railPanels = document.createElement('div');
                railPanels.className = 'right-rail-panels';
                railPanels.id = 'right-rail-panels';
                railPanels.innerHTML =
                    buildAlertsPanelHtml() +
                    '<div class="right-rail-panel" data-rail-panel="levels">' +
                        '<div class="rail-levels-table" id="right-rail-levels">' +
                            '<div class="lvl-empty">Key levels load with stream data.</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="right-rail-panel" data-rail-panel="scenarios">' +
                        '<div class="scenario-table-wrap">' +
                            '<table class="scenario-table" id="scenario-table">' +
                                '<thead><tr><th>Scenario</th><th class="num">Net GEX</th><th>Regime</th></tr></thead>' +
                                '<tbody><tr><td colspan="3" class="scn-empty">Scenarios load with stream data.</td></tr></tbody>' +
                            '</table>' +
                        '</div>' +
                    '</div>';
                grid.appendChild(railPanels);
                wireGexScopePill();
                redrawGexScope();
                applyRightRailTab();
            }
            ensureFlowEventLane();
            renderStrikeRailPanel();
            return priceContainer;
        }

        function showPriceChartUI() {
            const ids = ['workspace-toolbar-shell', 'tv-toolbar-container', 'gex-col-header', 'gex-resize-handle', 'gex-column', 'right-rail-tabs', 'right-rail-panels', 'flow-event-lane'];
            ids.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = ''; });
            const pc = document.querySelector('.price-chart-container');
            if (pc) pc.style.display = 'block';
        }

        // ── GEX column collapse state ────────────────────────────────────
        const GEX_COL_COLLAPSE_KEY = 'gex.sidePanelCollapsed';
        let _gexResizeRefreshScheduled = false;
        function getGexColWidthConstraints() {
            const grid = document.getElementById('chart-grid');
            if (!grid) return { min: 280, max: 640 };
            const styles = getComputedStyle(grid);
            const railWidth = parseFloat(styles.getPropertyValue('--rail-col-w')) || 272;
            const min = 280;
            const max = Math.max(min, Math.min(640, grid.clientWidth - railWidth - 360));
            return { min, max };
        }
        function clampGexColWidth(width) {
            const { min, max } = getGexColWidthConstraints();
            return Math.max(min, Math.min(max, width));
        }
        function applyGexColWidth(width, persist = false) {
            const grid = document.getElementById('chart-grid');
            if (!grid || !Number.isFinite(width)) return;
            const clamped = clampGexColWidth(width);
            grid.style.setProperty('--gex-col-w', clamped + 'px');
            if (!persist) return;
            try { localStorage.setItem(GEX_COL_WIDTH_KEY, String(Math.round(clamped))); } catch (e) {}
        }
        function scheduleGexResizeRefresh() {
            if (_gexResizeRefreshScheduled) return;
            _gexResizeRefreshScheduled = true;
            requestAnimationFrame(() => {
                _gexResizeRefreshScheduled = false;
                const target = getStrikeRailTarget();
                if (target && target._fullLayout) {
                    try { Plotly.Plots.resize(target); } catch (e) {}
                }
                scheduleGexPanelSync();
                try { window.dispatchEvent(new Event('resize')); } catch (e) {}
            });
        }
        function ensureStrikeRailResizeHandle(grid = document.getElementById('chart-grid')) {
            if (!grid) return null;
            let handle = document.getElementById('gex-resize-handle');
            if (!handle) {
                handle = document.createElement('div');
                handle.className = 'gex-resize-handle';
                handle.id = 'gex-resize-handle';
                handle.setAttribute('role', 'separator');
                handle.setAttribute('aria-label', 'Resize strike rail');
                handle.setAttribute('aria-orientation', 'vertical');
                grid.appendChild(handle);
            }
            wireStrikeRailResizeHandle(handle);
            return handle;
        }
        function isGexColumnCollapsed() {
            const grid = document.getElementById('chart-grid');
            return !!(grid && grid.classList.contains('gex-collapsed'));
        }
        function applyGexColumnCollapse(collapsed) {
            const grid = document.getElementById('chart-grid');
            const btn  = document.getElementById('gex-col-toggle');
            if (!grid) return;
            grid.classList.toggle('gex-collapsed', !!collapsed);
            if (btn) {
                btn.textContent = collapsed ? '›' : '‹';
                btn.title = collapsed ? 'Expand Strike Rail' : 'Collapse Strike Rail';
            }
            if (!collapsed) {
                // Re-render the active strike rail after expanding.
                const target = getStrikeRailTarget();
                renderStrikeRailPanel();
                if (target) { try { Plotly.Plots.resize(target); } catch (e) {} }
                scheduleGexPanelSync();
            }
            // Notify TV chart the container width changed so candles reflow
            try { window.dispatchEvent(new Event('resize')); } catch (e) {}
        }
        function wireGexColumnToggle() {
            const btn = document.getElementById('gex-col-toggle');
            if (!btn || btn.__wired) return;
            btn.__wired = true;
            btn.addEventListener('click', () => {
                const next = !isGexColumnCollapsed();
                try { localStorage.setItem(GEX_COL_COLLAPSE_KEY, next ? '1' : '0'); } catch (e) {}
                applyGexColumnCollapse(next);
            });
        }
        function wireStrikeRailResizeHandle(handle = document.getElementById('gex-resize-handle')) {
            if (!handle || handle.__wired) return;
            handle.__wired = true;
            handle.addEventListener('pointerdown', (event) => {
                if (isGexColumnCollapsed()) return;
                const grid = document.getElementById('chart-grid');
                if (!grid) return;
                event.preventDefault();
                const startX = event.clientX;
                const startWidth = parseFloat(getComputedStyle(grid).getPropertyValue('--gex-col-w')) || 352;
                handle.classList.add('dragging');
                document.body.classList.add('gex-resize-active');
                try { handle.setPointerCapture(event.pointerId); } catch (e) {}

                const onMove = (moveEvent) => {
                    const nextWidth = startWidth + (startX - moveEvent.clientX);
                    applyGexColWidth(nextWidth, false);
                    scheduleGexResizeRefresh();
                };
                const onUp = (upEvent) => {
                    document.removeEventListener('pointermove', onMove);
                    document.removeEventListener('pointerup', onUp);
                    document.removeEventListener('pointercancel', onUp);
                    handle.classList.remove('dragging');
                    document.body.classList.remove('gex-resize-active');
                    try { handle.releasePointerCapture(upEvent.pointerId); } catch (e) {}
                    const liveWidth = parseFloat(getComputedStyle(grid).getPropertyValue('--gex-col-w')) || startWidth;
                    applyGexColWidth(liveWidth, true);
                    scheduleGexResizeRefresh();
                };
                document.addEventListener('pointermove', onMove);
                document.addEventListener('pointerup', onUp);
                document.addEventListener('pointercancel', onUp);
            });
        }
        (function restoreGexColumnCollapse() {
            let collapsed = false;
            try { collapsed = localStorage.getItem(GEX_COL_COLLAPSE_KEY) === '1'; } catch (e) {}
            // Defer until DOM is parsed
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => { ensureStrikeRailResizeHandle(); applyGexColumnCollapse(collapsed); wireGexColumnToggle(); });
            } else {
                ensureStrikeRailResizeHandle();
                applyGexColumnCollapse(collapsed);
                wireGexColumnToggle();
            }
        })();
        (function restoreGexColumnWidth() {
            let savedWidth = 352;
            try {
                const raw = parseFloat(localStorage.getItem(GEX_COL_WIDTH_KEY));
                if (Number.isFinite(raw)) savedWidth = raw;
            } catch (e) {}
            applyGexColWidth(savedWidth, false);
        })();

        // Standalone price chart renderer — called by /update_price without touching other charts.
        function applyPriceData(priceJson) {
            if (!isChartVisible('price')) return;
            lastPriceData = priceJson; // keep for popout push
            const priceContainer = ensurePriceChartDom();
            if (!priceContainer) return;
            showPriceChartUI();
            const parsed = typeof priceJson === 'string' ? JSON.parse(priceJson) : priceJson;
            if (!parsed.error) {
                renderTVPriceChart(parsed);
            }
        }

        // ── Right-rail tab state (Overview / Levels / Scenarios) ─────────
        const RAIL_TAB_KEY = 'gex.rightRailTab';
        let activeRailTab = 'overview';
        try {
            const saved = localStorage.getItem(RAIL_TAB_KEY);
            if (saved === 'overview' || saved === 'levels' || saved === 'scenarios') {
                activeRailTab = saved;
            } else if (saved === 'alerts' || saved === 'gex') {
                // Migrate older tab keys to the Overview surface.
                try { localStorage.setItem(RAIL_TAB_KEY, 'overview'); } catch (e) {}
            }
        } catch (e) {}
        let _lastGexPanelJson = null; // retained so re-render paths (resize, uncollapse) can reuse last data

        function applyRightRailTab() {
            document.querySelectorAll('.right-rail-tab').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.railTab === activeRailTab);
            });
            document.querySelectorAll('.right-rail-panel').forEach(p => {
                p.classList.toggle('active', p.dataset.railPanel === activeRailTab);
            });
            if (activeRailTab === 'overview') {
                markRailAlertsSeen();
            } else {
                _updateAlertsBadge();
            }
            if (activeRailTab === 'scenarios') {
                renderScenarioTable(_lastStats && _lastStats.scenarios);
            }
        }

        function wireRightRailTabs() {
            document.querySelectorAll('.right-rail-tab').forEach(btn => {
                if (btn.__railWired) return;
                btn.__railWired = true;
                btn.addEventListener('click', () => {
                    const next = btn.dataset.railTab;
                    if (!next || next === activeRailTab) return;
                    activeRailTab = next;
                    try { localStorage.setItem(RAIL_TAB_KEY, activeRailTab); } catch (e) {}
                    applyRightRailTab();
                });
            });
            wireGexScopePill();
        }

        function getVisibleStrikeRailTabs(selectedCharts = getChartVisibility()) {
            const tabs = ['gex'];
            STRIKE_RAIL_CHART_IDS.forEach(id => {
                if (selectedCharts[id] !== false) tabs.push(id);
            });
            return tabs;
        }

        function applyStrikeRailTabs(selectedCharts = getChartVisibility()) {
            const tabsEl = document.getElementById('strike-rail-tabs');
            if (!tabsEl) return;
            const availableTabs = getVisibleStrikeRailTabs(selectedCharts);
            if (!availableTabs.includes(activeStrikeRailTab)) {
                activeStrikeRailTab = availableTabs[0] || 'gex';
                try { localStorage.setItem(STRIKE_RAIL_TAB_KEY, activeStrikeRailTab); } catch (e) {}
            }
            const tabsKey = availableTabs.join('|');
            const needsRebuild =
                tabsEl.dataset.tabsKey !== tabsKey ||
                !tabsEl.querySelector('.strike-rail-tab-list') ||
                !tabsEl.querySelector('#strike-rail-select');
            if (needsRebuild) {
                const buttonHtml = availableTabs.map(tab =>
                    '<button type="button" class="strike-rail-tab' + (tab === activeStrikeRailTab ? ' active' : '') +
                    '" data-strike-rail-tab="' + tab + '">' + (STRIKE_RAIL_LABELS[tab] || tab) + '</button>'
                ).join('');
                const optionHtml = availableTabs.map(tab =>
                    '<option value="' + tab + '"' + (tab === activeStrikeRailTab ? ' selected' : '') + '>' +
                    (STRIKE_RAIL_LABELS[tab] || tab) + '</option>'
                ).join('');
                tabsEl.innerHTML =
                    '<div class="strike-rail-tab-list">' + buttonHtml + '</div>' +
                    '<label class="strike-rail-select-wrap" aria-label="Strike rail view">' +
                        '<span class="strike-rail-select-icon" aria-hidden="true">&#9776;</span>' +
                        '<select class="strike-rail-select" id="strike-rail-select">' + optionHtml + '</select>' +
                    '</label>';
                tabsEl.dataset.tabsKey = tabsKey;
                wireStrikeRailTabs();
            } else {
                tabsEl.querySelectorAll('.strike-rail-tab').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.strikeRailTab === activeStrikeRailTab);
                });
                const select = tabsEl.querySelector('#strike-rail-select');
                if (select && select.value !== activeStrikeRailTab) {
                    select.value = activeStrikeRailTab;
                }
            }
        }

        function wireStrikeRailTabs() {
            document.querySelectorAll('.strike-rail-tab').forEach(btn => {
                if (btn.__strikeRailWired) return;
                btn.__strikeRailWired = true;
                btn.addEventListener('click', () => {
                    const next = btn.dataset.strikeRailTab;
                    if (!next || next === activeStrikeRailTab) return;
                    activeStrikeRailTab = next;
                    try {
                        localStorage.setItem(STRIKE_RAIL_TAB_KEY, activeStrikeRailTab);
                        localStorage.setItem(STRIKE_RAIL_PREF_VERSION_KEY, '2');
                    } catch (e) {}
                    applyStrikeRailTabs();
                    renderStrikeRailPanel();
                });
            });
            const select = document.getElementById('strike-rail-select');
            if (select && !select.__strikeRailWired) {
                select.__strikeRailWired = true;
                select.addEventListener('change', () => {
                    const next = select.value;
                    if (!next || next === activeStrikeRailTab) return;
                    activeStrikeRailTab = next;
                    try {
                        localStorage.setItem(STRIKE_RAIL_TAB_KEY, activeStrikeRailTab);
                        localStorage.setItem(STRIKE_RAIL_PREF_VERSION_KEY, '2');
                    } catch (e) {}
                    applyStrikeRailTabs();
                    renderStrikeRailPanel();
                });
            }
        }

        function getStrikeRailTarget() {
            return document.getElementById('gex-side-panel');
        }

        function renderStrikeRailEmpty(message) {
            const target = getStrikeRailTarget();
            if (!target) return;
            try { Plotly.purge(target); } catch (e) {}
            target.replaceChildren();
            target.__strikeRailState = null;
            const empty = document.createElement('div');
            empty.className = 'strike-rail-empty';
            empty.textContent = message || 'Strike rail data loads with chart updates.';
            target.appendChild(empty);
        }

        let _strikeRailLastPayloadByTab = Object.create(null);
        let _strikeRailRenderToken = 0;
        function getStrikeRailPayloadKey(payload) {
            if (payload == null) return '';
            return typeof payload === 'string' ? payload : JSON.stringify(payload);
        }
        function getStrikeRailSyncSpec() {
            if (isGexColumnCollapsed()) return null;
            const tvEl = document.getElementById('price-chart');
            if (!tvEl || !tvPriceChart || !tvCandleSeries) return null;
            try {
                const h = tvEl.clientHeight;
                if (!h) return null;
                const tsH = (tvPriceChart.timeScale && tvPriceChart.timeScale().height)
                    ? tvPriceChart.timeScale().height() : 0;
                const plotBottomPx = Math.max(0, h - tsH);
                const top = tvCandleSeries.coordinateToPrice(0);
                const bot = tvCandleSeries.coordinateToPrice(plotBottomPx);
                if (top == null || bot == null) return null;
                const lo = Math.min(top, bot);
                const hi = Math.max(top, bot);
                if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return null;
                return {
                    lo,
                    hi,
                    tsH,
                    key: [lo.toFixed(4), hi.toFixed(4), String(Math.round(tsH))].join('|'),
                };
            } catch (e) {
                return null;
            }
        }
        function applyStrikeRailSyncToFigure(fig, syncSpec) {
            if (!fig || !syncSpec) return fig;
            fig.layout = fig.layout || {};
            fig.layout.margin = Object.assign({ l: 12, r: 12, t: 10, b: 28 }, fig.layout.margin || {});
            fig.layout.margin.t = 0;
            fig.layout.margin.b = syncSpec.tsH;
            fig.layout.yaxis = Object.assign({}, fig.layout.yaxis || {}, {
                range: [syncSpec.lo, syncSpec.hi],
                autorange: false,
                side: 'right',
                automargin: true,
            });
            return fig;
        }
        function lockStrikeRailFigureInteractions(fig) {
            if (!fig) return fig;
            fig.layout = fig.layout || {};
            fig.layout.dragmode = false;
            fig.layout.uirevision = 'strike-rail-locked';
            fig.layout.xaxis = Object.assign({}, fig.layout.xaxis || {}, {
                fixedrange: true,
                automargin: true,
            });
            fig.layout.yaxis = Object.assign({}, fig.layout.yaxis || {}, {
                fixedrange: true,
                side: 'right',
                automargin: true,
            });
            return fig;
        }

        function buildStrikeRailFigure(tab, payload) {
            const parsed = typeof payload === 'string' ? JSON.parse(payload) : payload;
            parsed.layout = parsed.layout || {};
            parsed.layout.autosize = true;
            parsed.layout.width = null;
            parsed.layout.height = null;
            parsed.layout.title = { text: '' };
            parsed.layout.margin = Object.assign({ l: 12, r: 12, t: 10, b: 28 }, parsed.layout.margin || {});
            parsed.layout.showlegend = false;
            parsed.layout.plot_bgcolor = parsed.layout.plot_bgcolor || '#1E1E1E';
            parsed.layout.paper_bgcolor = parsed.layout.paper_bgcolor || '#1E1E1E';
            if (parsed.layout.xaxis) parsed.layout.xaxis.automargin = true;
            if (parsed.layout.yaxis) {
                parsed.layout.yaxis.automargin = true;
                parsed.layout.yaxis.side = 'right';
            }
            return lockStrikeRailFigureInteractions(parsed);
        }

        function renderStrikeRailPanel(force = false) {
            const target = getStrikeRailTarget();
            if (!target || isGexColumnCollapsed()) return;
            let payload = activeStrikeRailTab === 'gex'
                ? _lastGexPanelJson
                : (lastData && lastData[activeStrikeRailTab]);
            if (!payload) {
                payload = _strikeRailLastPayloadByTab[activeStrikeRailTab] || null;
                if (!payload) {
                    renderStrikeRailEmpty((STRIKE_RAIL_LABELS[activeStrikeRailTab] || 'Strike rail') + ' data loads with the next refresh.');
                    return;
                }
            }
            try {
                const payloadKey = getStrikeRailPayloadKey(payload);
                const syncSpec = getStrikeRailSyncSpec();
                const syncKey = syncSpec ? syncSpec.key : '';
                const currentState = target.__strikeRailState || null;
                if (
                    !force &&
                    target._fullLayout &&
                    currentState &&
                    currentState.tab === activeStrikeRailTab &&
                    currentState.payloadKey === payloadKey
                ) {
                    if (syncKey && currentState.syncKey !== syncKey) {
                        scheduleGexPanelSync();
                    }
                    return;
                }
                const fig = activeStrikeRailTab === 'gex' ? (typeof payload === 'string' ? JSON.parse(payload) : payload)
                                                          : buildStrikeRailFigure(activeStrikeRailTab, payload);
                lockStrikeRailFigureInteractions(fig);
                applyStrikeRailSyncToFigure(fig, syncSpec);
                const config = {
                    displayModeBar: false,
                    responsive: true,
                    scrollZoom: false,
                    doubleClick: false,
                    showAxisDragHandles: false,
                };
                target.style.width = '100%';
                target.style.height = '100%';
                const hasMountedPlot = !!target._fullLayout;
                if (!hasMountedPlot) {
                    target.replaceChildren();
                }
                _strikeRailLastPayloadByTab[activeStrikeRailTab] = payload;
                const renderToken = ++_strikeRailRenderToken;
                const renderer = hasMountedPlot ? Plotly.react : Plotly.newPlot;
                renderer(target, fig.data || [], fig.layout || {}, config)
                    .then(() => {
                        if (renderToken !== _strikeRailRenderToken) return;
                        target.__strikeRailState = {
                            tab: activeStrikeRailTab,
                            payloadKey,
                            syncKey,
                        };
                        try { Plotly.Plots.resize(target); } catch (e) {}
                        syncGexPanelYAxisToTV();
                    });
            } catch (e) {
                console.warn('strike rail render failed', activeStrikeRailTab, e);
                renderStrikeRailEmpty('Could not render ' + (STRIKE_RAIL_LABELS[activeStrikeRailTab] || 'strike rail') + '.');
            }
        }

        function wireGexScopePill() {
            document.querySelectorAll('.gex-scope-btn').forEach(btn => {
                if (btn.__scopeWired) return;
                btn.__scopeWired = true;
                btn.addEventListener('click', () => {
                    gexScope = btn.dataset.scope;
                    try { localStorage.setItem('gexScope', gexScope); } catch(e) {}
                    redrawGexScope();
                });
            });
        }

        function getScopedStats() {
            return gexScope === '0dte' ? (_lastStats0dte || null) : (_lastStats || null);
        }

        function getScopedKeyLevels() {
            return gexScope === '0dte' ? (_lastKeyLevels0dte || null) : (_lastKeyLevels || null);
        }

        function scopePillShouldShow() {
            const selected = getSelectedExpiryValues();
            return selected.length > 1 && !!_lastStats && !!_lastStats0dte;
        }

        function syncGexScopePillVisibility() {
            const show = scopePillShouldShow();
            document.querySelectorAll('.gex-scope-pill').forEach(pill => {
                pill.classList.toggle('hidden', !show);
            });
            if (!show && gexScope !== 'all') {
                gexScope = 'all';
                try { localStorage.setItem('gexScope', gexScope); } catch(e) {}
            }
            return show;
        }

        function redrawGexScope() {
            syncGexScopePillVisibility();
            // Sync pill active state
            document.querySelectorAll('.gex-scope-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.scope === gexScope);
            });
            const stats = getScopedStats();
            const levels = getScopedKeyLevels();
            renderMarketMetrics(stats);
            renderDealerImpact(stats);
            renderGammaProfile(stats);
            renderChainActivity(stats);
            renderIVContext(stats);
            renderFlowPulse(stats);
            renderRailAlerts(Array.isArray(stats && stats.alerts) ? stats.alerts : []);
            renderRailKeyLevels(stats);
            renderKeyLevels(levels);
            renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
        }

        function renderGexSidePanel(panelJson) {
            _lastGexPanelJson = panelJson || null;
            if (!_lastGexPanelJson) {
                if (activeStrikeRailTab === 'gex') {
                    renderStrikeRailEmpty('GEX data loads with the next refresh.');
                }
                return;
            }
            if (activeStrikeRailTab === 'gex') renderStrikeRailPanel();
        }

        // Mirror the TradingView chart's visible price range onto the Plotly
        // strike rail so bars line up with candles at the same strike.
        let _gexSyncScheduled = false;
        function syncGexPanelYAxisToTV() {
            if (isGexColumnCollapsed()) return;
            const panel = getStrikeRailTarget();
            const syncSpec = getStrikeRailSyncSpec();
            if (!panel || !syncSpec) return;
            try {
                const currentState = panel.__strikeRailState || {};
                if (currentState.syncKey === syncSpec.key) return;
                // Mirror TV's plot-area pixel bounds by zeroing Plotly top margin
                // and matching bottom margin to TV's time-axis height. That way
                // the Plotly y-axis range maps to the same screen pixels as TV's.
                Plotly.relayout(panel, {
                    'yaxis.range': [syncSpec.lo, syncSpec.hi],
                    'margin.t': 0,
                    'margin.b': syncSpec.tsH,
                });
                panel.__strikeRailState = Object.assign({}, currentState, { syncKey: syncSpec.key });
            } catch (e) {
                // TV chart may not be ready yet; skip silently
            }
        }
        function scheduleGexPanelSync() {
            if (isGexColumnCollapsed()) return;
            if (_gexSyncScheduled) return;
            _gexSyncScheduled = true;
            requestAnimationFrame(() => {
                _gexSyncScheduled = false;
                syncGexPanelYAxisToTV();
            });
        }

        // ── Trader stats KPI strip + alerts ─────────────────────────────────
        function fmtMoneyCompact(n) {
            if (n == null || !isFinite(n)) return '—';
            const abs = Math.abs(n);
            const sign = n < 0 ? '-' : '';
            if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
            if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
            if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
            return sign + '$' + abs.toFixed(0);
        }
        function fmtCountCompact(n) {
            if (n == null || !isFinite(n)) return '—';
            const abs = Math.abs(n);
            if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
            if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
            if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'K';
            return Math.round(n).toLocaleString();
        }
        function fmtSignedPriceDelta(n) {
            if (n == null || !isFinite(n)) return '—';
            if (Math.abs(n) < 0.005) return '$0.00';
            return (n > 0 ? '+' : '-') + '$' + Math.abs(n).toFixed(2);
        }
        function fmtPrice(n) {
            return (n == null || !isFinite(n)) ? '—' : ('$' + n.toFixed(2));
        }
        let _lastStats = null;
        function renderTraderStats(stats) {
            _lastStats = stats || null;
            if (!stats) {
                renderRailAlerts([], { reset: true });
                renderRailKeyLevels(null);
                renderDealerImpact(null);
                renderMarketMetrics(null);
                renderGammaProfile(null);
                renderChainActivity(null);
                renderIVContext(null);
                renderFlowPulse(null);
                renderCentroidPanel(null);
                renderScenarioTable(null);
                return;
            }
            renderRailAlerts(Array.isArray(stats.alerts) ? stats.alerts : []);
            renderRailKeyLevels(stats);
            renderDealerImpact(stats);
            renderMarketMetrics(stats);
            renderGammaProfile(stats);
            renderChainActivity(stats);
            renderCentroidPanel(stats.centroid_panel || null);
            // Skip Scenario DOM work on background ticks; applyRightRailTab will
            // render it lazily on first reveal using _lastStats.
            if (activeRailTab === 'scenarios') {
                renderScenarioTable(stats.scenarios);
            }
        }

        // Scenario GEX table renderer (Stage 3). Rebuilds tbody from the
        // 7-row scenarios payload. "Current" row is highlighted; sign drives
        // the color token so long-/short-gamma rows are scannable at a glance.
        function renderScenarioTable(rows) {
            const tbl = document.getElementById('scenario-table');
            if (!tbl) return;
            const tbody = tbl.querySelector('tbody');
            if (!tbody) return;
            if (!Array.isArray(rows) || !rows.length) {
                tbody.innerHTML = '<tr><td colspan="3" class="scn-empty">Scenarios load with stream data.</td></tr>';
                return;
            }
            tbody.innerHTML = rows.map(r => {
                const v = (r && typeof r.net_gex === 'number' && isFinite(r.net_gex)) ? r.net_gex : null;
                const cls = v == null ? '' : (v >= 0 ? 'pos' : 'neg');
                const txt = fmtMoneyCompact(v);
                const isCur = (r && r.label === 'Current');
                const regime = _escapeHtml(r && r.regime ? r.regime : '—');
                const mag = _escapeHtml(r && r.magnitude ? r.magnitude : '');
                return '<tr' + (isCur ? ' class="current"' : '') + '>' +
                       '<td>' + _escapeHtml(r && r.label ? r.label : '—') + '</td>' +
                       '<td class="num ' + cls + '">' + txt + '</td>' +
                       '<td>' + regime + (mag ? '<span class="mag">(' + mag + ')</span>' : '') + '</td>' +
                       '</tr>';
            }).join('');
        }

        // Dealer Hedge Impact block renderer (above GEX chart in the GEX rail tab).
        // Values are signed $-flows/shifts; pos/neg class drives the color token.
        function renderDealerImpact(stats) {
            const el = document.getElementById('dealer-impact');
            if (!el) return;
            el.classList.toggle('compact', !dealerImpactVerbose);
            const setCue = (key, tone, text) => {
                const n = el.querySelector('[data-di-cue="' + key + '"]');
                if (!n) return;
                n.textContent = text || '—';
                n.classList.remove('pos', 'neg');
                if (tone) n.classList.add(tone);
            };
            const setMetTone = (key, tone, text) => {
                const n = document.querySelector('[data-met="' + key + '"]');
                if (!n) return;
                n.textContent = text == null ? '—' : text;
                n.classList.remove('pos', 'neg');
                if (tone) n.classList.add(tone);
            };
            const describeStrength = score => {
                const abs = Math.abs(score);
                if (abs < 0.18) return 'Neutral';
                if (abs < 0.45) return 'Slight';
                if (abs < 0.72) return 'Moderate';
                return 'Strong';
            };
            const buildDealerOverview = s => {
                if (!s) {
                    return {
                        headline: 'Dealer read unavailable',
                        subhead: 'Waiting for live stats.',
                        conviction: '—',
                        tone: '',
                    };
                }
                const up = Number.isFinite(s.hedge_on_up_1pct) ? s.hedge_on_up_1pct : null;
                const down = Number.isFinite(s.hedge_on_down_1pct) ? s.hedge_on_down_1pct : null;
                const vanna = Number.isFinite(s.vanna_delta_shift_per_1volpt) ? s.vanna_delta_shift_per_1volpt : null;
                const charm = Number.isFinite(s.charm_by_close) ? s.charm_by_close : null;
                let structure = 'mixed';
                if (s.regime === 'Long Gamma') structure = 'mean_revert';
                else if (s.regime === 'Short Gamma') structure = 'momentum';
                else if (up != null && down != null) {
                    if (up < 0 && down > 0) structure = 'mean_revert';
                    else if (up > 0 && down < 0) structure = 'momentum';
                }
                const parts = [];
                if (vanna != null) parts.push(vanna);
                if (charm != null) parts.push(charm * 0.75);
                const numer = parts.reduce((sum, v) => sum + v, 0);
                const denom = parts.reduce((sum, v) => sum + Math.abs(v), 0);
                const biasScore = denom > 0 ? (numer / denom) : 0;
                const biasStrength = describeStrength(biasScore).toLowerCase();
                let tone = '';
                if (biasScore > 0.18) tone = 'pos';
                else if (biasScore < -0.18) tone = 'neg';
                const structureClarity = structure === 'mixed' ? 0.4 : 0.95;
                const convictionScore = Math.round((0.55 * structureClarity + 0.45 * Math.abs(biasScore)) * 100);
                const conviction = convictionScore >= 72 ? 'High edge'
                    : convictionScore >= 48 ? 'Usable'
                    : 'Low edge';
                if (structure === 'mean_revert') {
                    if (tone === 'pos') {
                        return {
                            headline: 'Chop with bullish support',
                            subhead: `${biasStrength} bullish hedge tilt. Spot hedging fades moves first.`,
                            conviction,
                            tone,
                        };
                    }
                    if (tone === 'neg') {
                        return {
                            headline: 'Chop with bearish pressure',
                            subhead: `${biasStrength} bearish hedge tilt. Spot hedging still leans mean reversion.`,
                            conviction,
                            tone,
                        };
                    }
                    return {
                        headline: 'Chop / mean reversion',
                        subhead: 'Spot hedging fades both directions. Good for lower follow-through unless flow overwhelms it.',
                        conviction,
                        tone: '',
                    };
                }
                if (structure === 'momentum') {
                    if (tone === 'pos') {
                        return {
                            headline: 'Bullish momentum risk',
                            subhead: `${biasStrength} bullish hedge tilt. Rips can extend if vol/charm keep leaning positive.`,
                            conviction,
                            tone,
                        };
                    }
                    if (tone === 'neg') {
                        return {
                            headline: 'Bearish momentum risk',
                            subhead: `${biasStrength} bearish hedge tilt. Dealers are more likely to reinforce downside moves.`,
                            conviction,
                            tone,
                        };
                    }
                    return {
                        headline: 'Momentum-sensitive tape',
                        subhead: 'Spot hedging reinforces moves. Directional edge is mixed, but follow-through risk is elevated.',
                        conviction,
                        tone: '',
                    };
                }
                if (tone === 'pos') {
                    return {
                        headline: 'Mixed read, bullish tilt',
                        subhead: `${biasStrength} bullish tilt from vanna/charm. Spot-response rows are not aligned cleanly.`,
                        conviction,
                        tone,
                    };
                }
                if (tone === 'neg') {
                    return {
                        headline: 'Mixed read, bearish tilt',
                        subhead: `${biasStrength} bearish tilt from vanna/charm. Spot-response rows are not aligned cleanly.`,
                        conviction,
                        tone,
                    };
                }
                return {
                    headline: 'Mixed / low edge',
                    subhead: 'Signals conflict. Use the raw rows as context, not a standalone trigger.',
                    conviction,
                    tone: '',
                };
            };
            const cueFor = (key, v) => {
                if (v == null || !isFinite(v) || v === 0) return { tone: '', text: 'balanced hedge read' };
                const pos = v > 0;
                if (key === 'hedge_on_up_1pct') {
                    return { tone: pos ? 'pos' : 'neg', text: pos ? 'buy into strength' : 'sell into strength' };
                }
                if (key === 'hedge_on_down_1pct') {
                    return { tone: pos ? 'pos' : 'neg', text: pos ? 'buy the dip' : 'sell the dip' };
                }
                if (key === 'vanna_up_1') {
                    return { tone: pos ? 'pos' : 'neg', text: pos ? 'higher vol adds long delta' : 'higher vol adds short delta' };
                }
                if (key === 'vanna_down_1') {
                    return { tone: pos ? 'pos' : 'neg', text: pos ? 'lower vol adds long delta' : 'lower vol adds short delta' };
                }
                if (key === 'charm_by_close') {
                    return { tone: pos ? 'pos' : 'neg', text: pos ? 'delta firms into close' : 'delta fades into close' };
                }
                return { tone: '', text: '—' };
            };
            const set = (key, v) => {
                const n = el.querySelector('[data-di="' + key + '"]');
                if (!n) return;
                if (v == null || !isFinite(v)) {
                    n.textContent = '—';
                    n.classList.remove('pos', 'neg');
                    setCue(key, '', '—');
                    return;
                }
                const sign = v > 0 ? '+' : '';
                n.textContent = sign + fmtMoneyCompact(v);
                n.classList.remove('pos', 'neg');
                if (v !== 0) n.classList.add(v > 0 ? 'pos' : 'neg');
                const cue = cueFor(key, v);
                setCue(key, cue.tone, cue.text);
            };
            const takeawayEl = document.querySelector('[data-met="dealer_takeaway"]');
            if (!stats) {
                ['hedge_on_up_1pct','hedge_on_down_1pct','vanna_up_1','vanna_down_1','charm_by_close']
                    .forEach(k => set(k, null));
                const overview = buildDealerOverview(null);
                setMetTone('dealer_conviction', overview.tone, overview.conviction);
                setMetTone('dealer_headline', overview.tone, overview.headline);
                setMetTone('dealer_subhead', '', overview.subhead);
                if (takeawayEl) {
                    takeawayEl.textContent = 'Positive values indicate dealer buying to hedge; negative values indicate dealer selling to hedge.';
                }
                return;
            }
            set('hedge_on_up_1pct',   stats.hedge_on_up_1pct);
            set('hedge_on_down_1pct', stats.hedge_on_down_1pct);
            set('vanna_up_1',         stats.vanna_delta_shift_per_1volpt);
            set('vanna_down_1',       stats.vanna_delta_shift_per_1volpt == null
                                        ? null : -stats.vanna_delta_shift_per_1volpt);
            set('charm_by_close',     stats.charm_by_close);
            const overview = buildDealerOverview(stats);
            setMetTone('dealer_conviction', overview.tone, overview.conviction);
            setMetTone('dealer_headline', overview.tone, overview.headline);
            setMetTone('dealer_subhead', '', overview.subhead);
            if (takeawayEl) {
                if (stats.regime === 'Long Gamma') {
                    takeawayEl.textContent = 'Long-gamma posture: dealers usually buy dips and sell rips, which tends to dampen follow-through.';
                } else if (stats.regime === 'Short Gamma') {
                    takeawayEl.textContent = 'Short-gamma posture: dealers usually sell dips and buy rips, which can reinforce momentum.';
                } else {
                    takeawayEl.textContent = 'Positive values indicate dealer buying to hedge; negative values indicate dealer selling to hedge.';
                }
            }
        }

        // ── Right-rail alerts panel ──────────────────────────────────────
        let _lastRailAlerts = [];
        let _alertsSeenKeys = new Set();
        let _railAlertBuffer = new Map();
        let _railAlertScopeKey = '';
        const RAIL_ALERT_TTL_MS = Object.freeze({
            critical: 10 * 60 * 1000,
            active: 5 * 60 * 1000,
            recent: 2 * 60 * 1000,
        });

        function _escapeHtml(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function _currentRailAlertScopeKey() {
            const tickerEl = document.getElementById('ticker');
            const ticker = String((tickerEl && tickerEl.value) || '').trim().toUpperCase() || 'UNKNOWN';
            return ticker + '|' + String(gexScope || 'all');
        }

        function _clearRailAlertState(scopeKey = '') {
            _railAlertBuffer = new Map();
            _lastRailAlerts = [];
            _alertsSeenKeys = new Set();
            _railAlertScopeKey = scopeKey || _currentRailAlertScopeKey();
        }

        function _ensureRailAlertScope(reset = false) {
            const scopeKey = _currentRailAlertScopeKey();
            if (reset || (_railAlertScopeKey && scopeKey !== _railAlertScopeKey)) {
                _clearRailAlertState(scopeKey);
            } else if (!_railAlertScopeKey) {
                _railAlertScopeKey = scopeKey;
            }
            return _railAlertScopeKey;
        }

        function _alertSeenKey(a) {
            if (!a) return '';
            return String(a.scopeKey || _railAlertScopeKey || '') + ':' + String(a.id || a.text || '');
        }

        function _coerceAlertTsMs(value) {
            if (!value) return null;
            const ts = new Date(value).getTime();
            return isFinite(ts) ? ts : null;
        }

        function _relTimeMs(tsMs) {
            if (tsMs == null || !isFinite(tsMs)) return '';
            const mins = Math.max(0, Math.round((Date.now() - tsMs) / 60000));
            if (mins < 1) return 'now';
            if (mins < 60) return mins + 'm';
            return Math.floor(mins / 60) + 'h';
        }

        function _flowPulseLeanMeta(label) {
            const key = String(label || 'mixed').toLowerCase();
            if (key === 'bullish') return { cls: 'bullish', text: 'Bull' };
            if (key === 'bearish') return { cls: 'bearish', text: 'Bear' };
            if (key === 'hedge') return { cls: 'hedge', text: 'Hedge' };
            return { cls: 'mixed', text: 'Mixed' };
        }

        function _flowPulseSummaryText(summary) {
            const s = summary && typeof summary === 'object' ? summary : {};
            const label = String(s.label || 'mixed').toLowerCase();
            if (label === 'bullish') {
                return 'Bullish Lean';
            }
            if (label === 'bearish') {
                return 'Bearish Lean';
            }
            if (label === 'hedge') {
                return 'Hedge Lean';
            }
            return 'Mixed Lean';
        }

        function _liveAlertsSummaryText(items) {
            const rows = Array.isArray(items) ? items : [];
            if (!rows.length) return 'Mixed Lean';
            let biasScore = 0;
            let hedgeWeight = 0;
            rows.forEach((row, index) => {
                const direction = String((row && row.direction_label) || '').toLowerCase();
                const tier = String((row && row.tier) || '').toLowerCase();
                const cluster = Math.max(1, Math.min(4, parseInt(row && row.clusterCount, 10) || 1));
                const tierWeight = tier === 'critical' ? 3 : (tier === 'active' ? 2 : 1);
                const weight = tierWeight * cluster * (index === 0 ? 1.3 : 1);
                if (direction === 'bullish') biasScore += weight;
                else if (direction === 'bearish') biasScore -= weight;
                else if (direction === 'hedge') hedgeWeight += weight;
            });
            if (Math.abs(biasScore) >= Math.max(1.5, hedgeWeight * 0.9)) {
                return biasScore > 0 ? 'Bullish Lean' : 'Bearish Lean';
            }
            if (hedgeWeight > 0 && Math.abs(biasScore) < hedgeWeight) {
                return 'Hedge Lean';
            }
            return 'Mixed Lean';
        }

        function _railAlertDirectionMeta(label) {
            const key = String(label || 'mixed').toLowerCase();
            if (key === 'bullish') return { cls: 'bullish', text: 'Bull' };
            if (key === 'bearish') return { cls: 'bearish', text: 'Bear' };
            if (key === 'hedge') return { cls: 'hedge', text: 'Hedge' };
            if (key === 'structural') return { cls: 'structural', text: 'Struct' };
            return { cls: 'mixed', text: 'Mixed' };
        }

        function _inferRailAlertKind(a) {
            if (a && a.alert_type) return String(a.alert_type);
            const id = String((a && a.id) || '');
            const text = String((a && a.text) || '').toLowerCase();
            if (id.startsWith('wall_shift:')) return 'wall_shift';
            if (id.startsWith('flow_pulse:')) return 'flow_pulse';
            if (id.startsWith('iv_surge:')) return 'iv_surge';
            if (id.startsWith('voi_ratio:')) return 'voi_ratio';
            if (id.startsWith('vol_spike:')) return 'vol_spike';
            if (text.includes('short-gamma regime') || text.includes('long-gamma regime')) return 'regime';
            if (text.includes('gamma flip')) return 'gamma_flip';
            if (text.includes('call wall') || text.includes('put wall')) return 'wall_proximity';
            return 'generic';
        }

        function _railAlertTier(kind) {
            if (['wall_shift', 'flow_pulse', 'regime'].includes(kind)) return 'critical';
            if (['iv_surge', 'voi_ratio', 'vol_spike'].includes(kind)) return 'active';
            return 'recent';
        }

        function _railAlertPriority(src, kind, tier) {
            const base = {
                wall_shift: 110,
                flow_pulse: 95,
                regime: 82,
                iv_surge: 72,
                voi_ratio: 64,
                vol_spike: 56,
                gamma_flip: 44,
                wall_proximity: 38,
                generic: 24,
            }[kind] || 20;
            const levelBonus = src.level === 'warn' ? 12 : (src.level === 'flow' ? 8 : 3);
            const detailBonus = src.detail ? 4 : 0;
            const strikeBonus = (src.strike != null && isFinite(src.strike)) ? 2 : 0;
            const tierBonus = tier === 'critical' ? 14 : (tier === 'active' ? 7 : 0);
            return base + levelBonus + detailBonus + strikeBonus + tierBonus;
        }

        function _mergeRailAlerts(list) {
            const scopeKey = _ensureRailAlertScope(false);
            const now = Date.now();
            (Array.isArray(list) ? list : []).forEach(item => {
                const src = (item && typeof item === 'object') ? item : { text: String(item == null ? '' : item) };
                const text = String(src.text == null ? '' : src.text).trim();
                if (!text) return;
                const id = String(src.id || (String(src.level || 'info') + ':' + text));
                const kind = _inferRailAlertKind(src);
                const tier = _railAlertTier(kind);
                const previous = _railAlertBuffer.get(id);
                const eventTsMs = _coerceAlertTsMs(src.ts) || now;
                _railAlertBuffer.set(id, Object.assign({}, previous || {}, src, {
                    id,
                    text,
                    detail: src.detail ? String(src.detail) : '',
                    level: ['warn', 'info', 'flow'].includes(src.level) ? src.level : 'info',
                    kind,
                    tier,
                    priority: _railAlertPriority(src, kind, tier),
                    eventTsMs,
                    firstSeenMs: previous ? previous.firstSeenMs : eventTsMs,
                    lastSeenMs: now,
                    refreshCount: previous ? (previous.refreshCount + 1) : 1,
                    scopeKey,
                }));
            });
        }

        function _pruneRailAlertBuffer() {
            const now = Date.now();
            for (const [key, alert] of _railAlertBuffer.entries()) {
                const ttl = RAIL_ALERT_TTL_MS[alert.tier] || RAIL_ALERT_TTL_MS.recent;
                const anchor = alert.eventTsMs || alert.lastSeenMs || now;
                if (!isFinite(anchor) || (now - anchor) > ttl) {
                    _railAlertBuffer.delete(key);
                }
            }
        }

        function _getBufferedRailAlerts() {
            const now = Date.now();
            const rows = Array.from(_railAlertBuffer.values()).map(alert => {
                const ttl = RAIL_ALERT_TTL_MS[alert.tier] || RAIL_ALERT_TTL_MS.recent;
                const ageMs = Math.max(0, now - (alert.eventTsMs || now));
                return Object.assign({}, alert, {
                    _ttlMs: ttl,
                    _ageMs: ageMs,
                    _freshness: Math.max(0, 1 - (ageMs / ttl)),
                });
            });
            rows.sort((a, b) => {
                if (b.priority !== a.priority) return b.priority - a.priority;
                if (b._freshness !== a._freshness) return b._freshness - a._freshness;
                return (b.eventTsMs || 0) - (a.eventTsMs || 0);
            });
            return rows;
        }

        function _formatRailAlertStrike(value) {
            const n = Number(value);
            if (!isFinite(n)) return '—';
            return (Math.abs(n - Math.round(n)) < 0.001)
                ? String(Math.round(n))
                : n.toFixed(2).replace(/\.?0+$/, '');
        }

        function _isClusterableRailAlert(a) {
            const kind = String((a && (a.alert_type || a.kind)) || '');
            return ['voi_ratio', 'iv_surge', 'flow_pulse'].includes(kind)
                && !!(a && a.option_type)
                && a && a.strike != null
                && isFinite(Number(a.strike));
        }

        function _clusterRailAlerts(rows) {
            const passthrough = [];
            const grouped = new Map();
            (Array.isArray(rows) ? rows : []).forEach(row => {
                if (!_isClusterableRailAlert(row)) {
                    passthrough.push(row);
                    return;
                }
                const kind = String(row.alert_type || row.kind || '');
                const key = [
                    kind,
                    String(row.option_type || ''),
                    String(row.side || ''),
                    String(row.expiry_iso || ''),
                    String(row.level || ''),
                    String(row.tier || ''),
                    String(row.direction_label || ''),
                ].join('|');
                if (!grouped.has(key)) grouped.set(key, []);
                grouped.get(key).push(row);
            });
            const clustered = [];
            grouped.forEach(groupRows => {
                const sorted = groupRows.slice().sort((a, b) => {
                    const strikeDiff = Number(a.strike) - Number(b.strike);
                    if (strikeDiff !== 0) return strikeDiff;
                    return (b.eventTsMs || 0) - (a.eventTsMs || 0);
                });
                const uniqueStrikes = sorted
                    .map(row => Number(row.strike))
                    .filter((strike, idx, arr) => idx === 0 || Math.abs(strike - arr[idx - 1]) > 0.0001);
                let baseStep = Infinity;
                for (let i = 1; i < uniqueStrikes.length; i += 1) {
                    const diff = Math.abs(uniqueStrikes[i] - uniqueStrikes[i - 1]);
                    if (diff > 0.0001 && diff < baseStep) baseStep = diff;
                }
                if (!isFinite(baseStep)) baseStep = 1;
                const adjacencyThreshold = Math.max(0.51, baseStep * 1.25);
                let run = [];
                const flushRun = () => {
                    if (!run.length) return;
                    if (run.length === 1) {
                        clustered.push(run[0]);
                        run = [];
                        return;
                    }
                    const strongest = run.reduce((best, row) => {
                        if (!best) return row;
                        if ((row.priority || 0) !== (best.priority || 0)) return (row.priority || 0) > (best.priority || 0) ? row : best;
                        return (row.eventTsMs || 0) > (best.eventTsMs || 0) ? row : best;
                    }, null);
                    const strikes = run.map(row => Number(row.strike)).sort((a, b) => a - b);
                    const start = strikes[0];
                    const end = strikes[strikes.length - 1];
                    const rangeLabel = _formatRailAlertStrike(start) + (Math.abs(end - start) > 0.0001 ? ('-' + _formatRailAlertStrike(end)) : '');
                    const kind = String(strongest.alert_type || strongest.kind || '');
                    const optionLabel = strongest.option_type === 'put' ? 'Put' : 'Call';
                    const title = kind === 'iv_surge'
                        ? 'IV surge'
                        : (kind === 'flow_pulse' ? 'burst' : 'heavy vol/OI');
                    const expiryLabel = strongest.expiry_text || strongest.expiry_iso || '';
                    const newestEventTsMs = Math.max.apply(null, run.map(row => row.eventTsMs || 0));
                    const ttlMs = strongest._ttlMs || (RAIL_ALERT_TTL_MS[strongest.tier] || RAIL_ALERT_TTL_MS.recent);
                    const ageMs = Math.max(0, Date.now() - newestEventTsMs);
                    clustered.push(Object.assign({}, strongest, {
                        id: 'cluster:' + kind + ':' + String(strongest.option_type || '') + ':' + String(strongest.expiry_iso || '') + ':' + _formatRailAlertStrike(start) + ':' + _formatRailAlertStrike(end),
                        text: optionLabel + ' ' + title + ' cluster @ ' + rangeLabel,
                        detail: run.length + ' adjacent strikes' + (expiryLabel ? (' · ' + expiryLabel) : ''),
                        strike: strongest.strike,
                        eventTsMs: newestEventTsMs,
                        lastSeenMs: Math.max.apply(null, run.map(row => row.lastSeenMs || 0)),
                        firstSeenMs: Math.min.apply(null, run.map(row => row.firstSeenMs || newestEventTsMs)),
                        refreshCount: run.reduce((sum, row) => sum + (row.refreshCount || 1), 0),
                        priority: (strongest.priority || 0) + Math.min(12, run.length * 3),
                        clusterCount: run.length,
                        isCluster: true,
                        clusterStrongestLabel: _formatRailAlertStrike(strongest.strike),
                        _ttlMs: ttlMs,
                        _ageMs: ageMs,
                        _freshness: Math.max(0, 1 - (ageMs / ttlMs)),
                    }));
                    run = [];
                };
                sorted.forEach(row => {
                    if (!run.length) {
                        run.push(row);
                        return;
                    }
                    const prev = run[run.length - 1];
                    const sameWindow = Math.abs((row.eventTsMs || 0) - (prev.eventTsMs || 0)) <= Math.max(row._ttlMs || 0, prev._ttlMs || 0, 60000);
                    const adjacentStrike = Math.abs(Number(row.strike) - Number(prev.strike)) <= adjacencyThreshold;
                    if (sameWindow && adjacentStrike) {
                        run.push(row);
                    } else {
                        flushRun();
                        run.push(row);
                    }
                });
                flushRun();
            });
            return clustered.concat(passthrough).sort((a, b) => {
                if ((b.priority || 0) !== (a.priority || 0)) return (b.priority || 0) - (a.priority || 0);
                if ((b._freshness || 0) !== (a._freshness || 0)) return (b._freshness || 0) - (a._freshness || 0);
                return (b.eventTsMs || 0) - (a.eventTsMs || 0);
            });
        }

        function _updateAlertsBadge() {
            const badge = document.getElementById('right-rail-alerts-badge');
            if (!badge) return;
            let unread = 0;
            if (activeRailTab !== 'overview') {
                for (const a of _lastRailAlerts) {
                    if (!_alertsSeenKeys.has(_alertSeenKey(a))) unread += 1;
                }
            }
            if (unread > 0) {
                badge.textContent = unread > 99 ? '99+' : String(unread);
                badge.classList.add('visible');
            } else {
                badge.textContent = '';
                badge.classList.remove('visible');
            }
        }

        function markRailAlertsSeen() {
            _alertsSeenKeys = new Set(_lastRailAlerts.map(a => _alertSeenKey(a)));
            _updateAlertsBadge();
        }

        function _renderRailAlertCard(a, options = {}) {
            const lvl = ['warn', 'info', 'flow'].includes(a.level) ? a.level : 'info';
            const ago = _relTimeMs(a.eventTsMs);
            const stale = a._ageMs >= (a._ttlMs * 0.65);
            const refreshed = a._ageMs <= 15000 && a.refreshCount > 1;
            const classes = ['rail-alert-item', lvl];
            if (options.lead) classes.push('top', 'lead');
            if (options.summary) classes.push('summary');
            if (stale) classes.push('stale');
            if (refreshed) classes.push('refreshed');
            if (options.muted) classes.push('muted');
            const tierLabel = a.tier === 'critical' ? 'hold'
                : (a.tier === 'active' ? 'active' : 'recent');
            const countCls = a.clusterCount >= 8 ? ' count-strong' : (a.clusterCount >= 4 ? ' count-mid' : '');
            const direction = _railAlertDirectionMeta(a.direction_label);
            return '<div class="' + classes.join(' ') + '">' +
                       '<div class="rail-alert-topline">' +
                           '<span class="rail-alert-topline-left">' +
                               '<span class="rail-alert-tag">' + (options.summary ? 'buffered' : (lvl === 'warn' ? 'active' : (lvl === 'flow' ? 'flow' : 'info'))) + '</span>' +
                               (!options.summary ? '<span class="rail-alert-tier">' + tierLabel + '</span>' : '') +
                               (a.direction_classifiable && !options.summary ? '<span class="rail-alert-direction ' + direction.cls + '" title="' + _escapeHtml(a.direction_hint || '') + '">' + direction.text + '</span>' : '') +
                               (a.clusterCount > 1 ? '<span class="rail-alert-tier' + countCls + '">x' + a.clusterCount + '</span>' : '') +
                           '</span>' +
                           (ago ? '<span class="rail-alert-ago">' + ago + '</span>' : '') +
                       '</div>' +
                       '<div class="rail-alert-text">' + _escapeHtml(a.text) + '</div>' +
                       (a.clusterStrongestLabel ? '<div class="rail-alert-strongest">Strongest <span class="rail-alert-strongest-value">' + _escapeHtml(a.clusterStrongestLabel) + '</span></div>' : '') +
                       (a.detail ? '<div class="rail-alert-detail">' + _escapeHtml(a.detail) + '</div>' : '') +
                   '</div>';
        }

        function _renderRailAlertOverflowCard(items) {
            if (!items.length) return '';
            const critical = items.filter(a => a.tier === 'critical').length;
            const active = items.filter(a => a.tier === 'active').length;
            const recent = items.length - critical - active;
            const parts = [];
            if (critical > 0) parts.push(critical + ' hold');
            if (active > 0) parts.push(active + ' active');
            if (recent > 0) parts.push(recent + ' recent');
            return '<div class="rail-alert-item info summary">' +
                       '<div class="rail-alert-topline">' +
                           '<span class="rail-alert-tag">buffered</span>' +
                           '<span class="rail-alert-ago">+' + items.length + '</span>' +
                       '</div>' +
                       '<div class="rail-alert-summary-count">+' + items.length + ' more</div>' +
                       '<div class="rail-alert-summary-text">' + _escapeHtml(parts.join(' · ') || 'Buffered alerts held off-screen.') + '</div>' +
                   '</div>';
        }

        function renderRailAlerts(list, options = {}) {
            const reset = !!(options && options.reset);
            const target = document.getElementById('right-rail-alerts');
            const titleNote = document.getElementById('rail-alerts-title-note');
            _ensureRailAlertScope(reset);
            if (reset) {
                _lastRailAlerts = [];
                if (target) target.innerHTML = '<div class="rail-alerts-empty">No active alerts.</div>';
                if (titleNote) titleNote.textContent = 'Mixed Lean';
                _updateAlertsBadge();
                return;
            }
            _mergeRailAlerts(list);
            _pruneRailAlertBuffer();
            const buffered = _clusterRailAlerts(_getBufferedRailAlerts());
            _lastRailAlerts = buffered;
            if (titleNote) titleNote.textContent = _liveAlertsSummaryText(buffered);
            if (target) {
                if (!buffered.length) {
                    target.innerHTML = '<div class="rail-alerts-empty">No active alerts.</div>';
                } else {
                    const pinned = buffered[0];
                    const supporting = buffered.slice(1, 4);
                    const overflow = buffered.slice(4);
                    target.innerHTML = [
                        _renderRailAlertCard(pinned, { lead: true }),
                        ...supporting.map((a, idx) => _renderRailAlertCard(a, { muted: idx >= 2 })),
                        _renderRailAlertOverflowCard(overflow),
                    ].filter(Boolean).join('');
                }
            }
            if (activeRailTab === 'overview') {
                markRailAlertsSeen();
            } else {
                _updateAlertsBadge();
            }
        }

        // ── Right-rail Key Levels table ──────────────────────────────────
        function _fmtLvlPrice(n) {
            return (n == null || !isFinite(n)) ? '—' : ('$' + n.toFixed(2));
        }
        function _fmtSignedDollar(n) {
            if (n == null || !isFinite(n)) return '—';
            if (Math.abs(n) < 0.005) return '$0.00';
            return (n > 0 ? '+' : '-') + '$' + Math.abs(n).toFixed(2);
        }
        function _fmtLvlDist(price, spot) {
            if (price == null || spot == null || !isFinite(price) || !isFinite(spot) || spot === 0) {
                return { primary: '—', secondary: '', cls: '' };
            }
            const delta = price - spot;
            const pct = (price - spot) / spot * 100;
            const sign = pct > 0 ? '+' : '';
            return {
                primary: _fmtSignedDollar(delta),
                secondary: sign + pct.toFixed(2) + '%',
                cls: pct >= 0 ? 'pos' : 'neg',
            };
        }
        function _fmtLvlDrift(delta) {
            if (delta == null || !isFinite(delta)) return { primary: '—', secondary: '', cls: '' };
            if (Math.abs(delta) < 0.005) return { primary: 'flat', secondary: '', cls: '' };
            return {
                primary: _fmtSignedDollar(delta),
                secondary: '',
                cls: delta > 0 ? 'pos' : 'neg',
            };
        }
        function renderRailKeyLevels(stats) {
            const target = document.getElementById('right-rail-levels');
            if (!target) return;
            if (!stats) {
                target.innerHTML = '<div class="lvl-empty">Key levels load with stream data.</div>';
                return;
            }
            const spot = stats.spot;
            const levelDeltas = (stats && stats.level_deltas) || {};
            const rows = [
                { key: 'call_wall',  label: 'Call Wall',  price: stats.call_wall,  tone: 'call' },
                { key: 'put_wall',   label: 'Put Wall',   price: stats.put_wall,   tone: 'put'  },
                { key: 'gamma_flip', label: 'Gamma Flip', price: stats.gamma_flip, tone: 'flip' },
                { key: 'em_upper',   label: '+1σ EM',     price: stats.em_upper,   tone: 'em'   },
                { key: 'em_lower',   label: '-1σ EM',     price: stats.em_lower,   tone: 'em'   },
            ];
            const validRows = rows
                .filter(r => r.price != null && isFinite(r.price))
                .map(r => Object.assign({}, r, {
                    absDist: (spot != null && isFinite(spot)) ? Math.abs(r.price - spot) : Number.POSITIVE_INFINITY,
                }))
                .sort((a, b) => a.absDist - b.absDist);
            const hasAny = validRows.length > 0;
            if (!hasAny) {
                target.innerHTML = '<div class="lvl-empty">Key levels load with stream data.</div>';
                return;
            }
            const body = validRows.map((r, idx) => {
                const d = _fmtLvlDist(r.price, spot);
                const drift = _fmtLvlDrift(levelDeltas[r.key]);
                return '<div class="rail-level-item ' + r.tone + (idx === 0 ? ' nearest' : '') + '">' +
                           '<div class="rail-level-top">' +
                               '<div class="rail-level-main">' +
                                   '<div class="rail-level-title-row">' +
                                       '<span class="rail-level-swatch"></span>' +
                                       '<span class="rail-level-name">' + _escapeHtml(r.label) + '</span>' +
                                       (idx === 0 ? '<span class="rail-level-chip">Nearest</span>' : '') +
                                   '</div>' +
                               '</div>' +
                               '<div class="rail-level-price">' +
                                   '<span class="secondary">Price</span>' +
                                   '<span class="primary">' + _fmtLvlPrice(r.price) + '</span>' +
                               '</div>' +
                           '</div>' +
                           '<div class="rail-level-metrics">' +
                               '<div class="rail-level-stat ' + d.cls + '">' +
                                   '<span class="rail-level-stat-label">Δ Spot</span>' +
                                   '<span class="primary">' + d.primary + '</span>' +
                                   (d.secondary ? '<span class="secondary">' + d.secondary + '</span>' : '') +
                               '</div>' +
                               '<div class="rail-level-stat ' + drift.cls + '">' +
                                   '<span class="rail-level-stat-label">Since Open</span>' +
                                   '<span class="primary">' + drift.primary + '</span>' +
                                   (drift.secondary ? '<span class="secondary">' + drift.secondary + '</span>' : '') +
                               '</div>' +
                           '</div>' +
                       '</div>';
            }).join('');
            const regimeCls = stats.regime === 'Long Gamma' ? 'pos' : (stats.regime === 'Short Gamma' ? 'neg' : '');
            target.innerHTML =
                '<div class="rail-levels-summary">' +
                    '<div>' +
                        '<div class="spot-label">Spot</div>' +
                        '<div class="spot-price">' + _fmtLvlPrice(spot) + '</div>' +
                    '</div>' +
                    '<div class="spot-regime ' + regimeCls + '">' + _escapeHtml(stats.regime || '—') + '</div>' +
                '</div>' +
                body;
        }

        // ── Secondary chart tabs ───────────────────────────────────────────
        let secondaryActiveTab = (() => {
            try { return localStorage.getItem(SECONDARY_TAB_KEY) || null; } catch(e) { return null; }
        })();
        const FLOW_BLOTTER_STATE_KEY = 'gex.flowBlotterState';
        const secondaryTabLabels = {
            gamma: 'Gamma', delta: 'Delta', vanna: 'Vanna', charm: 'Charm',
            speed: 'Speed', vomma: 'Vomma', color: 'Color',
            options_volume: 'Options Vol', open_interest: 'Open Interest',
            volume_ratio: 'Vol Ratio', options_chain: 'Chain',
            premium: 'Premium', large_trades: 'Flow Blotter',
        };
        function updateSecondaryTabs(chartIds) {
            const grid = document.querySelector('.charts-grid');
            if (!grid) return;
            let bar = document.getElementById('secondary-tabs');
            if (!chartIds.length || chartIds.length === 1) {
                if (bar) bar.remove();
                if (chartIds.length === 1) secondaryActiveTab = chartIds[0];
                applySecondaryTabVisibility();
                return;
            }
            if (!bar) {
                bar = document.createElement('div');
                bar.id = 'secondary-tabs';
                bar.className = 'secondary-tabs';
                grid.parentNode.insertBefore(bar, grid);
            }
            if (!chartIds.includes(secondaryActiveTab)) secondaryActiveTab = chartIds[0];
            bar.innerHTML = chartIds.map(id =>
                `<button class="secondary-tab${id === secondaryActiveTab ? ' active' : ''}" data-tab="${id}">${secondaryTabLabels[id] || id}</button>`
            ).join('');
            bar.querySelectorAll('.secondary-tab').forEach(btn => {
                btn.addEventListener('click', () => {
                    secondaryActiveTab = btn.dataset.tab;
                    try { localStorage.setItem(SECONDARY_TAB_KEY, secondaryActiveTab); } catch(e) {}
                    bar.querySelectorAll('.secondary-tab').forEach(b =>
                        b.classList.toggle('active', b.dataset.tab === secondaryActiveTab));
                    applySecondaryTabVisibility();
                });
            });
            grid.classList.add('tabbed');
            applySecondaryTabVisibility();
        }
        function applySecondaryTabVisibility() {
            const grid = document.querySelector('.charts-grid');
            if (!grid) return;
            grid.querySelectorAll('.chart-container').forEach(el => {
                const id = el.id.replace('-chart', '');
                const hide = id !== secondaryActiveTab;
                el.classList.toggle('tab-hidden', hide);
                if (!hide) {
                    try { Plotly.Plots.resize(el); } catch (e) {}
                }
            });
        }
        function initFlowBlotter(container) {
            if (!container) return;
            const root = container.querySelector('.flow-blotter');
            if (!root || root.dataset.bound === '1') return;
            root.dataset.bound = '1';

            const tbody = root.querySelector('tbody');
            if (!tbody) return;
            const rows = Array.from(tbody.querySelectorAll('tr[data-flow-row="1"]'));
            const filterButtons = Array.from(root.querySelectorAll('[data-flow-type]'));
            const minPremiumInput = root.querySelector('[data-flow-min-premium]');
            const resetButton = root.querySelector('[data-flow-reset]');
            const summaryEl = root.querySelector('[data-flow-summary]');
            const emptyStateEl = root.querySelector('[data-flow-empty]');
            const sortButtons = Array.from(root.querySelectorAll('[data-sort-key]'));

            function loadFlowState() {
                try {
                    const raw = localStorage.getItem(FLOW_BLOTTER_STATE_KEY);
                    const parsed = raw ? JSON.parse(raw) : null;
                    return parsed && typeof parsed === 'object' ? parsed : {};
                } catch (e) {
                    return {};
                }
            }
            function saveFlowState() {
                try {
                    localStorage.setItem(FLOW_BLOTTER_STATE_KEY, JSON.stringify({
                        activeType,
                        sortKey,
                        sortDir,
                        minPremium: minPremiumInput ? (parseFloat(minPremiumInput.value) || 0) : 0,
                    }));
                } catch (e) {}
            }

            const savedState = loadFlowState();
            let activeType = savedState.activeType || root.dataset.initialType || 'all';
            let sortKey = savedState.sortKey || root.dataset.defaultSort || 'premium';
            let sortDir = savedState.sortDir || root.dataset.defaultDir || 'desc';
            if (minPremiumInput) {
                minPremiumInput.value = String(Math.max(0, Number(savedState.minPremium) || 0));
            }

            function compactUsd(value) {
                const amount = Number(value) || 0;
                if (Math.abs(amount) >= 1e9) return '$' + (amount / 1e9).toFixed(2) + 'B';
                if (Math.abs(amount) >= 1e6) return '$' + (amount / 1e6).toFixed(2) + 'M';
                if (Math.abs(amount) >= 1e3) return '$' + (amount / 1e3).toFixed(1) + 'K';
                return '$' + amount.toFixed(0);
            }

            function readSortValue(row, key, type) {
                const raw = row.dataset[key] || '';
                if (type === 'string') return raw.toLowerCase();
                const num = parseFloat(raw);
                return Number.isFinite(num) ? num : 0;
            }

            function updateFilterButtons() {
                filterButtons.forEach(btn => {
                    const active = btn.dataset.flowType === activeType;
                    btn.classList.toggle('active', active);
                    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
                });
            }

            function updateSortIndicators() {
                sortButtons.forEach(btn => {
                    const indicator = btn.querySelector('[data-sort-indicator]');
                    if (!indicator) return;
                    if (btn.dataset.sortKey === sortKey) {
                        indicator.textContent = sortDir === 'desc' ? '↓' : '↑';
                        indicator.style.color = 'var(--accent)';
                    } else {
                        indicator.textContent = '↕';
                        indicator.style.color = 'var(--fg-2)';
                    }
                });
            }

            function applyFiltersAndSort() {
                const minPremium = minPremiumInput ? Math.max(0, parseFloat(minPremiumInput.value) || 0) : 0;
                const activeSortButton = root.querySelector(`[data-sort-key="${sortKey}"]`);
                const sortType = activeSortButton ? (activeSortButton.dataset.sortType || 'number') : 'number';
                const visibleRows = [];
                const hiddenRows = [];

                rows.forEach(row => {
                    const matchesType = activeType === 'all' || row.dataset.optionType === activeType;
                    const premium = parseFloat(row.dataset.premium) || 0;
                    const matchesPremium = premium >= minPremium;
                    if (matchesType && matchesPremium) visibleRows.push(row);
                    else hiddenRows.push(row);
                });

                visibleRows.sort((a, b) => {
                    const aVal = readSortValue(a, sortKey, sortType);
                    const bVal = readSortValue(b, sortKey, sortType);
                    let cmp = 0;
                    if (sortType === 'string') cmp = aVal.localeCompare(bVal);
                    else if (aVal !== bVal) cmp = aVal < bVal ? -1 : 1;
                    if (cmp !== 0) return sortDir === 'desc' ? -cmp : cmp;
                    return (parseFloat(b.dataset.premium) || 0) - (parseFloat(a.dataset.premium) || 0);
                });

                visibleRows.forEach(row => {
                    row.hidden = false;
                    tbody.appendChild(row);
                });
                hiddenRows.forEach(row => {
                    row.hidden = true;
                    tbody.appendChild(row);
                });

                const visiblePremium = visibleRows.reduce((sum, row) => sum + (parseFloat(row.dataset.premium) || 0), 0);
                if (summaryEl) {
                    const shownText = `${visibleRows.length.toLocaleString()} shown` +
                        (rows.length !== visibleRows.length ? ` of ${rows.length.toLocaleString()}` : '');
                    const premiumText = `Approx premium ${compactUsd(visiblePremium)}`;
                    const thresholdText = minPremium > 0 ? ` · Min prem ${compactUsd(minPremium)}` : '';
                    summaryEl.textContent = shownText + ' · ' + premiumText + thresholdText;
                }
                if (emptyStateEl) {
                    emptyStateEl.hidden = !(rows.length > 0 && visibleRows.length === 0);
                }

                saveFlowState();
                updateFilterButtons();
                updateSortIndicators();
            }

            filterButtons.forEach(btn => {
                btn.addEventListener('click', () => {
                    activeType = btn.dataset.flowType || 'all';
                    applyFiltersAndSort();
                });
            });

            sortButtons.forEach(btn => {
                btn.addEventListener('click', () => {
                    const nextKey = btn.dataset.sortKey || 'premium';
                    if (sortKey === nextKey) {
                        sortDir = sortDir === 'desc' ? 'asc' : 'desc';
                    } else {
                        sortKey = nextKey;
                        sortDir = 'desc';
                    }
                    applyFiltersAndSort();
                });
            });

            if (minPremiumInput) {
                minPremiumInput.addEventListener('input', applyFiltersAndSort);
                minPremiumInput.addEventListener('change', applyFiltersAndSort);
            }

            if (resetButton) {
                resetButton.addEventListener('click', () => {
                    activeType = root.dataset.initialType || 'all';
                    sortKey = root.dataset.defaultSort || 'premium';
                    sortDir = root.dataset.defaultDir || 'desc';
                    if (minPremiumInput) minPremiumInput.value = '0';
                    applyFiltersAndSort();
                });
            }

            applyFiltersAndSort();
        }

        // ── Key levels (Call Wall / Put Wall / Gamma Flip / ±1σ EM) ──────────
        function clearKeyLevels() {
            tvKeyLevelPrices = [];
            if (!tvCandleSeries) { tvKeyLevelLines = []; return; }
            tvKeyLevelLines.forEach(l => {
                try { tvCandleSeries.removePriceLine(l); } catch (e) {}
            });
            tvKeyLevelLines = [];
            tvRefreshOverlayLevelPrices();
        }

        function renderKeyLevels(levels) {
            clearKeyLevels();
            if (!levels || !tvCandleSeries || !window.LightweightCharts) return;
            const vis = getChartVisibility();
            const showWalls2  = vis.walls_2          !== false;
            const showHvl     = vis.hvl              !== false;
            const showEm2     = vis.em_2s            !== false;
            const showLiveGex = vis.live_gex_extrema !== false;
            const defs = [
                { key: 'call_wall',   show: true },
                { key: 'put_wall',    show: true },
                { key: 'gamma_flip',  show: true },
                { key: 'em_upper',    show: true },
                { key: 'em_lower',    show: true },
                { key: 'call_wall_2', show: showWalls2 },
                { key: 'put_wall_2',  show: showWalls2 },
                { key: 'hvl',         show: showHvl },
                { key: 'max_positive_gex', show: showLiveGex },
                { key: 'max_negative_gex', show: showLiveGex },
                { key: 'em_upper_2',  show: showEm2 },
                { key: 'em_lower_2',  show: showEm2 },
            ];
            const renderedPrices = [];
            defs.forEach(def => {
                if (def.show === false) return;
                const pref = getPriceLevelPref(def.key);
                if (!pref || pref.visible === false) return;
                const entry = levels[def.key];
                if (!entry || entry.price == null || !isFinite(entry.price)) return;
                try {
                    const line = tvCandleSeries.createPriceLine({
                        price: entry.price,
                        color: pref.color,
                        lineWidth: pref.lineWidth,
                        lineStyle: tvIndicatorLineStyleValue(pref.lineStyle),
                        axisLabelVisible: true,
                        title: pref.label,
                    });
                    tvKeyLevelLines.push(line);
                    renderedPrices.push(entry.price);
                } catch (e) {
                    console.warn('createPriceLine failed for', pref.label, e);
                }
            });
            tvKeyLevelPrices = renderedPrices.filter(price => Number.isFinite(price));
            tvRefreshOverlayLevelPrices();
        }

        function tvRefreshOverlayLevelPrices() {
            tvAllLevelPrices = (tvHistoricalPoints || []).map(point => point.price);
            tvAllLevelPrices.push(...tvKeyLevelPrices, ...tvSessionLevelPrices, ...tvTopOIPrices);
            tvDrawingDefs.forEach(def => {
                if (!def) return;
                if (def.type === 'hline' || def.type === 'text') {
                    tvAllLevelPrices.push(def.price);
                } else if (def.type === 'trendline') {
                    tvAllLevelPrices.push(def.p1, def.p2);
                } else if (def.type === 'channel') {
                    const channel = tvComputeChannelData(def);
                    if (channel) {
                        tvAllLevelPrices.push(channel.p1, channel.p2);
                        if (!channel.vertical) {
                            tvAllLevelPrices.push(channel.q1, channel.q2, channel.mid1, channel.mid2);
                        }
                    }
                } else if (def.type === 'rect') {
                    tvAllLevelPrices.push(def.top, def.bot);
                }
            });
            tvAllLevelPrices = tvAllLevelPrices.filter(price => Number.isFinite(price));
            tvApplyAutoscale();
        }

        function clearSessionLevels() {
            tvSessionLevelPrices = [];
            if (!tvCandleSeries) { tvSessionLevelLines = []; return; }
            tvSessionLevelLines.forEach(line => {
                try { tvCandleSeries.removePriceLine(line); } catch (e) {}
            });
            tvSessionLevelLines = [];
            scheduleSessionLevelCloudDraw();
            tvRefreshOverlayLevelPrices();
        }

        function renderSessionLevels(levels, rawSettings) {
            clearSessionLevels();
            const settings = normalizeSessionLevelSettings(rawSettings);
            if (!settings.enabled || !levels || !tvCandleSeries || !window.LightweightCharts) return;

            const overlayLevelPrices = (tvKeyLevelPrices || []).filter(price => Number.isFinite(price));
            const tick = 0.01;
            const renderedPrices = [];
            const shouldSkipPrice = (price) => {
                if (!Number.isFinite(price)) return true;
                if (overlayLevelPrices.some(levelPrice => Math.abs(levelPrice - price) <= tick)) return true;
                if (renderedPrices.some(levelPrice => Math.abs(levelPrice - price) <= tick)) return true;
                return false;
            };
            const formatTitle = (entry) => {
                const base = settings.abbreviate_labels
                    ? (entry.short_label || entry.label || '')
                    : (entry.full_label || entry.label || '');
                if (!settings.append_price) return base;
                const price = Number(entry.price);
                return `${base} ${price.toFixed(2)}`.trim();
            };
            const defs = [
                { key: 'ib_high',            enabled: settings.initial_balance },
                { key: 'ib_low',             enabled: settings.initial_balance },
                { key: 'ib_mid',             enabled: settings.initial_balance && settings.show_ib_mid },
                { key: 'ib_high_x2',         enabled: settings.initial_balance && settings.show_ib_extensions },
                { key: 'ib_low_x2',          enabled: settings.initial_balance && settings.show_ib_extensions },
                { key: 'ib_high_x3',         enabled: settings.initial_balance && settings.show_ib_extensions },
                { key: 'ib_low_x3',          enabled: settings.initial_balance && settings.show_ib_extensions },
                { key: 'opening_range_high', enabled: settings.opening_range },
                { key: 'opening_range_low',  enabled: settings.opening_range },
                { key: 'opening_range_mid',  enabled: settings.opening_range && settings.show_or_mid },
                { key: 'today_open',         enabled: settings.today },
                { key: 'today_high',         enabled: settings.today },
                { key: 'today_low',          enabled: settings.today },
                { key: 'yesterday_high',     enabled: settings.yesterday },
                { key: 'yesterday_low',      enabled: settings.yesterday },
                { key: 'yesterday_open',     enabled: settings.yesterday },
                { key: 'yesterday_close',    enabled: settings.yesterday },
                { key: 'near_open_high',     enabled: settings.near_open },
                { key: 'near_open_low',      enabled: settings.near_open },
                { key: 'premarket_high',     enabled: settings.premarket },
                { key: 'premarket_low',      enabled: settings.premarket },
                { key: 'after_hours_high',   enabled: settings.after_hours },
                { key: 'after_hours_low',    enabled: settings.after_hours },
            ];
            defs.forEach(def => {
                if (!def.enabled) return;
                const pref = getPriceLevelPref(def.key);
                if (!pref || pref.visible === false) return;
                const entry = levels[def.key];
                if (!entry || !Number.isFinite(entry.price) || shouldSkipPrice(entry.price)) return;
                try {
                    const line = tvCandleSeries.createPriceLine({
                        price: entry.price,
                        color: pref.color,
                        lineWidth: pref.lineWidth,
                        lineStyle: tvIndicatorLineStyleValue(pref.lineStyle),
                        axisLabelVisible: true,
                        title: formatTitle(entry),
                    });
                    tvSessionLevelLines.push(line);
                    tvSessionLevelPrices.push(entry.price);
                    renderedPrices.push(entry.price);
                } catch (e) {
                    console.warn('createPriceLine failed for session level', def.key, e);
                }
            });
            scheduleSessionLevelCloudDraw();
            tvRefreshOverlayLevelPrices();
        }

        // ── Top-OI overlay (dotted price lines, nearest expiry) ───────────────
        function clearTopOILines() {
            const hadLines = tvTopOILines.length > 0 || tvTopOIPrices.length > 0;
            tvTopOIPrices = [];
            if (!tvCandleSeries) { tvTopOILines = []; return; }
            tvTopOILines.forEach(l => { try { tvCandleSeries.removePriceLine(l); } catch (e) {} });
            tvTopOILines = [];
            tvRefreshOverlayLevelPrices();
            return hadLines;
        }

        function renderTopOI(topOi) {
            const cleared = clearTopOILines();
            if (!tvActiveInds.has('oi') || !topOi || !tvCandleSeries || !window.LightweightCharts) {
                if (cleared && tvCandleSeries && tvAutoRange) {
                    tvApplyAutoscale();
                    tvRefreshPriceScale();
                }
                return;
            }
            const LS = LightweightCharts.LineStyle;
            const callC = getComputedStyle(document.documentElement).getPropertyValue('--call').trim() || '#10B981';
            const putC  = getComputedStyle(document.documentElement).getPropertyValue('--put').trim()  || '#EF4444';
            const goldColor = getComputedStyle(document.documentElement).getPropertyValue('--gold').trim() || '#D4AF37';

            const map = new Map();
            (topOi.calls || []).forEach((r, idx) => map.set(r.strike, { color: callC, title: 'C OI #' + (idx + 1) + ' ' + r.strike }));
            (topOi.puts  || []).forEach((r, idx) => {
                if (map.has(r.strike)) map.set(r.strike, { color: goldColor, title: '\u2605 ' + r.strike });
                else                   map.set(r.strike, { color: putC,  title: 'P OI #' + (idx + 1) + ' ' + r.strike });
            });

            map.forEach(({ color, title }, strike) => {
                try {
                    const line = tvCandleSeries.createPriceLine({
                        price: strike, color, lineWidth: 1,
                        lineStyle: LS.Dotted, lineVisible: true, axisLabelVisible: true, title,
                    });
                    tvTopOILines.push(line);
                    tvTopOIPrices.push(strike);
                } catch (e) { console.warn('renderTopOI createPriceLine failed:', e); }
            });
            tvRefreshOverlayLevelPrices();
            if (tvTopOILines.length && tvAutoRange) {
                tvRefreshPriceScale();
            }
        }

        // Fetch top-OI from /update when user toggles on before /update has populated it
        function ensureTopOILoaded() {
            const requestTopOIContextKey = getTopOIContextKey();
            if ((_lastTopOI && _lastTopOIContextKey === requestTopOIContextKey) || _topOIFetchInFlight) return;
            const t = (document.getElementById('ticker') && document.getElementById('ticker').value || '').trim();
            const expiry = getSelectedExpiryValues();
            if (!t || !expiry.length) return;
            const exposureMetricEl = document.getElementById('exposure_metric');
            const deltaAdjustedEl = document.getElementById('delta_adjusted_exposures');
            const calculateInNotionalEl = document.getElementById('calculate_in_notional');
            const strikeRangeEl = document.getElementById('strike_range');
            _topOIFetchInFlight = true;
            fetch('/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ticker: t,
                    expiry,
                    exposure_metric: exposureMetricEl ? exposureMetricEl.value : 'Open Interest',
                    delta_adjusted: !!(deltaAdjustedEl && deltaAdjustedEl.checked),
                    calculate_in_notional: !!(calculateInNotionalEl && calculateInNotionalEl.checked),
                    top_oi_count: getTopOICountSetting(),
                    strike_range: strikeRangeEl ? (parseFloat(strikeRangeEl.value) / 100) : 0.1,
                    gate_alerts: !!(document.getElementById('gate_alerts') && document.getElementById('gate_alerts').checked),
                    show_gamma: false,
                    show_delta: false,
                    show_vanna: false,
                    show_charm: false,
                }),
            }).then(r => r.json()).then(d => {
                if (d && d.top_oi && requestTopOIContextKey === getTopOIContextKey()) {
                    _lastTopOI = d.top_oi;
                    _lastTopOIContextKey = requestTopOIContextKey;
                    if (tvActiveInds.has('oi')) renderTopOI(_lastTopOI);
                }
            }).catch(() => {}).finally(() => { _topOIFetchInFlight = false; });
        }
        let _topOIFetchInFlight = false;

        // ── Throttled price history fetcher ───────────────────────────────────
        // Fetches candle history + exposure levels from /update_price.
        // Real-time ticks come from SSE; this only handles the historical snapshot
        // and exposure level overlays, so it runs at most every 30 seconds unless
        // forced (ticker change or visible settings change).
        let _priceHistoryLastMs = 0;
        let _priceHistoryLastKey = '';
        let _priceHistoryInFlight = false;

        function buildPricePayload() {
            return {
                ticker: document.getElementById('ticker').value,
                timeframe: document.getElementById('timeframe').value,
                call_color: callColor,
                put_color: putColor,
                levels_types: Array.from(document.querySelectorAll('.levels-option input:checked')).map(cb => cb.value),
                levels_count: parseInt(document.getElementById('levels_count').value),
                use_heikin_ashi: document.getElementById('use_heikin_ashi').checked,
                strike_range: parseFloat(document.getElementById('strike_range').value) / 100,
                highlight_max_level: document.getElementById('highlight_max_level').checked,
                max_level_color: maxLevelColor,
                coloring_mode: document.getElementById('coloring_mode').value,
                top_oi_count: getTopOICountSetting(),
                session_levels: getSessionLevelSettingsFromDom(),
                gate_alerts: !!(document.getElementById('gate_alerts') && document.getElementById('gate_alerts').checked),
            };
        }

        function fetchPriceHistory(force) {
            if (!isChartVisible('price')) return;
            if (_priceHistoryInFlight) return;
            const payload = buildPricePayload();
            const key = JSON.stringify(payload);
            const requestTopOIContextKey = getTopOIContextKey();
            const now = Date.now();
            // Skip if nothing changed and it's been less than 30 seconds
            if (!force && key === _priceHistoryLastKey && now - _priceHistoryLastMs < 30000) return;
            _priceHistoryLastMs = now;
            _priceHistoryLastKey = key;
            _priceHistoryInFlight = true;
            fetch('/update_price', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: key
            })
            .then(r => r.json())
            .then(priceResp => {
                _lastSessionLevels = priceResp ? (priceResp.session_levels || null) : null;
                _lastSessionLevelsMeta = priceResp ? (priceResp.session_levels_meta || null) : null;
                _lastKeyLevels = priceResp ? (priceResp.key_levels || null) : null;
                _lastKeyLevels0dte = priceResp ? (priceResp.key_levels_0dte || null) : null;
                _lastStats0dte = priceResp ? (priceResp.stats_0dte || null) : null;
                if (!priceResp.error && priceResp.price) {
                    applyPriceData(priceResp.price);
                } else {
                    renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
                }
                if (priceResp && priceResp.top_oi && requestTopOIContextKey === getTopOIContextKey()) {
                    _lastTopOI = priceResp.top_oi;
                    _lastTopOIContextKey = requestTopOIContextKey;
                    if (tvActiveInds.has('oi')) renderTopOI(_lastTopOI);
                }
                renderGexSidePanel(priceResp ? priceResp.gex_panel : null);
                renderTraderStats(priceResp ? (priceResp.trader_stats || null) : null);
                redrawGexScope();
            })
            .catch(err => console.error('Error fetching price chart:', err))
            .finally(() => { _priceHistoryInFlight = false; });
        }

        function updateCharts(data, topOiContextKey = getTopOIContextKey()) {
            // Save scroll position before any DOM changes
            savedScrollPosition = window.scrollY || window.pageYOffset;

            if (data.top_oi && topOiContextKey === getTopOIContextKey()) {
                _lastTopOI = data.top_oi;
                _lastTopOIContextKey = topOiContextKey;
                if (tvActiveInds.has('oi')) renderTopOI(_lastTopOI);
            }
            
            const selectedCharts = getChartVisibility();
            applyStrikeRailTabs(selectedCharts);
            
            // Handle price chart separately (TradingView Lightweight Charts)
            if (selectedCharts.price && data.price) {
                ensurePriceChartDom();
                showPriceChartUI();

                const priceData = typeof data.price === 'string' ? JSON.parse(data.price) : data.price;
                if (!priceData.error) {
                    renderTVPriceChart(priceData);
                }
            } else if (!selectedCharts.price) {
                const priceContainer = document.querySelector('.price-chart-container');
                if (priceContainer) priceContainer.style.display = 'none';
                const toolbarShell = document.getElementById('workspace-toolbar-shell');
                if (toolbarShell) toolbarShell.style.display = 'none';
                const toolbar = document.getElementById('tv-toolbar-container');
                if (toolbar) toolbar.style.display = 'none';
                const gexHeader = document.getElementById('gex-col-header');
                if (gexHeader) gexHeader.style.display = 'none';
                const gexCol = document.getElementById('gex-column');
                if (gexCol) gexCol.style.display = 'none';
                const railTabs = document.getElementById('right-rail-tabs');
                if (railTabs) railTabs.style.display = 'none';
                const railPanels = document.getElementById('right-rail-panels');
                if (railPanels) railPanels.style.display = 'none';
                const flowEventLane = document.getElementById('flow-event-lane');
                if (flowEventLane) flowEventLane.style.display = 'none';
                tvDrawStart = null;
                tvDrawingPreviewDef = null;
                tvSelectedDrawingId = null;
                destroyRsiPane();
                destroyMacdPane();
                if (tvPriceChart) {
                    try { tvPriceChart.unsubscribeClick(tvHandleChartClick); } catch(e){}
                    clearTVUserHLinePriceLines();
                    tvPriceChart.remove();
                    tvPriceChart = null;
                    tvCandleSeries = null;
                    tvVolumeSeries = null;
                    tvIndicatorSeries = {};
                    tvIndicatorDataCache = {};
                    tvHistoricalPoints = [];
                    tvHistoricalExpectedMoveSeries = [];
                    tvKeyLevelLines = [];
                    tvSessionLevelLines = [];
                    tvTopOILines = [];
                    tvKeyLevelPrices = [];
                    tvSessionLevelPrices = [];
                    tvTopOIPrices = [];
                }
                if (tvResizeObserver) {
                    tvResizeObserver.disconnect();
                    tvResizeObserver = null;
                }
            }
            
            // Handle other charts
            let chartsGrid = document.querySelector('.charts-grid');
            if (!chartsGrid) {
                chartsGrid = document.createElement('div');
                chartsGrid.className = 'charts-grid';
                document.getElementById('chart-grid').appendChild(chartsGrid);
            }
            
            // Check if we need to rebuild the grid (enabled charts changed)
            const currentChartIds = Array.from(chartsGrid.querySelectorAll('.chart-container')).map(el => el.id.replace('-chart', ''));
            
            // Count enabled regular charts (excluding price)
            const regularCharts = Object.entries(selectedCharts).filter(([key, selected]) => 
                selected && !['price'].includes(key) && data[key]
            );

            const utilityCharts = regularCharts.filter(([key]) => !STRIKE_RAIL_CHART_IDS.includes(key));
            const utilityChartIds = utilityCharts.map(([key]) => key);
            const needsGridRebuild = utilityChartIds.length !== currentChartIds.length ||
                                     !utilityChartIds.every((id, i) => currentChartIds[i] === id);
            
            // Hide the charts grid if no regular charts are enabled
            if (utilityCharts.length === 0) {
                chartsGrid.style.display = 'none';
                chartsGrid.innerHTML = '';
                updateSecondaryTabs([]);
            } else {
                chartsGrid.style.display = 'block';

                // Only rebuild if chart selection changed
                if (needsGridRebuild) {
                    chartsGrid.innerHTML = '';
                    chartsGrid.className = 'charts-grid tabbed';
                    utilityCharts.forEach(([key, selected]) => {
                        const newContainer = document.createElement('div');
                        newContainer.className = 'chart-container';
                        newContainer.id = `${key}-chart`;
                        chartsGrid.appendChild(newContainer);
                        chartContainerCache[key] = newContainer;
                    });
                }
                updateSecondaryTabs(utilityChartIds);
                
                // Update chart data
                utilityCharts.forEach(([key, selected]) => {
                    let container = document.getElementById(`${key}-chart`);
                    if (!container) {
                        container = document.createElement('div');
                        container.className = 'chart-container';
                        container.id = `${key}-chart`;
                        chartsGrid.appendChild(container);
                    }
                    
                    try {
                        // Flow blotter is HTML, not a Plotly figure.
                        if (key === 'large_trades') {
                            // Only update if content changed
                            if (container.innerHTML !== data[key]) {
                                container.innerHTML = data[key];
                            }
                            initFlowBlotter(container);
                        } else {
                            const chartData = JSON.parse(data[key]);
                            
                            // Configure chart sizing to fill container
                            chartData.layout.autosize = true;
                            chartData.layout.width = null;
                            chartData.layout.height = null;
                            chartData.layout.margin = getChartMargins(`${key}-chart`, {l: 50, r: 50, t: 40, b: 20});
                            
                            // Ensure axes auto-scale with new data
                            if (chartData.layout.xaxis) {
                                chartData.layout.xaxis.autorange = true;
                            }
                            if (chartData.layout.yaxis) {
                                chartData.layout.yaxis.autorange = true;
                            }
                            
                            chartData.layout.plot_bgcolor = '#1E1E1E';
                            chartData.layout.paper_bgcolor = '#1E1E1E';
                            
                            const config = {
                                responsive: true,
                                displayModeBar: true,
                                modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                                displaylogo: false,
                                useResizeHandler: true,
                                style: {width: "100%", height: "100%"}
                            };
                            
                            if (charts[key]) {
                                Plotly.react(`${key}-chart`, chartData.data, chartData.layout, config);
                            } else {
                                charts[key] = Plotly.newPlot(`${key}-chart`, chartData.data, chartData.layout, config);
                            }
                        }
                    } catch (error) {
                        console.error(`Error rendering ${key} chart:`, error);
                    }
                });
            }
            renderStrikeRailPanel();
            
            // Clean up disabled regular charts from charts object
            Object.keys(selectedCharts).forEach(key => {
                if ((!selectedCharts[key] || STRIKE_RAIL_CHART_IDS.includes(key)) && !['price'].includes(key)) {
                    const container = document.getElementById(`${key}-chart`);
                    if (container) {
                        container.remove();
                    }
                    delete charts[key];
                    delete chartContainerCache[key];
                }
            });
            
            // Add fullscreen and popout buttons to all chart containers
            document.querySelectorAll('.chart-container').forEach(c => { addFullscreenButton(c); addPopoutButton(c); });

            // Push updated data to any open popout windows
            pushAllPopouts();

            // If a chart is currently fullscreen, ensure it resizes to fill viewport
            const fsChart = document.querySelector('.chart-container.fullscreen');
            if (fsChart) {
                requestAnimationFrame(() => {
                    const plot = fsChart.querySelector('.js-plotly-plot');
                    if (plot) { try { Plotly.Plots.resize(plot); } catch(e) {} }
                });
            }

            // Restore scroll position after DOM updates
            requestAnimationFrame(() => {
                window.scrollTo(0, savedScrollPosition);
            });
        }
        
        function updatePriceInfo(info) {
            // If EM range lock is active, silently sync the slider without triggering a full re-fetch
            if (emRangeLocked && info && info.expected_move_range) {
                applyEmRange(info.expected_move_range, false);
            }
            if (!info) return;
            renderPriceHeader(info);
            renderRangeScale(info);
        }

        // ── Phase 3 Stage 2 — alerts rail card renderers ─────────────────
        function _setMet(key, text) {
            document.querySelectorAll('[data-met="' + key + '"]').forEach(n => {
                n.textContent = text;
            });
        }

        function renderPriceHeader(info) {
            const p = (livePrice !== null) ? livePrice : info.current_price;
            const priceEl = document.querySelector('#rail-card-price [data-live-price]');
            if (priceEl && typeof p === 'number') {
                priceEl.textContent = '$' + p.toFixed(2);
            }
            const chgEl = document.querySelector('#rail-card-price [data-met="price_change"]');
            if (chgEl) {
                const pct = (typeof info.net_percent === 'number') ? info.net_percent : 0;
                const chg = (typeof info.net_change === 'number') ? info.net_change : 0;
                chgEl.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%  '
                                  + '(' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + ')';
                chgEl.classList.toggle('pos', pct >= 0);
                chgEl.classList.toggle('neg', pct < 0);
            }
            const expiries = lastData.selected_expiries || [];
            const chipText = expiries.length > 1
                ? (expiries.length + ' expiries')
                : (expiries[0] || '—');
            _setMet('expiry_chip', chipText);
        }

        function renderRangeScale(info) {
            const low = info.low, high = info.high;
            const price = (livePrice !== null) ? livePrice : info.current_price;
            if (typeof low !== 'number' || typeof high !== 'number' || high <= low) {
                _setMet('range_low',  '—');
                _setMet('range_high', '—');
                _setMet('em_type', 'ATM straddle');
                _setMet('em_band_label', '—');
                _setMet('em_context', 'Uses the current ATM straddle, not flow alone.');
                return;
            }
            const range = high - low;
            const pct = Math.max(0, Math.min(1, (price - low) / range));
            const marker = document.querySelector('#rail-card-range [data-met="price_marker"]');
            if (marker) marker.style.left = (pct * 100).toFixed(2) + '%';
            _setMet('range_low',  'Day low $' + low.toFixed(2));
            _setMet('range_high', 'Day high $' + high.toFixed(2));
            const band = document.querySelector('#rail-card-range [data-met="em_band"]');
            _setMet('em_type', 'ATM straddle');
            if (info.expected_move_range && typeof info.expected_move_range.lower === 'number'
                                         && typeof info.expected_move_range.upper === 'number') {
                const emLo = info.expected_move_range.lower;
                const emHi = info.expected_move_range.upper;
                const a = Math.max(0, Math.min(1, (emLo - low) / range));
                const b = Math.max(0, Math.min(1, (emHi - low) / range));
                if (band) {
                    band.style.left  = (a * 100).toFixed(2) + '%';
                    band.style.width = ((b - a) * 100).toFixed(2) + '%';
                    band.style.display = '';
                }
                _setMet('em_band_label', '$' + emLo.toFixed(2) + ' to $' + emHi.toFixed(2));
                const upperPct = info.expected_move_range.upper_pct;
                _setMet('em_pct', (upperPct != null) ? ('±' + Math.abs(upperPct).toFixed(2) + '%') : '');
                let context = 'Spot is inside the implied band.';
                if (typeof price === 'number' && price > emHi) context = 'Spot is above the implied upper band.';
                else if (typeof price === 'number' && price < emLo) context = 'Spot is below the implied lower band.';
                _setMet('em_context', context + ' Based on the current ATM straddle, not a flow-only forecast.');
            } else {
                if (band) band.style.display = 'none';
                _setMet('em_band_label', 'Implied move unavailable');
                _setMet('em_pct', '');
                _setMet('em_context', 'Uses the current ATM straddle when bid/ask data is available.');
            }
        }

        function renderMarketMetrics(stats) {
            if (!stats) {
                _setMet('net_gex', '—');
                _setMet('net_dex', '—');
                _setMet('net_gex_delta', '');
                _setMet('net_dex_delta', '');
                document.querySelectorAll('#rail-card-metrics .v').forEach(el => el.classList.remove('pos', 'neg'));
                return;
            }
            _setMet('net_gex', fmtMoneyCompact(stats.net_gex));
            _setMet('net_dex', fmtMoneyCompact(stats.net_dex));
            const gexValueEl = document.querySelector('#rail-card-metrics [data-met="net_gex"]');
            if (gexValueEl) {
                gexValueEl.classList.toggle('pos', typeof stats.net_gex === 'number' && stats.net_gex > 0);
                gexValueEl.classList.toggle('neg', typeof stats.net_gex === 'number' && stats.net_gex < 0);
            }
            const dexValueEl = document.querySelector('#rail-card-metrics [data-met="net_dex"]');
            if (dexValueEl) {
                dexValueEl.classList.toggle('pos', typeof stats.net_dex === 'number' && stats.net_dex > 0);
                dexValueEl.classList.toggle('neg', typeof stats.net_dex === 'number' && stats.net_dex < 0);
            }
            const sd = stats.session_deltas || {};
            const dGex = (typeof sd.net_gex_vs_open === 'number') ? sd.net_gex_vs_open : null;
            const dDex = (typeof sd.net_dex_vs_open === 'number') ? sd.net_dex_vs_open : null;
            _setMet('net_gex_delta', dGex == null ? '' : ('Δ ' + (dGex > 0 ? '+' : '') + fmtMoneyCompact(dGex)));
            _setMet('net_dex_delta', dDex == null ? '' : ('Δ ' + (dDex > 0 ? '+' : '') + fmtMoneyCompact(dDex)));
            const dGexEl = document.querySelector('#rail-card-metrics [data-met="net_gex_delta"]');
            if (dGexEl) {
                dGexEl.classList.toggle('pos', dGex != null && dGex > 0);
                dGexEl.classList.toggle('neg', dGex != null && dGex < 0);
            }
            const dDexEl = document.querySelector('#rail-card-metrics [data-met="net_dex_delta"]');
            if (dDexEl) {
                dDexEl.classList.toggle('pos', dDex != null && dDex > 0);
                dDexEl.classList.toggle('neg', dDex != null && dDex < 0);
            }
        }

        function renderGammaProfile(stats) {
            if (!stats || !stats.profile) {
                _setMet('profile_headline', '—');
                _setMet('profile_blurb', '');
                return;
            }
            const dot = document.querySelector('#rail-card-profile [data-met="profile_dot"]');
            if (dot) {
                const pos = stats.profile.regime === 'Long Gamma';
                dot.classList.toggle('pos', pos);
                dot.classList.toggle('neg', !pos);
            }
            _setMet('profile_headline', stats.profile.headline || '—');
            _setMet('profile_blurb',    stats.profile.blurb    || '');
        }

        function renderIVContext(stats) {
            const setTone = (key, value) => {
                document.querySelectorAll('[data-met="' + key + '"]').forEach(el => {
                    el.classList.remove('pos', 'neg');
                    if (value == null || !isFinite(value) || Math.abs(value) < 0.0001) return;
                    el.classList.add(value > 0 ? 'pos' : 'neg');
                });
            };
            const fmtPct = value => (value == null || !isFinite(value)) ? '—' : (value * 100).toFixed(1) + '%';
            const fmtPts = value => (value == null || !isFinite(value)) ? '—' : ((value > 0 ? '+' : '') + (value * 100).toFixed(1) + ' pts');
            const iv = stats && stats.iv_context;
            if (!iv) {
                _setMet('iv_expiry', 'Near expiry');
                _setMet('iv_atm', '—');
                _setMet('iv_headline', 'IV context unavailable');
                _setMet('iv_blurb', 'Need implied volatility on the near expiry to build a skew read.');
                _setMet('iv_atm_call', '—');
                _setMet('iv_atm_put', '—');
                _setMet('iv_put_wing', '—');
                _setMet('iv_call_wing', '—');
                _setMet('iv_skew_spread', '—');
                _setMet('iv_skew_change', '—');
                setTone('iv_skew_spread', null);
                setTone('iv_skew_change', null);
                return;
            }
            _setMet('iv_expiry', iv.expiry_text || 'Near expiry');
            _setMet('iv_atm', fmtPct(iv.atm_iv));
            _setMet('iv_headline', iv.headline || 'IV context unavailable');
            _setMet('iv_blurb', iv.blurb || 'Need implied volatility on the near expiry to build a skew read.');
            _setMet('iv_atm_call', fmtPct(iv.atm_call_iv));
            _setMet('iv_atm_put', fmtPct(iv.atm_put_iv));
            _setMet('iv_put_wing', fmtPct(iv.put_wing_iv));
            _setMet('iv_call_wing', fmtPct(iv.call_wing_iv));
            _setMet('iv_skew_spread', fmtPts(iv.skew_spread));
            _setMet('iv_skew_change', fmtPts(iv.skew_change));
            setTone('iv_skew_spread', iv.skew_spread);
            setTone('iv_skew_change', iv.skew_change);
        }

        function renderChainActivity(stats) {
            const biasEl = document.querySelector('#rail-card-activity [data-met="activity_bias"]');
            const oiFill  = document.querySelector('#rail-card-activity [data-met="oi_fill"]');
            const volFill = document.querySelector('#rail-card-activity [data-met="vol_fill"]');
            const setSplit = (key, share, callValue, putValue) => {
                if (share == null || !isFinite(share)) {
                    _setMet(key, '—');
                    return;
                }
                const callPct = Math.round(share * 100);
                const putPct = Math.max(0, 100 - callPct);
                _setMet(key, `C ${callPct}% · ${fmtCountCompact(callValue)} | P ${putPct}% · ${fmtCountCompact(putValue)}`);
            };
            if (!stats || !stats.chain_activity) {
                _setMet('activity_bias', '—');
                _setMet('oi_cp',  '—');
                _setMet('vol_cp', '—');
                _setMet('oi_split', '—');
                _setMet('vol_split', '—');
                if (biasEl) biasEl.classList.remove('pos', 'neg');
                if (oiFill) {
                    oiFill.style.width = '0%';
                    oiFill.classList.remove('pos', 'neg');
                }
                if (volFill) {
                    volFill.style.width = '0%';
                    volFill.classList.remove('pos', 'neg');
                }
                return;
            }
            const ca = stats.chain_activity;
            const sentiment = (typeof ca.sentiment === 'number') ? ca.sentiment : 0;
            const pct = Math.max(0, Math.min(1, (sentiment + 1) / 2));
            const marker = document.querySelector('#rail-card-activity [data-met="sentiment_marker"]');
            if (marker) marker.style.left = (pct * 100).toFixed(2) + '%';
            if (biasEl) {
                let biasText = 'Balanced';
                biasEl.classList.remove('pos', 'neg');
                if (sentiment >= 0.2) {
                    biasText = 'Calls in control';
                    biasEl.classList.add('pos');
                } else if (sentiment <= -0.2) {
                    biasText = 'Puts in control';
                    biasEl.classList.add('neg');
                } else {
                    biasText = 'Two-way flow';
                }
                biasEl.textContent = biasText;
            }
            _setMet('oi_cp',  ca.oi_cp_ratio  == null ? '—' : ('C/P ' + ca.oi_cp_ratio.toFixed(2)));
            _setMet('vol_cp', ca.vol_cp_ratio == null ? '—' : ('C/P ' + ca.vol_cp_ratio.toFixed(2)));
            setSplit('oi_split', ca.oi_call_share, ca.call_oi, ca.put_oi);
            setSplit('vol_split', ca.vol_call_share, ca.call_vol, ca.put_vol);
            const fillPct = share => (share == null || !isFinite(share)) ? 0
                : Math.max(0, Math.min(100, share * 100));
            if (oiFill) {
                oiFill.style.width  = fillPct(ca.oi_call_share)  + '%';
                oiFill.classList.toggle('pos', ca.oi_call_share != null && ca.oi_call_share >= 0.5);
                oiFill.classList.toggle('neg', ca.oi_call_share != null && ca.oi_call_share < 0.5);
            }
            if (volFill) {
                volFill.style.width = fillPct(ca.vol_call_share) + '%';
                volFill.classList.toggle('pos', ca.vol_call_share != null && ca.vol_call_share >= 0.5);
                volFill.classList.toggle('neg', ca.vol_call_share != null && ca.vol_call_share < 0.5);
            }
        }

        function renderFlowPulse(stats) {
            const target = document.getElementById('rail-flow-pulse');
            const note = document.getElementById('rail-flow-pulse-note');
            if (!target) return;
            const rows = Array.isArray(stats && stats.flow_pulse) ? stats.flow_pulse : [];
            const summary = (stats && typeof stats.flow_pulse_summary === 'object') ? stats.flow_pulse_summary : null;
            if (!rows.length) {
                if (note) note.textContent = 'Mixed Lean';
                target.innerHTML = '<div class="rail-pulse-empty">Pulse data builds after a minute of live flow history.</div>';
                return;
            }
            if (note) note.textContent = _flowPulseSummaryText(summary);
            target.innerHTML = rows.slice(0, 4).map(row => {
                const typeKey = row.option_type === 'put' ? 'put' : 'call';
                const lean = _flowPulseLeanMeta(row.lean_label);
                const pace = (row.pace_1m != null && isFinite(row.pace_1m)) ? row.pace_1m.toFixed(1) + 'x' : '—';
                const vol1m = (row.vol_delta_1m != null && isFinite(row.vol_delta_1m) && row.vol_delta_1m > 0)
                    ? '+' + fmtCountCompact(row.vol_delta_1m)
                    : '—';
                const prem1m = (row.premium_delta_1m != null && isFinite(row.premium_delta_1m) && row.premium_delta_1m > 0)
                    ? '+' + fmtMoneyCompact(row.premium_delta_1m)
                    : '—';
                const voi = (row.voi != null && isFinite(row.voi)) ? row.voi.toFixed(2) + 'x V/OI' : '—';
                return '<div class="rail-pulse-item ' + typeKey + '">' +
                           '<div class="rail-pulse-top">' +
                               '<div class="rail-pulse-contract">' + _escapeHtml(row.contract_label || '—') +
                                   '<span class="rail-pulse-expiry">' + _escapeHtml(row.expiry_text || '') + '</span>' +
                               '</div>' +
                               '<div class="rail-pulse-right">' +
                                   '<span class="rail-pulse-lean ' + lean.cls + '" title="' + _escapeHtml(row.lean_hint || '') + '">' + lean.text + '</span>' +
                                   '<div class="rail-pulse-pace">' + pace + '</div>' +
                               '</div>' +
                           '</div>' +
                           '<div class="rail-pulse-meta">' +
                               '<span class="emph">1m ' + vol1m + ' vol</span>' +
                               '<span>' + prem1m + '</span>' +
                               '<span>' + _escapeHtml(voi) + '</span>' +
                           '</div>' +
                       '</div>';
            }).join('');
        }

        function renderCentroidPanel(panel) {
            const target = document.querySelector('#rail-card-centroid [data-centroid-sparkline]');
            const callStrikeEl = document.querySelector('#rail-card-centroid [data-met="centroid_call_strike"]');
            const putStrikeEl = document.querySelector('#rail-card-centroid [data-met="centroid_put_strike"]');
            const callDeltaEl = document.querySelector('#rail-card-centroid [data-met="centroid_call_delta"]');
            const putDeltaEl = document.querySelector('#rail-card-centroid [data-met="centroid_put_delta"]');
            const callDriftEl = document.querySelector('#rail-card-centroid [data-met="centroid_call_drift"]');
            const putDriftEl = document.querySelector('#rail-card-centroid [data-met="centroid_put_drift"]');
            const structureEl = document.querySelector('#rail-card-centroid [data-met="centroid_structure"]');
            const driftReadEl = document.querySelector('#rail-card-centroid [data-met="centroid_drift_read"]');
            const setTone = (el, value, flat = 0.005) => {
                if (!el) return;
                el.classList.remove('pos', 'neg');
                if (value != null && isFinite(value) && Math.abs(value) >= flat) {
                    el.classList.add(value > 0 ? 'pos' : 'neg');
                }
            };
            const setDelta = (el, value, suffix) => {
                if (!el) return;
                el.textContent = (fmtSignedPriceDelta(value) === '—')
                    ? '—'
                    : (fmtSignedPriceDelta(value) + (suffix ? (' ' + suffix) : ''));
                setTone(el, value);
            };
            const setDrift = (el, label, value, fromTime) => {
                if (!el) return;
                el.textContent = (value == null || !isFinite(value))
                    ? `${label} drift —`
                    : `${label} drift ${fmtSignedPriceDelta(value)} since ${fromTime || 'open'}`;
                setTone(el, value, 0.05);
            };
            const describeStructure = (callVs, putVs) => {
                const far = 0.35;
                const near = 0.2;
                if (callVs == null || putVs == null) return 'The centroid tracks where call and put volume is clustering by strike.';
                if (callVs > near && putVs < -near) {
                    return 'Call volume is centered above spot while put volume sits below it, so flow is bracketing current price.';
                }
                if (callVs > far && putVs > 0) {
                    return 'Both centroids sit above spot, so traded volume is clustering in higher strikes.';
                }
                if (callVs < 0 && putVs < -far) {
                    return 'Both centroids sit below spot, so volume concentration is skewed to lower strikes.';
                }
                if (Math.abs(callVs) <= near && Math.abs(putVs) <= near) {
                    return 'Both centroids are hugging spot, so the highest traded strikes are concentrated near current price.';
                }
                if (callVs > near) {
                    return 'Calls are concentrated above spot, while put volume is staying closer to current price.';
                }
                if (putVs < -near) {
                    return 'Puts are concentrated below spot, while call volume is staying closer to current price.';
                }
                return 'The centroid is showing where call and put volume is leaning relative to spot right now.';
            };
            const describeDrift = (callDrift, putDrift, spreadDrift, fromTime) => {
                const move = 0.25;
                const anchor = fromTime || 'the first print';
                if (callDrift == null && putDrift == null) {
                    return 'Drift compares the current centroid strikes against the first centroid print of the session.';
                }
                const callActive = callDrift != null && Math.abs(callDrift) >= move;
                const putActive = putDrift != null && Math.abs(putDrift) >= move;
                if (!callActive && !putActive) {
                    return `Centroid drift is quiet since ${anchor}, so the volume center of mass has stayed fairly stable.`;
                }
                if (callActive && putActive && callDrift > 0 && putDrift > 0) {
                    return `Both centroids are drifting higher since ${anchor}, lifting the strike focus of traded volume.`;
                }
                if (callActive && putActive && callDrift < 0 && putDrift < 0) {
                    return `Both centroids are drifting lower since ${anchor}, pulling the strike focus down.`;
                }
                if (spreadDrift != null && spreadDrift >= move) {
                    return `Call and put centroids are separating since ${anchor}, widening the strike distribution.`;
                }
                if (spreadDrift != null && spreadDrift <= -move) {
                    return `Call and put centroids are converging since ${anchor}, compressing the strike distribution.`;
                }
                if (callActive && !putActive) {
                    return `The call centroid is doing most of the moving since ${anchor}, while puts remain relatively anchored.`;
                }
                if (!callActive && putActive) {
                    return `The put centroid is doing most of the moving since ${anchor}, while calls remain relatively anchored.`;
                }
                return `Drift shows the strike center shifting since ${anchor}, but without a strong one-sided migration yet.`;
            };
            if (!target) return;
            if (!panel || !Array.isArray(panel.points) || !panel.points.length) {
                _setMet('centroid_status', 'Waiting');
                _setMet('centroid_time', '—');
                _setMet('centroid_spread', '—');
                _setMet('centroid_call_strike', '—');
                _setMet('centroid_put_strike', '—');
                setDelta(callDeltaEl, null, '');
                setDelta(putDeltaEl, null, '');
                setDrift(callDriftEl, 'Call', null, '');
                setDrift(putDriftEl, 'Put', null, '');
                _setMet('centroid_structure', 'The centroid tracks where call and put volume is clustering by strike.');
                _setMet('centroid_drift_read', 'Drift compares the current centroid strikes against the first centroid print of the session.');
                target.innerHTML = '<div class="rail-centroid-empty">' + _escapeHtml((panel && panel.status) || 'Centroid data loads with stream data.') + '</div>';
                return;
            }

            _setMet('centroid_status', panel.showing_last_session ? 'Last session' : 'Current session');
            _setMet('centroid_time', panel.latest_time ? ('Updated ' + panel.latest_time + ' ET') : '—');
            _setMet('centroid_spread', panel.spread != null && isFinite(panel.spread)
                ? ('Spread ' + fmtSignedPriceDelta(panel.spread))
                : 'Spread —');
            _setMet('centroid_call_strike', fmtPrice(panel.latest_call));
            _setMet('centroid_put_strike', fmtPrice(panel.latest_put));
            setDelta(callDeltaEl, panel.call_vs_price, 'vs spot');
            setDelta(putDeltaEl, panel.put_vs_price, 'vs spot');
            setDrift(callDriftEl, 'Call', panel.call_drift, panel.first_time);
            setDrift(putDriftEl, 'Put', panel.put_drift, panel.first_time);
            _setMet('centroid_structure', describeStructure(panel.call_vs_price, panel.put_vs_price));
            _setMet('centroid_drift_read', describeDrift(panel.call_drift, panel.put_drift, panel.spread_drift, panel.first_time));

            const points = panel.points;
            const values = points.flatMap(p => [p.call, p.price, p.put]).filter(v => v != null && isFinite(v));
            if (values.length < 2) {
                target.innerHTML = '<div class="rail-centroid-empty">Need more centroid history for a trend line.</div>';
                return;
            }

            const width = 320;
            const height = 68;
            const pad = 6;
            const min = Math.min(...values);
            const max = Math.max(...values);
            const range = Math.max(max - min, 0.5);
            const seriesPath = key => {
                let path = '';
                let hasSegment = false;
                points.forEach((point, idx) => {
                    const val = point[key];
                    if (val == null || !isFinite(val)) {
                        hasSegment = false;
                        return;
                    }
                    const x = pad + ((width - pad * 2) * idx / Math.max(points.length - 1, 1));
                    const y = height - pad - (((val - min) / range) * (height - pad * 2));
                    path += (hasSegment ? ' L ' : ' M ') + x.toFixed(2) + ' ' + y.toFixed(2);
                    hasSegment = true;
                });
                return path.trim();
            };
            const midY = (height / 2).toFixed(2);
            target.innerHTML =
                '<svg viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none" aria-hidden="true">' +
                    '<line x1="0" y1="' + midY + '" x2="' + width + '" y2="' + midY + '" stroke="rgba(156,163,175,0.16)" stroke-width="1" />' +
                    '<path d="' + seriesPath('call') + '" fill="none" stroke="var(--call)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />' +
                    '<path d="' + seriesPath('price') + '" fill="none" stroke="#D4AF37" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" />' +
                    '<path d="' + seriesPath('put') + '" fill="none" stroke="var(--put)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />' +
                '</svg>';
        }

        function loadExpirations() {
            const ticker = document.getElementById('ticker').value;
            fetch(`/expirations/${ticker}`)
                .then(response => {
                    if (!response.ok) throw new Error('Failed to fetch expirations');
                    return response.json();
                })
                .then(data => {
                    if (data.error) {
                        showError(data.error);
                        return;
                    }
                    const optionsContainer = document.getElementById('expiry-options');
                    const previousSelections = Array.from(document.querySelectorAll('.expiry-option input[type="checkbox"]:checked')).map(cb => cb.value);
                    
                    // Clear existing options but keep the buttons
                    const buttons = optionsContainer.querySelector('.expiry-buttons');
                    optionsContainer.innerHTML = '';
                    
                    data.forEach(date => {
                        const optionDiv = document.createElement('div');
                        optionDiv.className = 'expiry-option';
                        
                        const checkbox = document.createElement('input');
                        checkbox.type = 'checkbox';
                        checkbox.value = date;
                        checkbox.id = 'expiry-' + date;
                        
                        const label = document.createElement('label');
                        label.htmlFor = 'expiry-' + date;
                        label.textContent = date;
                        label.style.cursor = 'pointer';
                        label.style.flex = '1';
                        
                        // Restore previous selections if they still exist
                        if (previousSelections.includes(date)) {
                            checkbox.checked = true;
                        }
                        
                        // Add change event listener
                        checkbox.addEventListener('change', function() {
                            updateExpiryDisplay();
                            updateData();
                        });
                        
                        optionDiv.appendChild(checkbox);
                        optionDiv.appendChild(label);
                        optionsContainer.appendChild(optionDiv);
                    });
                    
                    // Re-add the buttons at the top
                    optionsContainer.insertBefore(buttons, optionsContainer.firstChild);
                    
                    // If no previous selections or none match, select the first option
                    const checkedBoxes = document.querySelectorAll('.expiry-option input[type="checkbox"]:checked');
                    if (checkedBoxes.length === 0 && data.length > 0) {
                        const firstCheckbox = document.querySelector('.expiry-option input[type="checkbox"]');
                        if (firstCheckbox) {
                            firstCheckbox.checked = true;
                        }
                    }
                    
                    updateExpiryDisplay();
                    updateData();
                })
                .catch(error => {
                    showError('Error loading expirations: ' + error.message);
                });
        }
        
        function updateExpiryDisplay() {
            const checkedBoxes = document.querySelectorAll('.expiry-option input[type="checkbox"]:checked');
            const expiryText = document.getElementById('expiry-text');
            
            if (checkedBoxes.length === 0) {
                expiryText.textContent = 'Select expiry dates...';
            } else if (checkedBoxes.length === 1) {
                expiryText.textContent = checkedBoxes[0].value;
            } else {
                expiryText.textContent = `${checkedBoxes.length} expiries selected`;
            }
        }
        
        // Add event listeners for control checkboxes
        document.querySelectorAll('.control-group input[type="checkbox"]').forEach(checkbox => {
            checkbox.addEventListener('change', updateData);
        });
        
        document.getElementById('ticker').addEventListener('change', loadExpirations);
        
        // Add event listeners for dropdown toggle
        document.getElementById('expiry-display').addEventListener('click', function(e) {
            e.stopPropagation();
            const options = document.getElementById('expiry-options');
            options.classList.toggle('open');
        });
        
        // Close dropdown when clicking outside
        document.addEventListener('click', function(e) {
            const dropdown = document.querySelector('.expiry-dropdown');
            const options = document.getElementById('expiry-options');
            if (dropdown && !dropdown.contains(e.target)) {
                options.classList.remove('open');
            }
            
            const levelsDropdown = document.querySelector('.levels-dropdown');
            const levelsOptions = document.getElementById('levels-options');
            if (levelsDropdown && !levelsDropdown.contains(e.target)) {
                levelsOptions.classList.remove('open');
            }
        });
        
        // Add event listeners for expiry selection buttons
        document.getElementById('selectAllExpiry').addEventListener('click', function(e) {
            e.stopPropagation();
            const checkboxes = document.querySelectorAll('.expiry-option input[type="checkbox"]');
            checkboxes.forEach(checkbox => {
                checkbox.checked = true;
            });
            updateExpiryDisplay();
            updateData();
        });
        
        document.getElementById('clearAllExpiry').addEventListener('click', function(e) {
            e.stopPropagation();
            const checkboxes = document.querySelectorAll('.expiry-option input[type="checkbox"]');
            checkboxes.forEach(checkbox => {
                checkbox.checked = false;
            });
            // Select the first option to ensure at least one is selected
            if (checkboxes.length > 0) {
                checkboxes[0].checked = true;
            }
            updateExpiryDisplay();
            updateData();
        });

        function selectExpiriesUpTo(cutoffDate) {
            const checkboxes = document.querySelectorAll('.expiry-option input[type="checkbox"]');
            let anyChecked = false;
            checkboxes.forEach(checkbox => {
                // Parse as local date to avoid UTC offset issues
                const parts = checkbox.value.split('-');
                const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
                checkbox.checked = d <= cutoffDate;
                if (checkbox.checked) anyChecked = true;
            });
            if (!anyChecked && checkboxes.length > 0) {
                checkboxes[0].checked = true;
            }
            updateExpiryDisplay();
            updateData();
        }

        function getFriday(weeksAhead) {
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            const dow = today.getDay(); // 0=Sun,1=Mon,...,5=Fri,6=Sat
            const daysToFriday = (5 - dow + 7) % 7;
            const cutoff = new Date(today);
            cutoff.setDate(today.getDate() + daysToFriday + weeksAhead * 7);
            return cutoff;
        }

        document.getElementById('expiryToday').addEventListener('click', function(e) {
            e.stopPropagation();
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            selectExpiriesUpTo(today);
        });

        document.getElementById('expiryThisWk').addEventListener('click', function(e) {
            e.stopPropagation();
            selectExpiriesUpTo(getFriday(0));
        });

        function selectFirstNExpiries(n) {
            const checkboxes = document.querySelectorAll('.expiry-option input[type="checkbox"]');
            checkboxes.forEach((checkbox, i) => {
                checkbox.checked = i < n;
            });
            if (checkboxes.length > 0 && n === 0) checkboxes[0].checked = true;
            updateExpiryDisplay();
            updateData();
        }

        document.getElementById('expiry2Wks').addEventListener('click', function(e) {
            e.stopPropagation();
            selectFirstNExpiries(7);
        });

        document.getElementById('expiry4Wks').addEventListener('click', function(e) {
            e.stopPropagation();
            selectFirstNExpiries(14);
        });

        document.getElementById('expiry1Mo').addEventListener('click', function(e) {
            e.stopPropagation();
            const cutoff = new Date();
            cutoff.setHours(0, 0, 0, 0);
            cutoff.setDate(cutoff.getDate() + 30);
            selectExpiriesUpTo(cutoff);
        });

        // Initial load - automatically load saved settings, or use defaults
        applyPersistedTimeframePreference();
        loadSettings(false);

        // Auto-update every 1 second
        updateInterval = setInterval(updateData, 1000);
        
        // Handle window resize
        window.addEventListener('resize', () => {
            const grid = document.getElementById('chart-grid');
            if (grid) {
                const liveWidth = parseFloat(getComputedStyle(grid).getPropertyValue('--gex-col-w')) || 352;
                applyGexColWidth(liveWidth, false);
            }
            Object.keys(charts).forEach(chartKey => {
                const chartElement = document.getElementById(`${chartKey}-chart`);
                if (chartElement && charts[chartKey]) {
                    Plotly.Plots.resize(chartElement);
                }
            });
            scheduleTVDrawingOverlayDraw();
            scheduleTVHistoricalOverlayDraw();
            scheduleGexPanelSync();
        });

        // Cleanup on page unload
        window.addEventListener('beforeunload', () => {
            clearInterval(updateInterval);
            disconnectPriceStream();
            Object.values(charts).forEach(chart => {
                Plotly.purge(chart);
            });
        });

        function toggleStreaming() {
            isStreaming = !isStreaming;
            const button = document.getElementById('streamToggle');
            button.textContent = isStreaming ? 'Auto-Update' : 'Paused';
            button.classList.toggle('paused', !isStreaming);
            
            if (isStreaming) {
                updateInterval = setInterval(updateData, 1000);
                // Reconnect real-time price stream when resuming
                const tickerVal = (document.getElementById('ticker').value || '').trim();
                if (tickerVal) connectPriceStream(tickerVal);
            } else {
                clearInterval(updateInterval);
                // Disconnect real-time price stream when pausing
                disconnectPriceStream();
            }
        }
        
        document.getElementById('streamToggle').addEventListener('click', toggleStreaming);
        syncAlertGateCheckbox();
        syncDealerDetailCheckbox();
        applySessionLevelSettingsToDom(DEFAULT_SESSION_LEVEL_SETTINGS);
        wireSessionLevelControls();
        const gateAlertsToggle = document.getElementById('gate_alerts');
        if (gateAlertsToggle) {
            gateAlertsToggle.addEventListener('change', () => {
                setAlertGateSetting(gateAlertsToggle.checked);
                updateData();
            });
        }
        const dealerDetailToggle = document.getElementById('dealer_impact_verbose');
        if (dealerDetailToggle) {
            dealerDetailToggle.addEventListener('change', () => {
                setDealerDetailSetting(dealerDetailToggle.checked);
                renderDealerImpact(_lastStats);
            });
        }

        // Settings save/load functions
        function gatherSettings() {
            return {
                settings_schema_version: 6,
                ticker: document.getElementById('ticker').value,
                timeframe: document.getElementById('timeframe').value,
                strike_range: document.getElementById('strike_range').value,
                exposure_metric: document.getElementById('exposure_metric').value,
                top_oi_count: normalizeTopOICountInput(),
                delta_adjusted_exposures: document.getElementById('delta_adjusted_exposures').checked,
                calculate_in_notional: document.getElementById('calculate_in_notional').checked,
                show_calls: document.getElementById('show_calls').checked,
                show_puts: document.getElementById('show_puts').checked,
                show_net: document.getElementById('show_net').checked,
                ov_show_calls: document.getElementById('ov_show_calls').checked,
                ov_show_puts: document.getElementById('ov_show_puts').checked,
                ov_show_net: document.getElementById('ov_show_net').checked,
                ov_show_totals: document.getElementById('ov_show_totals').checked,
                coloring_mode: document.getElementById('coloring_mode').value,
                levels_types: Array.from(document.querySelectorAll('.levels-option input:checked')).map(cb => cb.value),
                levels_count: document.getElementById('levels_count').value,
                use_heikin_ashi: document.getElementById('use_heikin_ashi').checked,
                horizontal_bars: document.getElementById('horizontal_bars').checked,
                show_abs_gex: document.getElementById('show_abs_gex').checked,
                abs_gex_opacity: document.getElementById('abs_gex_opacity').value,
                use_range: document.getElementById('use_range').checked,
                call_color: document.getElementById('call_color').value,
                put_color: document.getElementById('put_color').value,
                highlight_max_level: document.getElementById('highlight_max_level').checked,
                max_level_color: document.getElementById('max_level_color').value,
                max_level_mode: document.getElementById('max_level_mode').value,
                gate_alerts: !!(document.getElementById('gate_alerts') && document.getElementById('gate_alerts').checked),
                dealer_impact_verbose: !!(document.getElementById('dealer_impact_verbose') && document.getElementById('dealer_impact_verbose').checked),
                em_range_locked: emRangeLocked,
                tv_active_indicators: Array.from(tvActiveInds),
                tv_indicator_prefs: normalizeTVIndicatorPrefMap(tvIndicatorPrefs),
                price_level_prefs: normalizePriceLevelPrefMap(priceLevelPrefs),
                session_levels: getSessionLevelSettingsFromDom(),
                // Chart visibility
                charts: getChartVisibility()
            };
        }

        function applySettings(settings, options = {}) {
            const preferLocalIndicatorState = !!options.preferLocalIndicatorState;
            const settingsSchemaVersion = Number(settings.settings_schema_version || 0);
            if (settings.ticker) document.getElementById('ticker').value = settings.ticker;
            if (settings.timeframe) document.getElementById('timeframe').value = settings.timeframe;
            if (settings.strike_range) {
                document.getElementById('strike_range').value = settings.strike_range;
                document.getElementById('strike_range_value').textContent = settings.strike_range + '%';
            }
            if (settings.exposure_metric) document.getElementById('exposure_metric').value = settings.exposure_metric;
            if (settings.top_oi_count !== undefined) {
                document.getElementById('top_oi_count').value = settings.top_oi_count;
                normalizeTopOICountInput();
            }
            if (settings.delta_adjusted_exposures !== undefined) document.getElementById('delta_adjusted_exposures').checked = settings.delta_adjusted_exposures;
            if (settings.calculate_in_notional !== undefined) document.getElementById('calculate_in_notional').checked = settings.calculate_in_notional;
            if (settings.show_calls !== undefined) document.getElementById('show_calls').checked = settings.show_calls;
            if (settings.show_puts !== undefined) document.getElementById('show_puts').checked = settings.show_puts;
            if (settings.show_net !== undefined) document.getElementById('show_net').checked = settings.show_net;
            if (settings.ov_show_calls !== undefined) document.getElementById('ov_show_calls').checked = settings.ov_show_calls;
            if (settings.ov_show_puts !== undefined) document.getElementById('ov_show_puts').checked = settings.ov_show_puts;
            if (settings.ov_show_net !== undefined) document.getElementById('ov_show_net').checked = settings.ov_show_net;
            if (settings.ov_show_totals !== undefined) document.getElementById('ov_show_totals').checked = settings.ov_show_totals;
            // Handle coloring_mode with migration from old color_intensity setting
            if (settings.coloring_mode) {
                document.getElementById('coloring_mode').value = settings.coloring_mode;
            } else if (settings.color_intensity !== undefined) {
                // Migrate old color_intensity boolean to new coloring_mode
                document.getElementById('coloring_mode').value = settings.color_intensity ? 'Linear Intensity' : 'Solid';
            }
            if (settings.levels_types) {
                document.querySelectorAll('.levels-option input').forEach(cb => cb.checked = false);
                settings.levels_types.forEach(type => {
                    const cb = document.getElementById('lvl-' + type.replace(/\\s+/g, ''));
                    if (cb) cb.checked = true;
                });
                updateLevelsDisplay();
            }
            if (settings.levels_count) document.getElementById('levels_count').value = settings.levels_count;
            if (settings.use_heikin_ashi !== undefined) document.getElementById('use_heikin_ashi').checked = settings.use_heikin_ashi;
            if (settings.horizontal_bars !== undefined) document.getElementById('horizontal_bars').checked = settings.horizontal_bars;
            if (settings.show_abs_gex !== undefined) document.getElementById('show_abs_gex').checked = settings.show_abs_gex;
            if (settings.abs_gex_opacity !== undefined) document.getElementById('abs_gex_opacity').value = settings.abs_gex_opacity;
            if (settings.use_range !== undefined) document.getElementById('use_range').checked = settings.use_range;
            if (settings.call_color) {
                document.getElementById('call_color').value = settings.call_color;
                callColor = settings.call_color;
            }
            if (settings.put_color) {
                document.getElementById('put_color').value = settings.put_color;
                putColor = settings.put_color;
            }
            if (settings.highlight_max_level !== undefined) {
                document.getElementById('highlight_max_level').checked = settings.highlight_max_level;
            }
            if (settings.max_level_color) {
                document.getElementById('max_level_color').value = settings.max_level_color;
                maxLevelColor = settings.max_level_color;
            }
            if (settings.max_level_mode) {
                document.getElementById('max_level_mode').value = settings.max_level_mode;
            }
            if (settings.gate_alerts !== undefined) {
                setAlertGateSetting(settings.gate_alerts);
            }
            if (settings.dealer_impact_verbose !== undefined) {
                setDealerDetailSetting(settings.dealer_impact_verbose);
            } else if (settingsSchemaVersion < 3) {
                setDealerDetailSetting(false);
            }
            if (settings.em_range_locked !== undefined) {
                setEmRangeLocked(settings.em_range_locked);
            }
            applySessionLevelSettingsToDom(settings.session_levels || DEFAULT_SESSION_LEVEL_SETTINGS);
            let nextTvActiveInds;
            if (Array.isArray(settings.tv_active_indicators)) {
                nextTvActiveInds = new Set(normalizeTVIndicatorActiveKeys(settings.tv_active_indicators));
            } else if (settingsSchemaVersion < 4) {
                nextTvActiveInds = new Set();
            } else {
                nextTvActiveInds = new Set();
            }
            let nextTvIndicatorPrefs = normalizeTVIndicatorPrefMap(settings.tv_indicator_prefs);
            if (preferLocalIndicatorState) {
                const persistedTvIndicatorState = readPersistedTVIndicatorState();
                if (persistedTvIndicatorState) {
                    nextTvActiveInds = new Set(persistedTvIndicatorState.active);
                    nextTvIndicatorPrefs = persistedTvIndicatorState.prefs;
                }
            }
            tvActiveInds = nextTvActiveInds;
            tvIndicatorPrefs = nextTvIndicatorPrefs;
            persistTVIndicatorState();
            syncTVIndicatorToggleButtons();
            renderTVIndicatorEditor();
            reapplyTVIndicators();
            let nextPriceLevelPrefs = normalizePriceLevelPrefMap(settings.price_level_prefs);
            if (preferLocalIndicatorState) {
                const persistedPriceLevelPrefs = readPersistedPriceLevelPrefs();
                if (persistedPriceLevelPrefs) nextPriceLevelPrefs = persistedPriceLevelPrefs;
            }
            priceLevelPrefs = nextPriceLevelPrefs;
            persistPriceLevelPrefs();
            renderPriceLevelEditor();
            renderKeyLevels(getScopedKeyLevels());
            renderSessionLevels(_lastSessionLevels, getSessionLevelSettingsFromDom());
            // Chart visibility — persist into localStorage; updateCharts() reads from there
            if (settings.charts) {
                const chartSettings = Object.assign({}, settings.charts);
                if (settingsSchemaVersion < 2 && chartSettings.open_interest === false) {
                    chartSettings.open_interest = true;
                }
                setAllChartVisibility(chartSettings);
                if (typeof renderChartVisibilitySection === 'function') renderChartVisibilitySection();
            }
        }
        
        function saveSettings() {
            const settings = gatherSettings();
            fetch('/save_settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const btn = document.getElementById('saveSettings');
                    btn.classList.add('success');
                    btn.textContent = '✓ Saved';
                    setTimeout(() => {
                        btn.classList.remove('success');
                        btn.textContent = '💾 Save';
                    }, 2000);
                } else {
                    showError('Error saving settings: ' + data.error);
                }
            })
            .catch(error => showError('Error saving settings: ' + error));
        }
        
        function loadSettings(showFeedback = true) {
            fetch('/load_settings')
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    if (showFeedback) {
                        showError('Error loading settings: ' + data.error);
                    }
                    // If auto-loading fails, fall back to default initialization
                    if (!showFeedback) {
                        applyPersistedTimeframePreference();
                        loadExpirations();
                    }
                } else {
                    applySettings(data, { preferLocalIndicatorState: !showFeedback });
                    if (!showFeedback) {
                        applyPersistedTimeframePreference();
                    } else {
                        persistSelectedTimeframe(document.getElementById('timeframe').value);
                    }
                    if (showFeedback) {
                        const btn = document.getElementById('loadSettings');
                        btn.classList.add('success');
                        btn.textContent = '✓ Loaded';
                        setTimeout(() => {
                            btn.classList.remove('success');
                            btn.textContent = '📂 Load';
                        }, 2000);
                    }
                    // Reload expirations for the new ticker and update
                    loadExpirations();
                }
            })
            .catch(error => {
                if (showFeedback) {
                    showError('Error loading settings: ' + error);
                }
                // If auto-loading fails, fall back to default initialization
                if (!showFeedback) {
                    applyPersistedTimeframePreference();
                    loadExpirations();
                }
            });
        }
        
        document.getElementById('saveSettings').addEventListener('click', saveSettings);
        document.getElementById('loadSettings').addEventListener('click', loadSettings);

        // ── Settings drawer + color modal ─────────────────────────────────────
        // Display labels for each chart id (covers `price` which secondaryTabLabels omits).
        const CHART_LABELS = {
            price: 'Price Chart',
            gamma: 'Gamma', delta: 'Delta', vanna: 'Vanna', charm: 'Charm',
            speed: 'Speed', vomma: 'Vomma', color: 'Color',
            options_volume: 'Options Vol', open_interest: 'Open Interest',
            large_trades: 'Flow Blotter', premium: 'Premium',
            hvl: 'HVL line', em_2s: '±2σ EM lines', walls_2: 'Secondary walls',
            live_gex_extrema: 'Live max ±GEX lines',
            historical_dots: 'Historical dots'
        };
        function renderChartVisibilitySection() {
            const list = document.getElementById('chart-visibility-list');
            if (!list) return;
            const vis = getChartVisibility();
            const mkToggle = id => `
                <label class="visibility-toggle">
                    <input type="checkbox" data-chart-id="${id}" ${vis[id] ? 'checked' : ''}>
                    <span>${CHART_LABELS[id] || id}</span>
                </label>`;
            list.innerHTML =
                CHART_IDS.map(mkToggle).join('') +
                `<div class="visibility-group-sep">Chart overlays</div>` +
                LINE_OVERLAY_IDS.map(mkToggle).join('');
            list.querySelectorAll('input[data-chart-id]').forEach(cb => {
                cb.addEventListener('change', () => {
                    const id = cb.dataset.chartId;
                    setAllChartVisibility({ [id]: cb.checked });
                    if (LINE_OVERLAY_IDS.includes(id)) {
                        // Price-chart overlays redraw from cache — no refetch needed.
                        renderKeyLevels(getScopedKeyLevels());
                        scheduleTVHistoricalOverlayDraw();
                    } else {
                        updateData();
                    }
                });
            });
        }
        renderChartVisibilitySection();
        tvIndicatorPrefs = normalizeTVIndicatorPrefMap(tvIndicatorPrefs);
        priceLevelPrefs = normalizePriceLevelPrefMap(priceLevelPrefs);
        hydrateTVIndicatorStateFromLocalStorage();
        hydratePriceLevelPrefsFromLocalStorage();
        renderTVIndicatorEditor();
        renderPriceLevelEditor();

        wireRightRailTabs();
        applyRightRailTab();
        ensureFlowEventLane();

        function openDrawer() {
            document.getElementById('settings-drawer').classList.add('open');
            document.getElementById('settings-drawer').setAttribute('aria-hidden', 'false');
            document.getElementById('drawer-backdrop').classList.add('open');
        }
        function closeDrawer() {
            document.getElementById('settings-drawer').classList.remove('open');
            document.getElementById('settings-drawer').setAttribute('aria-hidden', 'true');
            document.getElementById('drawer-backdrop').classList.remove('open');
        }
        function wireDrawerToggle(button = document.getElementById('drawerToggle')) {
            if (!button || button.__drawerWired) return;
            button.__drawerWired = true;
            button.addEventListener('click', openDrawer);
        }
        wireDrawerToggle();
        document.getElementById('drawerClose').addEventListener('click', closeDrawer);
        document.getElementById('drawer-backdrop').addEventListener('click', closeDrawer);

        const settingsModal = document.getElementById('settings-modal');
        const indicatorSettingsModal = document.getElementById('indicator-settings-modal');
        const priceLevelSettingsModal = document.getElementById('price-level-settings-modal');
        document.getElementById('settingsToggle').addEventListener('click', () => {
            if (settingsModal.showModal) { settingsModal.showModal(); }
            else { settingsModal.setAttribute('open', ''); } // <dialog> fallback
        });
        document.getElementById('modalClose').addEventListener('click', () => settingsModal.close());
        document.getElementById('indicatorModalClose').addEventListener('click', () => {
            tvIndicatorEditorTargetKey = '';
            if (indicatorSettingsModal.close) indicatorSettingsModal.close();
            else indicatorSettingsModal.removeAttribute('open');
        });
        document.getElementById('priceLevelModalClose').addEventListener('click', () => {
            if (priceLevelSettingsModal.close) priceLevelSettingsModal.close();
            else priceLevelSettingsModal.removeAttribute('open');
        });

        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (settingsModal.open) { settingsModal.close(); return; }
            if (indicatorSettingsModal.open) {
                tvIndicatorEditorTargetKey = '';
                if (indicatorSettingsModal.close) indicatorSettingsModal.close();
                else indicatorSettingsModal.removeAttribute('open');
                return;
            }
            if (priceLevelSettingsModal.open) {
                if (priceLevelSettingsModal.close) priceLevelSettingsModal.close();
                else priceLevelSettingsModal.removeAttribute('open');
                return;
            }
            if (document.getElementById('settings-drawer').classList.contains('open')) closeDrawer();
        });

        // Add event listener for ticker input
        document.getElementById('ticker').addEventListener('input', function(e) {
            // Stop auto-update when user starts typing
            if (isStreaming) {
                toggleStreaming();
            }
        });

        // Add event listener for ticker enter key
        document.getElementById('ticker').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                // Start auto-update when user hits enter
                if (!isStreaming) {
                    toggleStreaming();
                }
                // Also update the data
                updateData();
            }
        });

    // ── Token Monitor ──────────────────────────────────────────────────────
    function fetchTokenHealth() {
        fetch('/token_health')
            .then(r => r.json())
            .then(d => {
                const dot   = document.getElementById('tm-dot');
                const stats = document.getElementById('tm-stats');

                if (!d.db_exists || d.error) {
                    dot.className = 'tm-dot tm-err';
                    stats.textContent = d.error || 'DB missing';
                    stats.title = d.db_path || '';
                    return;
                }

                // Determine overall health
                const apiOk = d.api_ok === true;
                const atOk  = d.access_token_valid === true;
                const rtOk  = d.refresh_token_valid === true;
                const rtWarn = d.refresh_token_age_days !== null && d.refresh_token_age_days > 5;

                if (!atOk || !rtOk || !apiOk) {
                    dot.className = 'tm-dot tm-err';
                } else if (rtWarn) {
                    dot.className = 'tm-dot tm-warn';
                } else {
                    dot.className = 'tm-dot tm-ok';
                }

                const atMins = d.access_token_age_minutes !== null ? d.access_token_age_minutes.toFixed(1) + 'm' : '?';
                const rtDays = d.refresh_token_age_days   !== null ? d.refresh_token_age_days.toFixed(2)   + 'd' : '?';

                const atEl = document.getElementById('tm-access-stat');
                const rtEl = document.getElementById('tm-refresh-stat');

                atEl.textContent = 'access ' + atMins;
                atEl.title = d.access_token_valid
                    ? `Access token is ${atMins} old. Valid for ${(30 - d.access_token_age_minutes).toFixed(1)}m more (expires at 30 min).`
                    : `Access token is ${atMins} old — EXPIRED. Run the token getter script to refresh.`;
                atEl.style.color = d.access_token_valid ? '' : '#ff5252';

                rtEl.textContent = 'refresh ' + rtDays;
                const rtRemain = d.refresh_token_age_days !== null ? (7 - d.refresh_token_age_days).toFixed(2) : '?';
                rtEl.title = d.refresh_token_valid
                    ? `Refresh token is ${rtDays} old. Valid for ${rtRemain}d more (expires at 7 days). `
                      + (d.refresh_token_age_days > 5 ? 'Re-authenticate soon!' : 'Good.')
                    : `Refresh token is ${rtDays} old — EXPIRED. Full browser re-authentication required.`;
                rtEl.style.color = !d.refresh_token_valid ? '#ff5252' : (d.refresh_token_age_days > 5 ? '#ffb300' : '');
            })
            .catch(() => {
                const dot = document.getElementById('tm-dot');
                if (dot) { dot.className = 'tm-dot tm-err'; }
                const atEl = document.getElementById('tm-access-stat');
                if (atEl) { atEl.textContent = 'unreachable'; }
            });
    }

    function forceDeleteToken() {
        if (!confirm('Delete the Schwab token file? You will need to restart the server to re-authenticate.')) return;
        fetch('/token_delete', { method: 'POST' })
            .then(r => r.json())
            .then(d => {
                if (d.success) {
                    alert('Tokens cleared from: ' + d.db + '\\n\\n' + d.message);
                    fetchTokenHealth();
                } else {
                    alert('Delete failed: ' + d.error);
                }
            })
            .catch(err => alert('Request failed: ' + err));
    }

    // Fetch on load, then every 2 minutes
    fetchTokenHealth();
    setInterval(fetchTokenHealth, 120000);
    </script>
</body>
</html>
    ''')

@app.route('/expirations/<ticker>')
def get_expirations(ticker):
    try:
        ticker = format_ticker(ticker)
        expirations = get_option_expirations(ticker)
        return jsonify(expirations)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/update', methods=['POST'])
def update():
    data = request.get_json()
    ticker = data.get('ticker')
    expiry = data.get('expiry')  # This can now be a list or single value
    
    ticker = format_ticker(ticker) 
    if not ticker or not expiry:
        return jsonify({'error': 'Missing ticker or expiry'}), 400
    
    # Handle both single expiry and multiple expiries
    if isinstance(expiry, list):
        expiry_dates = expiry
    else:
        expiry_dates = [expiry]
        
    try:
        # Setting: use volume or OI for exposure weighting
        exposure_metric = data.get('exposure_metric', "Open Interest")
        delta_adjusted = data.get('delta_adjusted', False)
        # Default calculate_in_notional to True if not present, but handle string 'true' just in case
        cin_val = data.get('calculate_in_notional', True)
        if isinstance(cin_val, str):
            calculate_in_notional = cin_val.lower() == 'true'
        else:
            calculate_in_notional = bool(cin_val)

        # Fetch options data for multiple dates
        if len(expiry_dates) == 1:
            calls, puts = fetch_options_for_date(ticker, expiry_dates[0], exposure_metric=exposure_metric, delta_adjusted=delta_adjusted, calculate_in_notional=calculate_in_notional)
        else:
            calls, puts = fetch_options_for_multiple_dates(ticker, expiry_dates, exposure_metric=exposure_metric, delta_adjusted=delta_adjusted, calculate_in_notional=calculate_in_notional)
        
        if calls.empty and puts.empty:
            return jsonify({'error': 'No options data found'})
            
        # Get current price
        S = get_current_price(ticker)
        if S is None:
            return jsonify({'error': 'Could not fetch current price'})

        # Cache options data so /update_price can use it without re-fetching
        _options_cache[ticker] = {'calls': calls.copy(), 'puts': puts.copy(), 'S': S}
        
        # Get strike range
        strike_range = float(data.get('strike_range', 0.1))
        
        # Store interval data
        store_interval_data(ticker, S, strike_range, calls, puts)
        
        # Check if this is the first access of the day for this ticker and clear centroid data if needed
        est = pytz.timezone('US/Eastern')
        current_time_est = datetime.now(est)
        
        # Check if we're in a new trading session (after 9:30 AM ET)
        if (current_time_est.hour == 9 and current_time_est.minute >= 30) or current_time_est.hour > 9:
            if current_time_est.weekday() < 5:  # Weekday
                # Check if we have any centroid data from before 9:30 AM today
                today = current_time_est.strftime('%Y-%m-%d')
                market_open_timestamp = int(current_time_est.replace(hour=9, minute=30, second=0, microsecond=0).timestamp())
                
                with closing(sqlite_connect()) as conn:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('''
                            SELECT COUNT(*) FROM centroid_data 
                            WHERE ticker = ? AND date = ? AND timestamp < ?
                        ''', (ticker, today, market_open_timestamp))
                        
                        pre_market_count = cursor.fetchone()[0]
                        if pre_market_count > 0:
                            # Clear pre-market centroid data for a fresh session
                            cursor.execute('''
                                DELETE FROM centroid_data 
                                WHERE ticker = ? AND date = ? AND timestamp < ?
                            ''', (ticker, today, market_open_timestamp))
                            conn.commit()
        
        # Store centroid data
        store_centroid_data(ticker, S, calls, puts)
        
        # Clear centroid data at the end of the day
        current_time = datetime.now()
        if current_time.hour == 23 and current_time.minute == 59:
            clear_old_data()
        
        # Get timeframe from request
        timeframe = int(data.get('timeframe', 1))

        # NOTE: price chart is handled separately by /update_price

        # Calculate volumes and other metrics
        use_range = data.get('use_range', False)  # Rename to use_range for clarity
        strike_range = float(data.get('strike_range', 0.1))
        
        if use_range:
            # Filter for options within the strike range percentage
            min_strike = S * (1 - strike_range)
            max_strike = S * (1 + strike_range)
            range_calls = calls[(calls['strike'] >= min_strike) & (calls['strike'] <= max_strike)]
            range_puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)]
            call_volume = int(range_calls['volume'].sum()) if not range_calls.empty else 0
            put_volume = int(range_puts['volume'].sum()) if not range_puts.empty else 0
        else:
            # Use all options
            call_volume = int(calls['volume'].sum()) if not calls.empty else 0
            put_volume = int(puts['volume'].sum()) if not puts.empty else 0
            
        total_volume = int(call_volume + put_volume)
        
        # Calculate volume percentages safely
        call_percentage = 0.0
        put_percentage = 0.0
        if total_volume > 0:
            call_percentage = float(round((call_volume / total_volume * 100), 1))
            put_percentage = float(round((put_volume / total_volume * 100), 1))
        
        # Get chart visibility settings
        show_calls = data.get('show_calls', True)
        show_puts = data.get('show_puts', True)
        show_net = data.get('show_net', True)
        # Handle coloring_mode with migration from old color_intensity setting
        coloring_mode = data.get('coloring_mode', None)
        if coloring_mode is None:
            # Migrate from old boolean color_intensity
            old_color_intensity = data.get('color_intensity', True)
            coloring_mode = 'Linear Intensity' if old_color_intensity else 'Solid'
        call_color = data.get('call_color', '#00ff00')
        put_color = data.get('put_color', '#ff0000')
        exposure_levels_types = data.get('levels_types', [])
        exposure_levels_count = int(data.get('levels_count', 3))
        use_heikin_ashi = data.get('use_heikin_ashi', False)
        horizontal = data.get('horizontal_bars', False)
        strike_rail_horizontal = True
        show_abs_gex = data.get('show_abs_gex', False)
        abs_gex_opacity = float(data.get('abs_gex_opacity', 0.2))
        highlight_max_level = data.get('highlight_max_level', False)
        max_level_color = data.get('max_level_color', '#800080')
        max_level_mode = data.get('max_level_mode', 'Absolute')
        top_oi_count = data.get('top_oi_count', 5)
        ov_show_calls = data.get('ov_show_calls', True)
        ov_show_puts = data.get('ov_show_puts', True)
        ov_show_net = data.get('ov_show_net', False)
        ov_show_totals = data.get('ov_show_totals', True)
 
        
        response = {}

        try:
            response['top_oi'] = compute_top_oi_strikes(calls, puts, n=top_oi_count)
        except Exception as e:
            print(f"[top_oi] build failed: {e}")
            response['top_oi'] = {'calls': [], 'puts': [], 'both': []}

        # Create charts based on visibility settings
        # NOTE: price chart is handled by /update_price (separate concurrent request)

        if data.get('show_gamma', True):
            response['gamma'] = create_exposure_chart(calls, puts, "GEX", "Gamma Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, strike_rail_horizontal, show_abs_gex_area=show_abs_gex, abs_gex_opacity=abs_gex_opacity, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_delta', True):
            response['delta'] = create_exposure_chart(calls, puts, "DEX", "Delta Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, strike_rail_horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_vanna', True):
            response['vanna'] = create_exposure_chart(calls, puts, "VEX", "Vanna Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, strike_rail_horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_charm', True):
            response['charm'] = create_exposure_chart(calls, puts, "Charm", "Charm Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, strike_rail_horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_speed', True):
            response['speed'] = create_exposure_chart(calls, puts, "Speed", "Speed Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_vomma', True):
            response['vomma'] = create_exposure_chart(calls, puts, "Vomma", "Vomma Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)

        if data.get('show_color', True):
            response['color'] = create_exposure_chart(calls, puts, "Color", "Color Exposure by Strike", S, strike_range, show_calls, show_puts, show_net, coloring_mode, call_color, put_color, expiry_dates, horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_volume', False):
            response['volume'] = create_volume_chart(call_volume, put_volume, use_range, call_color, put_color, expiry_dates)
        
        if data.get('show_options_volume', True):
            response['options_volume'] = create_options_volume_chart(
                calls, puts, S, strike_range,
                call_color, put_color, coloring_mode,
                ov_show_calls, ov_show_puts, ov_show_net,
                expiry_dates, strike_rail_horizontal,
                highlight_max_level=highlight_max_level,
                max_level_color=max_level_color,
                max_level_mode=max_level_mode,
                show_totals=ov_show_totals
            )
        
        if data.get('show_open_interest', True):
            response['open_interest'] = create_open_interest_chart(calls, puts, S, strike_range, call_color, put_color, coloring_mode, show_calls, show_puts, show_net, expiry_dates, strike_rail_horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_premium', True):
            response['premium'] = create_premium_chart(calls, puts, S, strike_range, call_color, put_color, coloring_mode, show_calls, show_puts, show_net, expiry_dates, strike_rail_horizontal, highlight_max_level=highlight_max_level, max_level_color=max_level_color, max_level_mode=max_level_mode)
        
        if data.get('show_large_trades', True):
            response['large_trades'] = create_large_trades_table(calls, puts, S, strike_range, call_color, put_color, expiry_dates, ticker=ticker)
        
        if data.get('show_centroid', False):
            response['centroid'] = create_centroid_chart(ticker, call_color, put_color, expiry_dates)

        
        # Add volume data to response
        response.update({
            'call_volume': call_volume,
            'put_volume': put_volume,
            'total_volume': total_volume,
            'call_percentage': call_percentage,
            'put_percentage': put_percentage,
            'selected_expiries': expiry_dates  # Add this to show which expiries are selected
        })
        
        # Get fresh quote data
        try:
            # Use appropriate base ticker for market tickers
            if ticker == "MARKET":
                quote_ticker = "$SPX"
            elif ticker == "MARKET2":
                quote_ticker = "SPY"
            else:
                quote_ticker = ticker

            quote_response = client.quote(quote_ticker)
            if not quote_response.ok:
                raise Exception(f"Failed to fetch quote for display: {quote_response.status_code} {quote_response.reason}")

            # --- Always Calculate Expected Move Range (same as chart logic) ---
            expected_move_range = None
            expected_move_snapshot = calculate_expected_move_snapshot(
                calls, puts, S, selected_expiries=expiry_dates
            )
            if expected_move_snapshot:
                expected_move_range = {
                    'lower': round(expected_move_snapshot['lower'], 2),
                    'upper': round(expected_move_snapshot['upper'], 2),
                    'move': round(expected_move_snapshot['move'], 2),
                }

            if quote_response.ok:
                quote_data = quote_response.json()
                ticker_data = quote_data.get(quote_ticker, {})
                quote = ticker_data.get('quote', {})

                # compute high/low diffs relative to current price
                high_price = quote.get('highPrice', S)
                low_price  = quote.get('lowPrice', S)
                high_diff = high_price - S
                low_diff  = low_price - S
                high_diff_pct = (high_diff / S * 100) if S else 0
                low_diff_pct  = (low_diff  / S * 100) if S else 0

                # add percentage of expected move boundaries if available (rounded to 2 decimals)
                if expected_move_range:
                    expected_move_range['lower_pct'] = round(((expected_move_range['lower'] - S) / S * 100), 2)
                    expected_move_range['upper_pct'] = round(((expected_move_range['upper'] - S) / S * 100), 2)

                response['price_info'] = {
                    'current_price': S,
                    'high': high_price,
                    'low': low_price,
                    'high_diff': round(high_diff, 2),
                    'high_diff_pct': round(high_diff_pct, 2),
                    'low_diff': round(low_diff, 2),
                    'low_diff_pct': round(low_diff_pct, 2),
                    'net_change': quote.get('netChange', 0),
                    'net_percent': quote.get('netPercentChange', 0),
                    'call_volume': call_volume,
                    'put_volume': put_volume,
                    'total_volume': total_volume,
                    'call_percentage': call_percentage,
                    'put_percentage': put_percentage,
                    'expected_move_range': expected_move_range
                }
        except Exception as e:
            print(f"Error fetching quote data: {e}")
            response['price_info'] = {
                'current_price': S,
                'high': S,
                'low': S,
                'high_diff': 0,
                'high_diff_pct': 0,
                'low_diff': 0,
                'low_diff_pct': 0,
                'net_change': 0,
                'net_percent': 0,
                'call_volume': call_volume,
                'put_volume': put_volume,
                'total_volume': total_volume,
                'call_percentage': call_percentage,
                'put_percentage': put_percentage,
                'expected_move_range': None
            }
        
        return jsonify(response)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/update_price', methods=['POST'])
def update_price():
    """Lightweight endpoint that returns only the price chart data.
    Runs concurrently with /update so the price chart is never blocked
    by the heavier options-chain computations.
    """
    data = request.get_json()
    ticker = data.get('ticker')
    expiry = data.get('expiry')
    ticker = format_ticker(ticker)
    if not ticker:
        return jsonify({'error': 'Missing ticker'}), 400
    try:
        if isinstance(expiry, list):
            selected_expiries = expiry
        elif expiry:
            selected_expiries = [expiry]
        else:
            selected_expiries = []
        timeframe = int(data.get('timeframe', 1))
        call_color = data.get('call_color', '#00ff00')
        put_color = data.get('put_color', '#ff0000')
        exposure_levels_types = data.get('levels_types', [])
        exposure_levels_count = int(data.get('levels_count', 3))
        strike_range = float(data.get('strike_range', 0.1))
        use_heikin_ashi = data.get('use_heikin_ashi', False)
        highlight_max_level = data.get('highlight_max_level', False)
        max_level_color = data.get('max_level_color', '#800080')
        coloring_mode = data.get('coloring_mode', 'Linear Intensity')
        top_oi_count = data.get('top_oi_count', 5)
        session_level_config = normalize_session_level_config(data.get('session_levels'))
        # Mirror /update_data semantics. compute_trader_stats has needed these
        # since Phase 2 Stage 3 (commit 6fc40bb); without them every tick raised
        # a silent NameError and trader_stats stayed None.
        delta_adjusted = data.get('delta_adjusted', False)
        cin_val = data.get('calculate_in_notional', True)
        if isinstance(cin_val, str):
            calculate_in_notional = cin_val.lower() == 'true'
        else:
            calculate_in_notional = bool(cin_val)
        gate_val = data.get('gate_alerts', True)
        if isinstance(gate_val, str):
            gate_alerts = gate_val.lower() == 'true'
        else:
            gate_alerts = bool(gate_val)

        price_data = get_price_history(ticker, timeframe=timeframe)
        session_levels = None
        session_levels_meta = None
        if session_level_config.get('enabled'):
            try:
                session_candles = price_data.get('candles') if timeframe == 1 else None
                if not session_candles:
                    session_candles = get_session_level_candles(ticker)
                session_levels = compute_session_levels(
                    session_candles,
                    timezone='US/Eastern',
                    config=session_level_config,
                )
                session_levels_meta = (session_levels or {}).get('meta')
            except Exception as e:
                print(f"[session_levels] build failed: {e}")
                session_levels = None
                session_levels_meta = None

        # Use the most recently cached options data for exposure overlays.
        # If no cache exists yet the chart renders without overlays and
        # will gain them after the first /update completes.
        cached = _options_cache.get(ticker, {})
        calls = cached.get('calls')
        puts = cached.get('puts')

        price_chart = prepare_price_chart_data(
            price_data=price_data,
            calls=calls,
            puts=puts,
            exposure_levels_types=exposure_levels_types,
            exposure_levels_count=exposure_levels_count,
            call_color=call_color,
            put_color=put_color,
            strike_range=strike_range,
            use_heikin_ashi=use_heikin_ashi,
            highlight_max_level=highlight_max_level,
            max_level_color=max_level_color,
            coloring_mode=coloring_mode,
            ticker=ticker,
            selected_expiries=selected_expiries,
        )
        # Inject timeframe so the popout candle-close timer knows the selected interval
        try:
            import json as _json
            pc_dict = _json.loads(price_chart)
            pc_dict['timeframe'] = timeframe
            price_chart = _json.dumps(pc_dict)
        except Exception:
            pass

        gex_panel = None
        key_levels = None
        top_oi = {'calls': [], 'puts': [], 'both': []}
        S_for_panel = cached.get('S')
        if calls is not None and puts is not None and S_for_panel is not None:
            try:
                # Keep historical intraday bubbles in sync with the price-chart
                # refresh loop as well as the heavier /update loop. This avoids
                # gaps when the chart stays alive but the main payload path is
                # delayed or skipped late in the session.
                store_interval_data(ticker, S_for_panel, strike_range, calls, puts)
            except Exception as e:
                print(f"[interval_data] price-path store failed: {e}")
            try:
                gex_panel = create_gex_side_panel(
                    calls, puts, S_for_panel, strike_range=strike_range,
                    call_color=call_color, put_color=put_color,
                )
            except Exception as e:
                print(f"[gex_panel] build failed: {e}")
                gex_panel = None
            try:
                key_levels = compute_key_levels(
                    calls, puts, S_for_panel,
                    selected_expiries=selected_expiries,
                    strike_range=strike_range,
                )
            except Exception as e:
                print(f"[key_levels] build failed: {e}")
                key_levels = None
            try:
                top_oi = compute_top_oi_strikes(calls, puts, n=top_oi_count)
            except Exception as e:
                print(f"[top_oi] cached build failed: {e}")
                top_oi = {'calls': [], 'puts': [], 'both': []}

        trader_stats = None
        if calls is not None and puts is not None and S_for_panel is not None:
            try:
                full_scope_id = _build_stats_scope_id(
                    strike_range,
                    selected_expiries=selected_expiries,
                    scope_label='all',
                )
                trader_stats = compute_trader_stats(
                    calls, puts, S_for_panel, strike_range=strike_range,
                    selected_expiries=selected_expiries,
                    delta_adjusted=delta_adjusted,
                    calculate_in_notional=calculate_in_notional,
                    ticker=ticker,
                    gate_strike_alerts=gate_alerts,
                    scope_id=full_scope_id,
                )
            except Exception as e:
                print(f"[trader_stats] build failed: {e}")
                trader_stats = None

        # 0DTE-filtered bundles (nearest expiration only)
        key_levels_0dte = None
        stats_0dte = None
        if calls is not None and puts is not None and S_for_panel is not None:
            try:
                nearest_exp = _nearest_expiration(calls) or _nearest_expiration(puts)
                if nearest_exp:
                    c0 = calls[calls['expiration_date'] == nearest_exp] if 'expiration_date' in calls.columns else calls
                    p0 = puts[puts['expiration_date']   == nearest_exp] if 'expiration_date' in puts.columns  else puts
                    nearest_scope_id = _build_stats_scope_id(
                        strike_range,
                        selected_expiries=[nearest_exp],
                        scope_label=f'expiry:{nearest_exp}',
                    )
                    key_levels_0dte = compute_key_levels(
                        c0, p0, S_for_panel,
                        selected_expiries=[nearest_exp],
                        strike_range=strike_range,
                    )
                    stats_0dte = compute_trader_stats(
                        c0, p0, S_for_panel,
                        strike_range=strike_range,
                        selected_expiries=[nearest_exp],
                        delta_adjusted=delta_adjusted,
                        calculate_in_notional=calculate_in_notional,
                        ticker=ticker,
                        gate_strike_alerts=gate_alerts,
                        scope_id=nearest_scope_id,
                    )
            except Exception as e:
                print(f"[0dte_bundle] build failed: {e}")

        return jsonify({
            'price': price_chart,
            'gex_panel': gex_panel,
            'key_levels': key_levels,
            'session_levels': session_levels,
            'session_levels_meta': session_levels_meta,
            'top_oi': top_oi,
            'trader_stats': trader_stats,
            'key_levels_0dte': key_levels_0dte,
            'stats_0dte': stats_0dte,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/save_settings', methods=['POST'])
def save_settings():
    try:
        settings = request.get_json()
        with open('settings.json', 'w') as f:
            json.dump(settings, f, indent=2)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/load_settings')
def load_settings():
    try:
        if os.path.exists('settings.json'):
            with open('settings.json', 'r') as f:
                settings = json.load(f)
            return jsonify(settings)
        else:
            return jsonify({'error': 'No settings file found'})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/price_stream/<path:ticker>')
def price_stream(ticker):
    """Server-Sent Events endpoint for real-time price candle/quote updates.

    The frontend connects here via EventSource; the backend pushes CHART_EQUITY
    (completed 1-min candles) and LEVELONE_EQUITIES (real-time last price) data
    from the schwabdev websocket stream.
    """
    ticker = format_ticker(ticker)
    client_queue = queue.Queue(maxsize=300)
    price_streamer.subscribe(ticker, client_queue)

    def generate():
        try:
            # Initial connection confirmation
            yield 'data: {"type":"connected"}\n\n'
            while True:
                try:
                    payload = client_queue.get(timeout=20)
                    yield f'data: {payload}\n\n'
                except queue.Empty:
                    # Heartbeat keeps the connection alive through proxies/browsers
                    yield 'data: {"type":"heartbeat"}\n\n'
        except GeneratorExit:
            pass
        finally:
            price_streamer.unsubscribe_queue(ticker, client_queue)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # disable Nginx buffering if behind a proxy
            'Connection': 'keep-alive',
        }
    )


def _get_token_db_path():
    """Return the path to the TokenManager SQLite database."""
    return os.path.expanduser(os.getenv('SCHWAB_TOKENS_DB', '~/.schwabdev/tokens.db'))


def _read_token_db(db_path):
    """Read token row from the SQLite DB. Returns dict or None."""
    if not os.path.exists(db_path):
        return None
    with closing(sqlite_connect(db_path)) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT access_token_issued, refresh_token_issued, access_token FROM schwabdev"
        ).fetchone()
    if not row:
        return None
    return {'access_token_issued': row[0], 'refresh_token_issued': row[1], 'access_token': row[2]}


@app.route('/token_health')
def token_health():
    """Return Schwab token status and API connectivity check as JSON."""
    import datetime as _dt
    import requests as _requests

    db_path = _get_token_db_path()
    result = {
        'db_path': db_path,
        'db_exists': os.path.exists(db_path),
        'access_token_age_minutes': None,
        'access_token_valid': None,
        'refresh_token_age_days': None,
        'refresh_token_valid': None,
        'api_ok': None,
        'api_message': None,
        'error': None,
    }

    try:
        row = _read_token_db(db_path)
        if row is None:
            result['error'] = 'No token row found in DB'
            return jsonify(result), 200

        now = _dt.datetime.now(_dt.timezone.utc)
        at_issued = _dt.datetime.fromisoformat(row['access_token_issued'])
        rt_issued = _dt.datetime.fromisoformat(row['refresh_token_issued'])
        if at_issued.tzinfo is None:
            at_issued = at_issued.replace(tzinfo=_dt.timezone.utc)
        if rt_issued.tzinfo is None:
            rt_issued = rt_issued.replace(tzinfo=_dt.timezone.utc)

        at_age_min = (now - at_issued).total_seconds() / 60
        rt_age_days = (now - rt_issued).total_seconds() / 86400

        result['access_token_age_minutes'] = round(at_age_min, 2)
        result['access_token_valid'] = at_age_min < 30
        result['refresh_token_age_days'] = round(rt_age_days, 4)
        result['refresh_token_valid'] = rt_age_days < 7

        # Live API test using the stored access token
        access_token = row.get('access_token', '')
        if access_token:
            try:
                resp = _requests.get(
                    'https://api.schwabapi.com/trader/v1/accounts/accountNumbers',
                    headers={'Authorization': f'Bearer {access_token}'},
                    timeout=10,
                )
                if resp.ok:
                    result['api_ok'] = True
                    result['api_message'] = f'API OK ({resp.status_code})'
                else:
                    result['api_ok'] = False
                    result['api_message'] = f'API {resp.status_code}: {resp.text[:120]}'
            except Exception as api_err:
                result['api_ok'] = False
                result['api_message'] = f'API error: {str(api_err)[:120]}'
        else:
            result['api_ok'] = False
            result['api_message'] = 'No access token stored'

    except Exception as e:
        result['error'] = str(e)
        return jsonify(result), 500

    return jsonify(result)


@app.route('/token_delete', methods=['POST'])
def token_delete():
    """Clear all rows from the token DB and null out in-memory client tokens (logout)."""
    db_path = _get_token_db_path()
    if not os.path.exists(db_path):
        return jsonify({'success': False, 'error': f'Token DB not found: {db_path}'})
    try:
        with closing(sqlite_connect(db_path)) as conn:
            conn.execute("DELETE FROM schwabdev")
            conn.commit()

        # Null out in-memory tokens on the schwabdev client so it can't make API calls
        if client is not None:
            import datetime as _dt
            client.tokens.access_token = None
            client.tokens.refresh_token = None
            client.tokens.id_token = None
            client.tokens._access_token_issued = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
            client.tokens._refresh_token_issued = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)

        return jsonify({
            'success': True,
            'db': db_path,
            'message': 'Logged out. Run your token getter script to re-authenticate.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5001, threaded=True)
