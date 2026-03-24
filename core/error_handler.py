"""
Enhanced Error Handling & Logging Module
Provides comprehensive error tracking, retry logic, and health monitoring.
"""

import logging
import time
import traceback
from typing import Callable, Any, Optional, TypeVar, Tuple
from functools import wraps
import MetaTrader5 as mt5

from config.settings import log

T = TypeVar('T')


class ErrorHandler:
    """
    Centralized error handling with retry logic, circuit breaking, and health monitoring.
    """
    
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.error_counts = {}
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 60  # seconds
        
    def with_retry(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator for automatic retry with exponential backoff.
        """
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    error_type = type(e).__name__
                    self._record_error(error_type)
                    
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (2 ** (attempt - 1))  # Exponential backoff
                        log.warning(
                            f"Retry {attempt}/{self.max_retries} for {func.__name__}: "
                            f"{error_type}: {str(e)[:100]} | Waiting {delay:.1f}s"
                        )
                        time.sleep(delay)
                    else:
                        log.error(
                            f"Failed after {self.max_retries} retries for {func.__name__}: "
                            f"{error_type}: {str(e)[:200]}"
                        )
                        if self._is_circuit_broken(error_type):
                            log.critical(f"Circuit broken for {error_type}, skipping execution")
                            raise CircuitBreakerError(f"Circuit broken for {error_type}") from e
            
            raise last_error
        return wrapper
    
    def _record_error(self, error_type: str):
        """Record error occurrence for circuit breaking."""
        if error_type not in self.error_counts:
            self.error_counts[error_type] = {"count": 0, "last_time": time.time()}
        
        self.error_counts[error_type]["count"] += 1
        self.error_counts[error_type]["last_time"] = time.time()
        
        # Reset count if enough time has passed
        if time.time() - self.error_counts[error_type]["last_time"] > 300:  # 5 minutes
            self.error_counts[error_type]["count"] = 1
    
    def _is_circuit_broken(self, error_type: str) -> bool:
        """Check if circuit breaker should trip for this error type."""
        if error_type not in self.error_counts:
            return False
        
        error_info = self.error_counts[error_type]
        if error_info["count"] >= self.circuit_breaker_threshold:
            # Check if we're still in timeout period
            if time.time() - error_info["last_time"] < self.circuit_breaker_timeout:
                return True
            else:
                # Reset after timeout
                error_info["count"] = 0
                return False
        return False
    
    def safe_execute(self, func: Callable[..., T], default: T = None, 
                    log_error: bool = True) -> Tuple[bool, Optional[T], Optional[str]]:
        """
        Safely execute a function with comprehensive error handling.
        Returns (success, result, error_message)
        """
        try:
            result = func()
            return True, result, None
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            if log_error:
                log.error(f"Error in {func.__name__ if hasattr(func, '__name__') else 'unknown'}: {error_msg}")
                log.debug(f"Traceback: {traceback.format_exc()}")
            return False, default, error_msg


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


# Global error handler instance
error_handler = ErrorHandler(max_retries=3, retry_delay=1.0)


# MT5-specific error handling utilities
def check_mt5_connection() -> Tuple[bool, Optional[str]]:
    """
    Comprehensive MT5 connection health check.
    Returns (is_healthy, error_message)
    """
    try:
        # Check if MT5 is initialized
        if not mt5.initialize():
            return False, "MT5 not initialized"
        
        # Check account info
        account = mt5.account_info()
        if not account:
            return False, "No account info available"
        
        # Check terminal connection
        terminal_info = mt5.terminal_info()
        if not terminal_info:
            return False, "No terminal info available"
        
        # Check if terminal is connected
        if not getattr(terminal_info, "connected", False):
            return False, "Terminal not connected to server"
        
        # Check if trading is allowed
        if not getattr(terminal_info, "trade_allowed", False):
            return False, "Trading not allowed"
        
        return True, None
        
    except Exception as e:
        return False, f"MT5 connection check failed: {str(e)}"


def validate_symbol_data(symbol: str) -> Tuple[bool, Optional[str]]:
    """
    Validate symbol data availability.
    Returns (is_valid, error_message)
    """
    try:
        # Check symbol info
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return False, f"Symbol {symbol} not found"
        
        # Check if symbol is visible/selectable
        if not getattr(sym_info, "visible", False):
            mt5.symbol_select(symbol, True)
            # Re-check after selection attempt
            sym_info = mt5.symbol_info(symbol)
            if not sym_info:
                return False, f"Symbol {symbol} cannot be selected"
        
        # Check tick data
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False, f"No tick data for {symbol}"
        
        if tick.bid <= 0 or tick.ask <= 0:
            return False, f"Invalid prices for {symbol}: bid={tick.bid}, ask={tick.ask}"
        
        # Check spread
        spread = (tick.ask - tick.bid) / sym_info.point if sym_info.point > 0 else 9999
        if spread > 100:  # Arbitrary high spread threshold
            return False, f"Spread too high for {symbol}: {spread} points"
        
        return True, None
        
    except Exception as e:
        return False, f"Symbol validation failed for {symbol}: {str(e)}"


def log_data_quality_metrics(symbol: str, df, timeframe: str):
    """
    Log data quality metrics for analysis.
    """
    if df.empty:
        log.warning(f"[{symbol}] Empty DataFrame for {timeframe}")
        return
    
    # Check for data issues
    issues = []
    
    # Check for NaN values
    if df.isnull().any().any():
        issues.append(f"NaN values in columns: {df.columns[df.isnull().any()].tolist()}")
    
    # Check for zero or negative prices
    price_cols = ['open', 'high', 'low', 'close']
    for col in price_cols:
        if (df[col] <= 0).any():
            issues.append(f"Non-positive {col} values")
    
    # Check OHLC consistency
    if not ((df['low'] <= df['open']) & (df['low'] <= df['close']) & 
            (df['high'] >= df['open']) & (df['high'] >= df['close'])).all():
        issues.append("OHLC inconsistency")
    
    # Check volume
    if 'tick_volume' in df.columns and (df['tick_volume'] < 0).any():
        issues.append("Negative volume")
    
    if issues:
        log.warning(f"[{symbol}] Data quality issues for {timeframe}: {', '.join(issues)}")
    else:
        log.debug(f"[{symbol}] Good data quality for {timeframe}: {len(df)} candles")


# Decorators for common error handling patterns
def log_errors(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator to log errors with context."""
    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            func_name = func.__name__
            args_str = str(args)[:100] + "..." if len(str(args)) > 100 else str(args)
            log.error(
                f"Error in {func_name} with args {args_str}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            log.debug(f"Full traceback: {traceback.format_exc()}")
            raise
    return wrapper


def return_default_on_error(default: Any):
    """Decorator to return default value on error."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log.warning(f"{func.__name__} failed, returning default: {str(e)[:100]}")
                return default
        return wrapper
    return decorator