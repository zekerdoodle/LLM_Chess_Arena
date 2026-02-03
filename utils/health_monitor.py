"""
Health Monitoring System for Theo

Provides system health checks and component status monitoring.
"""
import time
import logging
from typing import Dict, Any, Optional
from utils.circuit_breaker import get_system_health

logger = logging.getLogger(__name__)


class SystemHealthMonitor:
    """Monitor system health and component status"""
    
    def __init__(self):
        self.start_time = time.time()
        self.health_checks = {}
        self.last_health_check = 0
        
    def get_comprehensive_health(self) -> Dict[str, Any]:
        """Get comprehensive system health report"""
        current_time = time.time()
        uptime = current_time - self.start_time
        
        # Get circuit breaker status
        circuit_health = get_system_health()
        
        # Calculate overall health score
        if circuit_health:
            healthy_components = sum(1 for state in circuit_health.values() 
                                   if state.get('state') == 'CLOSED')
            total_components = len(circuit_health)
            health_score = healthy_components / total_components if total_components > 0 else 1.0
        else:
            health_score = 1.0  # No components monitored yet
        
        # Determine overall status
        if health_score >= 0.8:
            overall_status = "HEALTHY"
        elif health_score >= 0.5:
            overall_status = "DEGRADED"
        else:
            overall_status = "UNHEALTHY"
        
        return {
            "timestamp": current_time,
            "uptime_seconds": uptime,
            "uptime_formatted": self._format_uptime(uptime),
            "overall_status": overall_status,
            "health_score": health_score,
            "circuit_breakers": circuit_health,
            "component_summary": self._get_component_summary(circuit_health)
        }
    
    def _format_uptime(self, uptime_seconds: float) -> str:
        """Format uptime in human readable format"""
        if uptime_seconds < 60:
            return f"{uptime_seconds:.1f} seconds"
        elif uptime_seconds < 3600:
            minutes = uptime_seconds / 60
            return f"{minutes:.1f} minutes"
        else:
            hours = uptime_seconds / 3600
            return f"{hours:.1f} hours"
    
    def _get_component_summary(self, circuit_health: Dict[str, Any]) -> Dict[str, int]:
        """Get summary of component states"""
        summary = {"healthy": 0, "degraded": 0, "unhealthy": 0}
        
        for component_state in circuit_health.values():
            state = component_state.get('state', 'CLOSED')
            if state == 'CLOSED':
                summary["healthy"] += 1
            elif state == 'HALF_OPEN':
                summary["degraded"] += 1
            elif state == 'OPEN':
                summary["unhealthy"] += 1
        
        return summary
    
    def check_component_health(self, component_name: str) -> Dict[str, Any]:
        """Check health of specific component"""
        circuit_health = get_system_health()
        
        if component_name in circuit_health:
            component_state = circuit_health[component_name]
            
            # Determine health status
            state = component_state.get('state', 'CLOSED')
            if state == 'CLOSED':
                status = "HEALTHY"
            elif state == 'HALF_OPEN':
                status = "DEGRADED" 
            else:
                status = "UNHEALTHY"
            
            return {
                "component": component_name,
                "status": status,
                "details": component_state
            }
        else:
            return {
                "component": component_name,
                "status": "UNKNOWN",
                "details": {"error": "Component not monitored"}
            }
    
    def log_health_summary(self, force: bool = False):
        """Log health summary (rate limited)"""
        current_time = time.time()
        
        # Rate limit health logging (every 5 minutes)
        if not force and current_time - self.last_health_check < 300:
            return
        
        self.last_health_check = current_time
        
        try:
            health = self.get_comprehensive_health()
            
            logger.info(
                f"System Health: {health['overall_status']} "
                f"(Score: {health['health_score']:.2f}, "
                f"Uptime: {health['uptime_formatted']})"
            )
            
            if health['circuit_breakers']:
                summary = health['component_summary']
                logger.info(
                    f"Components: {summary['healthy']} healthy, "
                    f"{summary['degraded']} degraded, "
                    f"{summary['unhealthy']} unhealthy"
                )
            
            # Log degraded/unhealthy components
            for name, state in health['circuit_breakers'].items():
                if state.get('state') != 'CLOSED':
                    logger.warning(
                        f"Component {name} is {state.get('state')}: "
                        f"{state.get('failure_count', 0)} failures"
                    )
        
        except Exception as e:
            logger.error(f"Error logging health summary: {e}")


# Global health monitor instance
_health_monitor = SystemHealthMonitor()


def get_health_monitor() -> SystemHealthMonitor:
    """Get global health monitor instance"""
    return _health_monitor


def log_system_health(force: bool = False):
    """Log system health summary"""
    _health_monitor.log_health_summary(force)


def get_system_status() -> Dict[str, Any]:
    """Get current system status"""
    return _health_monitor.get_comprehensive_health()