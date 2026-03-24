"""
Connection Health Monitor
Monitors MT5 connection, data quality, and system health.
"""

import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import MetaTrader5 as mt5
import pandas as pd

from config.settings import log, SYMBOLS
from core.error_handler import check_mt5_connection, validate_symbol_data


class ConnectionMonitor:
    """
    Monitors MT5 connection health, data quality, and system performance.
    """
    
    def __init__(self, check_interval: int = 30):
        self.check_interval = check_interval  # seconds
        self.monitoring = False
        self.health_stats = {
            "connection_checks": 0,
            "connection_failures": 0,
            "last_check": None,
            "last_success": None,
            "symbol_health": {},
            "data_quality_issues": [],
            "performance_metrics": {
                "avg_response_time": 0,
                "max_response_time": 0,
                "candle_fetch_errors": 0,
            }
        }
        self.monitor_thread = None
        
    def start_monitoring(self):
        """Start background health monitoring."""
        if self.monitoring:
            log.warning("Connection monitor already running")
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        log.info("Connection monitor started")
    
    def stop_monitoring(self):
        """Stop background health monitoring."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        log.info("Connection monitor stopped")
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        while self.monitoring:
            try:
                self.check_health()
                time.sleep(self.check_interval)
            except Exception as e:
                log.error(f"Error in monitor loop: {e}")
                time.sleep(self.check_interval)
    
    def check_health(self) -> Dict:
        """
        Perform comprehensive health check.
        Returns health status dictionary.
        """
        health_status = {
            "timestamp": datetime.now().isoformat(),
            "overall_health": "UNKNOWN",
            "connection": {},
            "symbols": {},
            "issues": [],
            "recommendations": []
        }
        
        self.health_stats["connection_checks"] += 1
        self.health_stats["last_check"] = datetime.now()
        
        # 1. Check MT5 connection
        connection_ok, connection_error = check_mt5_connection()
        health_status["connection"] = {
            "ok": connection_ok,
            "error": connection_error,
            "timestamp": datetime.now().isoformat()
        }
        
        if not connection_ok:
            self.health_stats["connection_failures"] += 1
            health_status["issues"].append(f"Connection failed: {connection_error}")
            health_status["overall_health"] = "UNHEALTHY"
        else:
            self.health_stats["last_success"] = datetime.now()
            
            # 2. Check symbol health
            symbol_health = {}
            for symbol in SYMBOLS:
                symbol_ok, symbol_error = validate_symbol_data(symbol)
                symbol_health[symbol] = {
                    "ok": symbol_ok,
                    "error": symbol_error,
                    "last_check": datetime.now().isoformat()
                }
                
                if not symbol_ok:
                    health_status["issues"].append(f"Symbol {symbol}: {symbol_error}")
            
            health_status["symbols"] = symbol_health
            self.health_stats["symbol_health"] = symbol_health
            
            # 3. Check data quality
            data_issues = self._check_data_quality()
            if data_issues:
                health_status["issues"].extend(data_issues)
                self.health_stats["data_quality_issues"].extend(data_issues)
            
            # 4. Check performance
            perf_issues = self._check_performance()
            if perf_issues:
                health_status["issues"].extend(perf_issues)
        
        # Determine overall health
        if not health_status["issues"]:
            health_status["overall_health"] = "HEALTHY"
        elif connection_ok:
            health_status["overall_health"] = "DEGRADED"
        else:
            health_status["overall_health"] = "UNHEALTHY"
        
        # Generate recommendations
        health_status["recommendations"] = self._generate_recommendations(health_status)
        
        # Log health status
        if health_status["overall_health"] != "HEALTHY":
            log.warning(f"Health check: {health_status['overall_health']}. Issues: {len(health_status['issues'])}")
            for issue in health_status["issues"][:3]:  # Log first 3 issues
                log.warning(f"  - {issue}")
        else:
            log.debug(f"Health check: {health_status['overall_health']}")
        
        return health_status
    
    def _check_data_quality(self) -> List[str]:
        """Check data quality for all symbols."""
        issues = []
        
        for symbol in SYMBOLS:
            try:
                # Check recent candles
                for timeframe, count in [(mt5.TIMEFRAME_M5, 10), (mt5.TIMEFRAME_H1, 5)]:
                    start_time = time.time()
                    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
                    response_time = time.time() - start_time
                    
                    # Update performance metrics
                    self.health_stats["performance_metrics"]["avg_response_time"] = (
                        self.health_stats["performance_metrics"]["avg_response_time"] * 0.9 + response_time * 0.1
                    )
                    self.health_stats["performance_metrics"]["max_response_time"] = max(
                        self.health_stats["performance_metrics"]["max_response_time"], response_time
                    )
                    
                    if rates is None:
                        issues.append(f"No {timeframe} data for {symbol}")
                        self.health_stats["performance_metrics"]["candle_fetch_errors"] += 1
                        continue
                    
                    df = pd.DataFrame(rates)
                    if df.empty:
                        issues.append(f"Empty {timeframe} DataFrame for {symbol}")
                        continue
                    
                    # Check for data anomalies
                    if len(df) < count // 2:  # Less than half expected candles
                        issues.append(f"Incomplete {timeframe} data for {symbol}: {len(df)}/{count} candles")
                    
                    # Check for stale data
                    if 'time' in df.columns and len(df) > 0:
                        latest_time = pd.to_datetime(df['time'].iloc[-1], unit='s')
                        time_diff = (datetime.now() - latest_time).total_seconds()
                        if time_diff > 300:  # 5 minutes stale
                            issues.append(f"Stale {timeframe} data for {symbol}: {time_diff:.0f}s old")
                    
                    # Check price validity
                    price_cols = ['open', 'high', 'low', 'close']
                    for col in price_cols:
                        if col in df.columns and (df[col] <= 0).any():
                            issues.append(f"Invalid {col} prices in {timeframe} for {symbol}")
            
            except Exception as e:
                issues.append(f"Data quality check failed for {symbol}: {str(e)[:100]}")
        
        return issues
    
    def _check_performance(self) -> List[str]:
        """Check system performance."""
        issues = []
        
        # Check response time
        avg_response = self.health_stats["performance_metrics"]["avg_response_time"]
        max_response = self.health_stats["performance_metrics"]["max_response_time"]
        
        if avg_response > 1.0:  # 1 second average
            issues.append(f"High average response time: {avg_response:.2f}s")
        
        if max_response > 3.0:  # 3 seconds max
            issues.append(f"Very high max response time: {max_response:.2f}s")
        
        # Check connection failure rate
        total_checks = self.health_stats["connection_checks"]
        failures = self.health_stats["connection_failures"]
        if total_checks > 10:
            failure_rate = failures / total_checks
            if failure_rate > 0.3:  # 30% failure rate
                issues.append(f"High connection failure rate: {failure_rate:.1%}")
        
        # Check for recent successful connection
        if self.health_stats["last_success"]:
            time_since_success = (datetime.now() - self.health_stats["last_success"]).total_seconds()
            if time_since_success > 300:  # 5 minutes
                issues.append(f"No successful connection for {time_since_success:.0f}s")
        
        return issues
    
    def _generate_recommendations(self, health_status: Dict) -> List[str]:
        """Generate recommendations based on health status."""
        recommendations = []
        
        if not health_status["connection"]["ok"]:
            recommendations.append("Restart MT5 terminal and check internet connection")
            recommendations.append("Verify MT5 login credentials")
        
        # Check for symbol-specific issues
        for symbol, info in health_status["symbols"].items():
            if not info["ok"]:
                recommendations.append(f"Check symbol {symbol} availability in MT5")
        
        # Check for data quality issues
        data_issues = [issue for issue in health_status["issues"] if "data" in issue.lower()]
        if data_issues:
            recommendations.append("Consider reducing trading frequency during data issues")
            recommendations.append("Check MT5 data feed connection")
        
        # Performance recommendations
        perf_metrics = self.health_stats["performance_metrics"]
        if perf_metrics["avg_response_time"] > 0.5:
            recommendations.append("Consider increasing sleep interval between cycles")
        
        if perf_metrics["candle_fetch_errors"] > 5:
            recommendations.append("Check MT5 history center for missing data")
        
        return recommendations
    
    def get_health_summary(self) -> Dict:
        """Get summary of health status."""
        return {
            "overall_health": self._get_overall_health(),
            "connection_stats": {
                "checks": self.health_stats["connection_checks"],
                "failures": self.health_stats["connection_failures"],
                "failure_rate": (
                    self.health_stats["connection_failures"] / self.health_stats["connection_checks"]
                    if self.health_stats["connection_checks"] > 0 else 0
                ),
                "last_success": (
                    self.health_stats["last_success"].isoformat() 
                    if self.health_stats["last_success"] else None
                ),
                "last_check": (
                    self.health_stats["last_check"].isoformat() 
                    if self.health_stats["last_check"] else None
                ),
            },
            "performance": self.health_stats["performance_metrics"],
            "symbol_health": self.health_stats["symbol_health"],
            "recent_issues": self.health_stats["data_quality_issues"][-5:] if self.health_stats["data_quality_issues"] else []
        }
    
    def _get_overall_health(self) -> str:
        """Calculate overall health status."""
        if not self.health_stats["last_success"]:
            return "UNKNOWN"
        
        time_since_success = (datetime.now() - self.health_stats["last_success"]).total_seconds()
        
        if time_since_success > 300:  # 5 minutes
            return "UNHEALTHY"
        elif time_since_success > 60:  # 1 minute
            return "DEGRADED"
        else:
            return "HEALTHY"
    
    def is_healthy_for_trading(self) -> Tuple[bool, str]:
        """
        Check if system is healthy enough for trading.
        Returns (is_healthy, reason)
        """
        health_summary = self.get_health_summary()
        
        if health_summary["overall_health"] == "UNHEALTHY":
            return False, "System is unhealthy"
        
        # Check connection failure rate
        failure_rate = health_summary["connection_stats"]["failure_rate"]
        if failure_rate > 0.5:  # 50% failure rate
            return False, f"High connection failure rate: {failure_rate:.1%}"
        
        # Check response time
        avg_response = health_summary["performance"]["avg_response_time"]
        if avg_response > 2.0:  # 2 seconds average
            return False, f"High response time: {avg_response:.2f}s"
        
        # Check symbol health
        unhealthy_symbols = [
            symbol for symbol, health in health_summary["symbol_health"].items()
            if not health.get("ok", False)
        ]
        if unhealthy_symbols:
            return False, f"Unhealthy symbols: {', '.join(unhealthy_symbols[:3])}"
        
        return True, "System is healthy"


# Global connection monitor instance
connection_monitor = ConnectionMonitor(check_interval=30)