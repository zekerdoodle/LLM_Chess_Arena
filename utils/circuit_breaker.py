"""
Circuit Breaker Pattern Implementation for Theo

Provides failure isolation and automatic recovery for system components.
"""
import time
import logging
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Failures detected, blocking calls
    HALF_OPEN = "HALF_OPEN"  # Testing if service recovered


class ComponentCircuitBreaker:
    """Circuit breaker for individual components"""
    
    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: int = 300):
        """
        Initialize circuit breaker.
        
        Args:
            name: Component name for logging
            failure_threshold: Number of failures before opening circuit
            reset_timeout: Seconds before attempting recovery
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
        
        logger.debug(f"Circuit breaker initialized for {name}")
    
    def is_open(self) -> bool:
        """Check if circuit is open (blocking calls)"""
        if self.state == CircuitState.OPEN:
            # Check if reset timeout has elapsed
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(f"Circuit breaker for {self.name} entering HALF_OPEN state")
                return False
            return True
        return False
    
    def record_success(self):
        """Record successful operation"""
        self.success_count += 1
        
        if self.state == CircuitState.HALF_OPEN:
            # Reset circuit after successful call in HALF_OPEN
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info(f"Circuit breaker for {self.name} reset to CLOSED")
        elif self.state == CircuitState.CLOSED and self.failure_count > 0:
            # Reduce failure count on success
            self.failure_count = max(0, self.failure_count - 1)
    
    def record_failure(self):
        """Record failed operation"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                self.state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker for {self.name} OPENED after {self.failure_count} failures"
                )
        
        logger.debug(f"Circuit breaker for {self.name}: {self.failure_count} failures")
    
    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "is_open": self.is_open(),
            "last_failure_time": self.last_failure_time
        }


class SystemCircuitBreakers:
    """Global circuit breaker manager"""
    
    def __init__(self):
        self.breakers: Dict[str, ComponentCircuitBreaker] = {}
    
    def get_breaker(self, name: str, **kwargs) -> ComponentCircuitBreaker:
        """Get or create circuit breaker for component"""
        if name not in self.breakers:
            self.breakers[name] = ComponentCircuitBreaker(name, **kwargs)
        return self.breakers[name]
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get health status of all components"""
        return {
            name: breaker.get_state() 
            for name, breaker in self.breakers.items()
        }


# Global instance
_system_breakers = SystemCircuitBreakers()


def get_circuit_breaker(name: str, **kwargs) -> ComponentCircuitBreaker:
    """Get circuit breaker for component"""
    return _system_breakers.get_breaker(name, **kwargs)


def get_system_health() -> Dict[str, Any]:
    """Get system-wide health status"""
    return _system_breakers.get_system_health()


def reset_circuit_breakers():
    """Reset all registered circuit breakers (testing utility)."""
    try:
        _system_breakers.breakers.clear()
    except Exception:
        pass
